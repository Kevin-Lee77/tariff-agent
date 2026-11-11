"""Bounded read-only planner/tool subgraph. Legal candidate scope is never model-controlled."""

from typing import Literal, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from pydantic import Field, ValidationError

from .agent_skills import catalog, load_skill
from .agent_telemetry import span
from .clients import ModelError
from .domain import Strict
from .history import History
from .runtime_context import digest


class ExploreAction(Strict):
    skill_id: Literal["exclusion_review", "processing_comparison", "clarification"]
    action: Literal["lookup_notes", "search_cases", "compare_candidates", "ask_clarification", "finish"]
    codes: list[str] = Field(default_factory=list, max_length=3)
    query: str = Field(default="", max_length=240)
    attribute: Literal["", "material", "function", "use", "form", "processing"] = ""
    question: str = Field(default="", max_length=240)
    reason: str = Field(default="", max_length=300)


class ExplorationState(TypedDict, total=False):
    rounds: int
    done: bool
    action: dict
    observations: list[dict]
    evidence: list[dict]
    references: list[dict]
    questions: list[str]
    seen: list[str]
    stop_reason: str


class ScopedExplorer:
    def __init__(self, settings, knowledge, models, call_model, record):
        self.s, self.k, self.models, self.call_model, self.record = (
            settings,
            knowledge,
            models,
            call_model,
            record,
        )

    def run(self, case, attributes, allowed, ranking):
        self.case, self.attrs, self.allowed = case, attributes, set(allowed)
        max_rounds = min(2, max(0, self.s.agentic_retrieval_max_rounds))

        def plan(state):
            if state["rounds"] >= max_rounds:
                return {"done": True, "stop_reason": "round_budget_exhausted"}
            payload = {
                "original_description": case["description"],
                "submitted_elements": case.get("elements", []),
                "attributes": attributes,
                "allowed_codes": allowed,
                "candidates": self.k.brief(allowed),
                "issues": ranking.conflicts,
                "skills": catalog(),
                "tool_observations": state["observations"],
                "remaining_rounds": max_rounds - state["rounds"],
            }
            try:
                raw = self.call_model("l2_exploration_plan", payload, ExploreAction, teacher=True)
                action = ExploreAction.model_validate(raw.model_dump())
                skill = load_skill(action.skill_id)
                if action.action not in skill["tools"] or not set(action.codes) <= self.allowed:
                    raise ValueError("Action outside skill/candidate scope")
                if action.action not in ("finish", "ask_clarification") and not action.codes:
                    raise ValueError("Scoped read tools require candidate codes")
            except (ModelError, ValidationError, ValueError):
                self.record("agentic_plan_rejected", {"reason": "invalid_or_unavailable_scoped_action"})
                return {"done": True, "stop_reason": "invalid_plan"}
            signature = digest([action.action, action.codes, action.query, action.attribute])
            if signature in state["seen"]:
                return {"done": True, "stop_reason": "repeated_action"}
            self.record(
                "skill_loaded", {"id": skill["id"], "version": skill["version"], "action": action.action}
            )
            # Only the selected playbook is loaded for tool execution / subsequent judgement.
            return {
                "action": action.model_dump(),
                "seen": state["seen"] + [signature],
                "done": action.action == "finish",
                "stop_reason": "model_finished" if action.action == "finish" else "",
            }

        def act(state):
            action = ExploreAction.model_validate(state["action"])
            with span("tool.exploration." + action.action, {"agent.tool": action.action}):
                output = self.dispatch(action)
            signature = digest(output)
            if any(row.get("content_hash") == signature for row in state["observations"]):
                return {"done": True, "stop_reason": "no_new_evidence"}
            observation = {
                "action": action.action,
                "skill": load_skill(action.skill_id),
                "content_hash": signature,
                "result": output,
            }
            self.record(
                "agentic_action",
                {
                    "round": state["rounds"] + 1,
                    "action": action.action,
                    "skill_id": action.skill_id,
                    "codes": action.codes,
                    "output": output,
                },
            )
            return {
                "rounds": state["rounds"] + 1,
                "observations": state["observations"] + [observation],
                "evidence": state["evidence"] + output.get("evidence", []),
                "references": state["references"] + output.get("references", []),
                "questions": output.get("questions", []),
                "done": bool(output.get("questions")) or output["status"] != "ok",
                "stop_reason": "clarification_required"
                if output.get("questions")
                else output["status"]
                if output["status"] != "ok"
                else "",
            }

        graph = StateGraph(ExplorationState)
        graph.add_node("plan", plan)
        graph.add_node("read_tool", act)
        graph.add_edge(START, "plan")
        graph.add_conditional_edges("plan", lambda state: END if state["done"] else "read_tool")
        graph.add_conditional_edges("read_tool", lambda state: END if state["done"] else "plan")
        return graph.compile().invoke(
            {
                "rounds": 0,
                "done": False,
                "observations": [],
                "evidence": [],
                "references": [],
                "questions": [],
                "seen": [],
            },
            config={"recursion_limit": 8},
        )

    def dispatch(self, action):
        if action.action == "ask_clarification":
            names = {
                "material": "材质",
                "function": "功能",
                "use": "用途",
                "form": "形态",
                "processing": "加工方式",
            }
            name = names.get(action.attribute)
            submitted = {row["name"] for row in self.case.get("elements", [])}
            if not name or self.attrs.get(action.attribute) or name in submitted or len(action.question) < 5:
                return {"status": "already_known_or_invalid_question"}
            return {"status": "ok", "questions": [action.question]}
        if action.action in ("lookup_notes", "compare_candidates"):
            rows = self.k.brief(action.codes, notes=True, query=action.query or self.case["description"])
            evidence = {note["id"]: note for row in rows for note in row.pop("notes", [])}
            return {"status": "ok", "candidates": rows, "evidence": list(evidence.values())}
        if action.action == "search_cases":
            if not self.s.history_enabled:
                return {"status": "dependency_not_configured"}
            index = History(self.s)
            try:
                rows = index.search(
                    action.query or self.case["description"],
                    self.models.embed_query
                    if self.s.dense_enabled and self.s.history_dense_enabled
                    else None,
                    limit=10,
                    exclude_description=self.case["description"],
                )
                refs = [r for r in rows if r["reference_code"][:4] in action.codes][:3]
                return {"status": "ok", "references": refs, "authority": "historical_reference_not_law"}
            except httpx.HTTPError:
                return {"status": "dependency_unavailable"}
            finally:
                index.close()
        raise ValueError("Unallowlisted exploration tool")
