"""Native interrupt/resume for expert decisions, separated from classification and side effects."""

from contextlib import contextmanager
from types import SimpleNamespace
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select

from .durable import checkpoint_store
from .persistence import jobs
from .runtime_context import digest
from .store import Conflict


class ReviewState(TypedDict, total=False):
    request: dict
    decision: dict


def review_graph(saver):
    def expert(state):
        decision = interrupt(state["request"])
        return {"decision": decision}

    graph = StateGraph(ReviewState)
    graph.add_node("expert_decision", expert)
    graph.add_edge(START, "expert_decision")
    graph.add_edge("expert_decision", END)
    return graph.compile(checkpointer=saver)


def prepare_review(store, settings, job, result):
    if not settings.native_human_review_enabled or not settings.durable_execution_enabled:
        return None
    if result.get("risk_check", {}).get("decision") != "clear":
        return None
    provenance = result["provenance"]
    request = {
        "case_id": job["id"],
        "run_id": provenance["run_id"],
        "input_revision": provenance["input_revision"],
        "domain_version": provenance["domain_version"],
        "execution_version": provenance["execution_version"],
        "proposal_hash": digest(
            {key: result.get(key) for key in ("votes", "resolved", "citations", "conflicts")}
        ),
    }
    thread = provenance["run_id"] + ":expert-review"
    config = {"configurable": {"thread_id": thread}}
    with checkpoint_store(store, settings, job) as saver:
        graph = review_graph(saver)
        snapshot = graph.get_state(config)
        output = graph.invoke(
            None if snapshot.values else {"request": request}, config=config, durability="sync"
        )
        pending = output.get("__interrupt__", ())
        if not pending:
            raise Conflict("Expert review checkpoint was already resolved")
    return {
        "thread_id": thread,
        "interrupt_id": pending[0].id,
        "request": request,
        "status": "waiting_for_expert",
    }


def resume_review(store, db, job, value):
    """Caller holds the job row through checkpoint persistence AND feedback commit."""
    review = job["result"].get("human_review")
    if not review:
        return None  # Older results remain compatible with the existing application review.
    decision = value.model_dump()
    # The version is pinned in request, including when a direct trusted repository caller omits it.
    decision["expected_revision"] = job["revision"]
    config = {"configurable": {"thread_id": review["thread_id"]}}

    @contextmanager
    def fence():
        current = db.execute(select(jobs.c.status).where(jobs.c.id == job["id"])).scalar_one()
        if current != "awaiting_review":
            raise Conflict("Expert decision no longer owns this review")
        yield

    with checkpoint_store(store, SimpleNamespace(durable_execution_enabled=True), job, fence=fence) as saver:
        graph = review_graph(saver)
        snapshot = graph.get_state(config)
        if snapshot.values.get("request") != review["request"]:
            raise Conflict("Human review checkpoint does not match current proposal")
        previous = snapshot.values.get("decision")
        if previous is not None:
            if previous != decision:
                raise Conflict("A different expert decision is already durably recorded")
            return digest(decision)
        pending = [i for task in snapshot.tasks for i in task.interrupts]
        if not any(item.id == review["interrupt_id"] for item in pending):
            raise Conflict("Expected expert interrupt is not pending")
        output = graph.invoke(
            Command(resume={review["interrupt_id"]: decision}), config=config, durability="sync"
        )
        if output.get("decision") != decision or output.get("__interrupt__"):
            raise Conflict("Native expert review did not complete")
    return digest(decision)
