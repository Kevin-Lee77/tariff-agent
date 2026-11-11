import hashlib
import json
import time
from collections import Counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .agent_memory import AgentMemory
from .agent_telemetry import span, task_trace, trace_id
from .agentic_retrieval import ScopedExplorer
from .clients import ModelError
from .context_engineering import ContextPacker
from .domain import Attributes, CaseInput, Ranking, Report
from .durable import checkpoint_store
from .governance import check_standard
from .governance_tools import GovernanceTools
from .knowledge import EvidenceError
from .model_transport import gateway_health
from .retrieval import recall
from .runtime_context import RunContextChanged, digest, input_revision, versions
from .store import Conflict


class State(TypedDict, total=False):
    case: dict
    attributes: dict
    retrieved: list[str]
    shortlist: list[str]
    ranked: list[str]
    resolved: list[str]
    citations: list[dict]
    conflicts: list[str]
    excluded: list[dict]
    votes: dict
    status: str
    report: dict
    standard_check: dict
    risk_check: dict
    normalization: dict
    historical_cases: list[dict]
    decisions: list[dict]
    expert_memories: list[dict]
    evidence_manifest: dict
    exploration: dict
    extra_evidence: list[dict]
    tool_observations: list[dict]


class Workflow:
    def __init__(self, knowledge, models, store, settings, tools=None):
        self.k, self.models, self.store, self.s = knowledge, models, store, settings
        self.tools = tools or GovernanceTools(settings)
        self.memory = AgentMemory(store, settings)
        self.context = ContextPacker(settings)

    def run(self, job, scope="end_to_end"):
        if scope not in ("end_to_end", "classification_component", "governance_component"):
            raise ValueError("Unknown evaluation scope")
        previous_guard = getattr(self.models, "runtime_guard", None)
        previous_context = getattr(self.models, "runtime_context", {})
        try:
            with task_trace(self.store, self.s, job["id"], input_revision(job["payload"])[:32]) as session:
                result = self._run(job, scope, session)
                session.complete(result)
                return result
        except Exception:
            if hasattr(self, "_run_id") and self.store.owns(job["id"], job["token"]):
                self.memory.failed(job, self._run_id, getattr(self, "_active_stage", "unknown"))
            raise
        finally:
            self.models.runtime_guard = previous_guard
            self.models.runtime_context = previous_context

    def _run(self, job, scope, session):
        key = job["id"]
        self._job_id = key
        if not self.store.owns(key, job["token"]):
            raise Conflict("Task cancelled or lease lost")
        manifest = versions(self.s, self.store)
        self._memory_enabled = (
            self.s.expert_memory_enabled and job["payload"].get("governance_profile", "standard") != "sandbox"
        )
        memory_snapshot = (
            self.memory.active(manifest["domain_version"], metadata_only=True) if self._memory_enabled else []
        )
        self._pinned_memories = {r["id"] for r in memory_snapshot}
        manifest["memory_version"] = (
            digest([(r["id"], r["expires"]) for r in memory_snapshot]) if self._memory_enabled else "disabled"
        )
        initial = {"case": job["payload"]}
        prefetched = {}
        # Recheck raw-input risk on EVERY attempt, including native checkpoint recovery.
        if scope != "classification_component":
            with span("preflight.risk"):
                prefetched["risk_precheck"] = self.risk_precheck(initial)
            if prefetched["risk_precheck"]["status"] == "running":
                with span("preflight.language"):
                    prefetched["normalize"] = self.normalize(initial | prefetched["risk_precheck"])
        else:
            initial.update(
                risk_check={"decision": "not_evaluated_component"},
                normalization={
                    "normalized_text": job["payload"]["description"],
                    "status": "not_evaluated_component",
                    "dictionary_version": "component_raw_input",
                },
                conflicts=["component_evaluation_no_governance"],
            )
        cleaned_preflight = {
            k: {field: value for field, value in out.items() if field not in ("risk_check", "normalization")}
            | {
                field: {a: b for a, b in out[field].items() if a != "report_id"}
                for field in ("risk_check", "normalization")
                if field in out
            }
            for k, out in prefetched.items()
        }
        run_id, existed = self.memory.begin(job, manifest, scope, cleaned_preflight)
        session.bind_run(run_id)
        self._run_id, self._manifest = run_id, manifest

        def guard():
            if not self.store.owns(key, job["token"]):
                raise Conflict("Task cancelled or worker lease lost")
            current = versions(self.s, self.store)
            if any(current[field] != manifest[field] for field in ("domain_version", "execution_version")):
                raise RunContextChanged("Pinned policy or execution version changed")
            if self._memory_enabled and not self._pinned_memories <= {
                r["id"] for r in self.memory.active(manifest["domain_version"], metadata_only=True)
            }:
                raise RunContextChanged(
                    "A pinned expert memory was revoked or expired; retry with a new version"
                )

        self.models.runtime_guard = guard
        self.models.runtime_context = {"run_id": run_id, "domain_version": manifest["domain_version"]}
        previous = self.store.events(key)
        policy = {}
        for path in (
            self.s.rules,
            self.s.risk_rules,
            self.s.language_rules,
            self.s.word2vec,
            self.s.history_directory / "active.json",
            self.s.history_directory / "rules" / "active.json",
        ):
            if path.exists():
                info = path.stat()
                policy[str(path)] = [info.st_size, info.st_mtime_ns]

        def node(name, fn):
            def invoke(state):
                self._active_stage = name
                guard()
                signature_state = dict(state)
                for field in ("risk_check", "normalization"):
                    if field in signature_state:
                        signature_state[field] = {
                            k: v for k, v in signature_state[field].items() if k != "report_id"
                        }
                signature = hashlib.sha256(
                    json.dumps(
                        {
                            "input": signature_state,
                            "corpus": self.k.fingerprint,
                            "model": self.s.deepseek_model,
                            "dense": self.s.dense_enabled,
                            "stage": name,
                            "embedding": self.s.embedding_model,
                            "rerank": self.s.rerank_enabled,
                            "history": self.s.history_enabled,
                            "qdrant_catalog": self.s.qdrant_catalog_enabled,
                            "small_model": [self.s.small_model_enabled, self.s.small_model_name],
                            "history_dense": self.s.history_dense_enabled,
                            "policies": policy,
                            "terms": self.store.terms(),
                            "workflow_version": 4,
                            "scope": scope,
                            "runtime_versions": manifest,
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode()
                ).hexdigest()
                cached = next(
                    (
                        e["data"]["output"]
                        for e in reversed(previous)
                        if e["stage"] == name
                        and e["data"].get("signature") == signature
                        and e["data"].get("run_id") == run_id
                    ),
                    None,
                )
                if cached is not None and name not in ("risk_precheck", "normalize"):
                    return cached
                started = time.monotonic()
                with span("node." + name, {"agent.stage": name, "run.id": run_id}):
                    output = prefetched[name] if name in prefetched else fn(state)
                    guard()
                self.store.event(
                    key,
                    name,
                    {
                        "signature": signature,
                        "run_id": run_id,
                        "trace_id": trace_id(),
                        "output": output,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    },
                )
                self.memory.record(job, run_id, name, state | output, session.trace_id, manifest)
                return output

            return invoke

        graph = StateGraph(State)
        for name, fn in [
            ("risk_precheck", self.risk_precheck),
            ("normalize", self.normalize),
            ("attributes", self.attributes),
            ("retrieve", self.retrieve),
            ("l1", self.l1),
            ("l2", self.l2),
            ("subheading", self.subheading),
            ("verify", self.verify),
            ("teacher", self.teacher),
        ]:
            graph.add_node(name, node(name, fn))
        graph.add_edge(START, "attributes" if scope == "classification_component" else "risk_precheck")
        graph.add_conditional_edges(
            "risk_precheck", lambda x: "normalize" if x.get("status") == "running" else END
        )
        graph.add_conditional_edges(
            "normalize",
            lambda x: (
                "attributes" if x.get("status") == "running" and scope != "governance_component" else END
            ),
        )
        graph.add_conditional_edges(
            "attributes", lambda x: END if x.get("status") == "awaiting_information" else "retrieve"
        )
        graph.add_edge("retrieve", "l1")
        graph.add_edge("l1", "l2")
        graph.add_conditional_edges(
            "l2",
            lambda x: (
                END
                if x.get("status") == "awaiting_information"
                else "subheading"
                if x["ranked"]
                else "teacher"
            ),
        )
        graph.add_edge("subheading", "verify")
        graph.add_edge("verify", "teacher")
        graph.add_edge("teacher", END)
        with checkpoint_store(self.store, self.s, job) as saver:
            compiled = graph.compile(checkpointer=saver)
            config = {"recursion_limit": 16, "configurable": {"thread_id": run_id}, "durability": "sync"}
            snapshot = compiled.get_state(config) if saver else None
            if existed:
                self.store.event(
                    key,
                    "checkpoint_resume",
                    {
                        "run_id": run_id,
                        "trace_id": session.trace_id,
                        "next_nodes": list(snapshot.next) if snapshot else [],
                        "preflight_rechecked": scope != "classification_component",
                    },
                )
            if snapshot and snapshot.values:
                result = (
                    compiled.invoke(None, config=config, durability="sync")
                    if snapshot.next
                    else dict(snapshot.values)
                )
            else:
                result = compiled.invoke(initial, config=config, durability="sync")
            guard()
        if scope == "governance_component" and result.get("status") == "running":
            result["status"] = "completed"
        result["evidence_manifest"] = {
            c["source_id"]: {
                "content_hash": digest(self.k.sources.get(c["source_id"], "")),
                "corpus_version": self.k.fingerprint,
            }
            for c in result.get("citations", [])
        }
        result["provenance"] = manifest | {
            "run_id": run_id,
            "trace_id": session.trace_id,
            "case_id": job["id"],
            "input_revision": input_revision(job["payload"]),
            "scope": scope,
            "checkpoint_backend": self.store.engine.dialect.name,
        }
        if result.get("status") == "awaiting_review":
            from .human_review import prepare_review

            review = prepare_review(self.store, self.s, job, result)
            if review:
                result["human_review"] = review
                self.store.event(
                    key, "human_review_paused", {"run_id": run_id, "interrupt_id": review["interrupt_id"]}
                )
        self.memory.record(job, run_id, "settled", result, session.trace_id, manifest)
        return result

    def call_model(self, stage, payload, schema, teacher=False):
        packed, metrics = self.context.pack(stage, payload, schema)
        if hasattr(self, "_run_id"):
            data = metrics | {"stage": stage, "run_id": self._run_id}
            if metrics["omitted_records"]:
                self.store.event(
                    self._job_id,
                    "context_artifact",
                    {
                        "artifact_id": metrics["artifact_id"],
                        "stage": stage,
                        "run_id": self._run_id,
                        "payload": payload,
                    },
                )
            self.store.event(self._job_id, "context_prepared", data)
        return self.models.structured(stage, packed, schema, teacher=teacher)

    def case_input(self, state):
        return CaseInput.model_validate({k: v for k, v in state["case"].items() if not k.startswith("_")})

    def risk_precheck(self, state):
        risk = self.tools.tax_source_risk_check(self.case_input(state))
        decision = risk.get("decision")
        status = {
            "clear": "running",
            "blocked": "risk_blocked",
            "information_required": "awaiting_information",
        }.get(decision, "risk_review_required")
        return {
            "risk_check": risk,
            "status": status,
            "report": {
                "summary": risk.get("reason", "风险校验未通过"),
                "questions": [risk["reason"]] if decision == "information_required" else [],
            },
        }

    def normalize(self, state):
        result = self.tools.tax_language_normalize(self.case_input(state), self.store.terms())
        ready = result.get("status") == "processed"
        if ready and self.s.strict_readiness:
            import httpx

            model_ready = bool(self.s.deepseek_api_key.get_secret_value())
            if state["case"].get("retrieval_profile", "hybrid") == "hybrid":
                try:
                    model_ready = model_ready and gateway_health(self.s).get("status") == "ready"
                except (httpx.HTTPError, ValueError):
                    model_ready = False
            if self.s.history_enabled or self.s.qdrant_catalog_enabled:
                from .history import retrieval_ready

                model_ready = model_ready and retrieval_ready(
                    self.s, dense=state["case"].get("retrieval_profile", "hybrid") == "hybrid"
                )
            if not model_ready:
                return {
                    "normalization": result,
                    "status": "awaiting_models",
                    "report": {"summary": "前置校验完成，推理依赖未就绪；请服务恢复后重新提交"},
                }
        return {
            "normalization": result,
            "status": "running" if ready else "language_review_required",
            "report": {
                "summary": "税智语已处理，继续归类"
                if ready
                else "税智语词典不可用或存在歧义，请核查后重新提交"
            },
        }

    def attributes(self, state):
        # Original text is retained; an approved alias map supplies hints, never blind replacement.
        published = self.store.terms()
        hints = {
            main: aliases
            for main, aliases in published.get("groups", {}).items()
            if any(w in state["case"]["description"] for w in [main] + aliases)
        }
        payload = state["case"] | {
            "description": state["normalization"]["normalized_text"],
            "approved_term_hints": hints,
        }
        payload["original_description"] = state["case"]["description"]
        attrs = self.call_model("extract_attributes", payload, Attributes)
        if attrs.missing and self.s.small_model_enabled:
            attrs = self.call_model("extract_attributes", payload, Attributes, teacher=True)
        # Never fabricate unspecified material/use. Clarification is bounded by Store.clarify.
        missing = list(dict.fromkeys(attrs.missing))
        attrs.missing = missing
        return {
            "attributes": attrs.model_dump(),
            "status": "awaiting_information" if missing else "running",
            "report": {"questions": [f"请补充商品的{n}" for n in missing]},
        }

    def retrieve(self, state):
        query = (
            state["normalization"]["normalized_text"]
            + " "
            + " ".join(str(v) for v in state["attributes"].values())
        )
        retrieved, history = recall(self.s, self.k, self.models, query, state["case"]["description"])
        expert = (
            self.memory.recall(
                state["case"]["description"],
                self._manifest["domain_version"],
                set(retrieved),
                self._job_id,
                allowed_ids=self._pinned_memories,
            )
            if self._memory_enabled
            else []
        )
        return {
            "retrieved": retrieved,
            "historical_cases": history,
            "expert_memories": expert,
        }

    def rank(self, stage, codes, state, notes=False):
        if not codes:
            raise ValueError("No candidates: cannot rank")
        brief = self.k.brief(codes, notes, state["case"]["description"])
        # Share repeated chapter/GIR evidence once, with a bounded global context.
        evidence, remaining = {}, self.s.evidence_budget_chars
        for note in state.get("extra_evidence", []):
            if note["id"] not in evidence and len(note["text"]) <= remaining:
                evidence[note["id"]] = note
                remaining -= len(note["text"])
        for position in range(8):
            for row in brief:
                candidates = row.get("notes", [])
                if position >= len(candidates):
                    continue
                note = candidates[position]
                if note["id"] not in evidence and len(note["text"]) <= remaining:
                    evidence[note["id"]] = note
                    remaining -= len(note["text"])
        for row in brief:
            row["note_ids"] = [n["id"] for n in row.pop("notes", []) if n["id"] in evidence]
        payload = {
            "original_description": state["case"]["description"],
            "submitted_elements": state["case"].get("elements", []),
            "attributes": state["attributes"],
            "candidates": brief,
            "allowed_codes": codes,
            "evidence": list(evidence.values()),
            "evidence_scope": "bounded excerpts; missing evidence is not evidence of inclusion",
            "historical_references_not_legal_authority": state.get("historical_cases", []),
            "reviewed_expert_references_not_legal_authority": state.get("expert_memories", []),
            "tool_observations": state.get("tool_observations", []),
            "domain_skills": list(
                {row["skill"]["id"]: row["skill"] for row in state.get("tool_observations", [])}.values()
            ),
        }
        accessible = {row["source_id"] for row in brief}
        accessible.update(evidence)
        for attempt in range(2):
            escalate = stage == "l1_heading_shortlist" and self.s.small_model_enabled and attempt > 0
            packed, _ = self.context.pack(stage, payload, Ranking)
            accessible = {row["source_id"] for row in packed["candidates"]} | {
                row["id"] for row in packed["evidence"]
            }
            ranking = self.call_model(stage, payload, Ranking, teacher=escalate)
            if stage == "l1_heading_shortlist" and self.s.small_model_enabled and ranking.conflicts:
                ranking = self.call_model(stage, payload, Ranking, teacher=True)
            try:
                self.k.validate_ranking(ranking, codes, accessible)
                return ranking
            except ValueError as exc:
                scope_error = not isinstance(exc, EvidenceError)
                if hasattr(self, "_job_id"):
                    self.store.event(
                        self._job_id,
                        "ranking_rejected",
                        {
                            "stage": stage,
                            "reason": "candidate_scope_or_duplicate"
                            if scope_error
                            else "unverifiable_citation",
                        },
                    )
                if attempt == 0:
                    payload["validation_feedback"] = (
                        "上次编码越界或重复。codes必须逐字取自allowed_codes且不得重复；历史案例的参考税号不能直接作为本阶段输出，不能跳层。允许编码："
                        + json.dumps(codes)
                        if scope_error
                        else "上次引用未通过验证。只能引用当前给定的source_id与逐字原文；证据不足则返回空citations并在conflicts说明，禁止改写成引文。"
                    )
                else:
                    if scope_error:
                        raise ModelError(
                            "Candidate scope validation failed after bounded correction"
                        ) from None
                    ranking.citations = []
                    ranking.conflicts = list(set(ranking.conflicts + ["unverifiable_citation"]))
                    self.k.validate_ranking(ranking, codes, accessible)
                    return ranking

    def l1(self, state):
        candidates = state["retrieved"]
        # Candidate reranking already ran in the shared retrieval pipeline.
        r = self.rank("l1_heading_shortlist", candidates, state)
        return {
            "shortlist": r.codes[:10],
            "conflicts": r.conflicts,
            "decisions": [{"stage": "l1", "reasons": r.reasons}],
        }

    def l2(self, state):
        allowed, excluded = self.k.allowed(state["shortlist"], state["attributes"])
        if not allowed:
            return {
                "ranked": [],
                "excluded": excluded,
                "citations": [],
                "conflicts": ["all_candidates_excluded"],
            }
        r = self.rank("l2_note_confirmation", allowed, state, notes=True)
        exploration = None
        if self.s.agentic_retrieval_enabled and (r.conflicts or not r.citations):

            def record(stage, data):
                if hasattr(self, "_run_id"):
                    self.store.event(
                        self._job_id, stage, data | {"run_id": self._run_id, "trace_id": trace_id()}
                    )

            explorer = ScopedExplorer(self.s, self.k, self.models, self.call_model, record)
            exploration = explorer.run(state["case"], state["attributes"], allowed, r)
            if exploration["questions"]:
                return {
                    "ranked": r.codes[:3],
                    "excluded": excluded,
                    "citations": [],
                    "conflicts": state.get("conflicts", []) + r.conflicts,
                    "status": "awaiting_information",
                    "report": {
                        "summary": "需要补充区分当前候选的关键信息",
                        "questions": exploration["questions"],
                    },
                    "exploration": exploration,
                }
            if exploration["evidence"] or exploration["references"]:
                state = state | {
                    "extra_evidence": exploration["evidence"],
                    "tool_observations": exploration["observations"],
                    "historical_cases": state.get("historical_cases", []) + exploration["references"],
                }
                r = self.rank("l2_note_confirmation", allowed, state, notes=True)
        return {
            "ranked": r.codes[:3],
            "excluded": excluded,
            "citations": [x.model_dump() for x in r.citations],
            "conflicts": state.get("conflicts", []) + r.conflicts,
            "decisions": state.get("decisions", []) + [{"stage": "l2", "reasons": r.reasons}],
            "exploration": exploration or {},
            "extra_evidence": state.get("extra_evidence", []),
            "tool_observations": state.get("tool_observations", []),
        }

    def subheading(self, state):
        resolved, citations, conflicts = [], list(state["citations"]), list(state["conflicts"])
        decisions, excluded = list(state.get("decisions", [])), list(state.get("excluded", []))
        for heading in state["ranked"]:
            parent = heading
            while self.k.children(parent) and len(parent) < state["case"].get("target_digits", 10):
                choices, rejected = self.k.allowed(self.k.children(parent), state["attributes"])
                excluded.extend(rejected)
                if not choices:
                    conflicts.append("no_legal_child:" + parent)
                    break
                r = self.rank("subheading_under_parent:" + parent, choices, state, notes=True)
                decisions.append({"stage": "subheading", "parent": parent, "reasons": r.reasons})
                parent = r.codes[0]
                citations.extend(x.model_dump() for x in r.citations)
                conflicts.extend(r.conflicts)
            resolved.append(parent)
        return {
            "resolved": resolved,
            "citations": citations,
            "conflicts": conflicts,
            "decisions": decisions,
            "excluded": excluded,
        }

    def verify(self, state):
        codes = state["resolved"]
        samples = [self.rank(f"final_vote_{i}", codes, state, notes=True) for i in range(5)]
        top = Counter(r.codes[0] for r in samples).most_common(1)[0][0]
        levels = {
            str(n): sum(r.codes[0][:n] == top[:n] for r in samples) / 5
            for n in (2, 4, 6, 8, 10)
            if len(top) >= n
        }
        citations = list(state["citations"])
        conflicts = list(state["conflicts"])
        for r in samples:
            citations.extend(x.model_dump() for x in r.citations)
            conflicts.extend(r.conflicts)
        self.k.path(top)
        if len(top) < state["case"].get("target_digits", 10):
            conflicts.append("resolution_incomplete")
        if not citations:
            conflicts.append("no_verifiable_citation")
        if any(v < 0.6 for v in levels.values()):
            conflicts.append("low_layer_agreement")
        rules = json.loads(self.s.rules.read_text(encoding="utf-8")) if self.s.rules.exists() else {}
        from .domain import Element

        standard = check_standard(
            [Element.model_validate(e) for e in state["case"].get("elements", [])], top[:2], rules
        )
        if state.get("risk_check", {}).get("synthetic") or state.get("normalization", {}).get("synthetic"):
            conflicts.append("synthetic_governance_policy")
        if any(self.k.entries[c].source_id.startswith("derived-index") for c in self.k.path(top)):
            conflicts.append("derived_node_requires_source_verification")
        if "notes2022" in self.k.corpus.version:
            conflicts.append("catalog_notes_version_alignment_required")
        if state["case"].get("retrieval_profile") == "bm25_diagnostic":
            conflicts.append("diagnostic_retrieval_profile")
        return {
            "votes": {"top_code": top, "levels": levels, "samples": [r.codes[0] for r in samples]},
            "conflicts": sorted(set(conflicts)),
            "citations": citations,
            "standard_check": standard,
            "status": "awaiting_review",
        }

    def teacher(self, state):
        conflicts = state.get("conflicts", [])
        if not conflicts:
            return {
                "status": "awaiting_review",
                "report": {
                    "summary": "Machine recommendation; expert approval required",
                    "teacher_status": "not_needed",
                },
            }
        if not self.s.deepseek_enabled:
            return {
                "status": "awaiting_review",
                "report": {
                    "summary": "疑难案例等待专家复核",
                    "teacher_status": "disabled",
                    "issues": conflicts,
                },
            }
        try:
            # Teacher branch intentionally excludes raw retrieved legal passages.
            r = self.call_model(
                "teacher_report_not_verified_authority",
                {
                    "case": state["case"],
                    "attributes": state["attributes"],
                    "candidates": state.get("resolved", state.get("ranked", [])),
                    "issues": conflicts,
                },
                Report,
                teacher=True,
            )
            return {"status": "awaiting_review", "report": r.model_dump() | {"teacher_status": "completed"}}
        except ModelError:
            return {
                "status": "awaiting_review",
                "report": {
                    "summary": "教师接口不可用，交专家复核",
                    "teacher_status": "unavailable",
                    "issues": conflicts,
                },
            }
