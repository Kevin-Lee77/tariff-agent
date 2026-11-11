import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select

from .agent_memory import AgentMemory
from .domain import Strict
from .persistence import agent_spans, events


class MemoryPublication(Strict):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    ttl_days: int = Field(default=180, ge=1, le=365)


class MemoryRevocation(Strict):
    reason: str = Field(min_length=3, max_length=1000)


def router(settings, user, reviewer, knowledge):
    routes = APIRouter()

    @routes.get("/v1/cases/{task_id}/memory", dependencies=[Depends(user)])
    def task_memory(task_id: str, request: Request):
        store = request.app.state.store
        result = AgentMemory(store, settings).case_memory(task_id)
        job = store.get(task_id)
        run = (job.get("result") or {}).get("provenance", {}).get("run_id")
        records = (
            [e for e in store.events(task_id, limit=5000) if e["data"].get("run_id") == run] if run else []
        )
        result["runtime"] = {
            "contexts": [e["data"] for e in records if e["stage"] == "context_prepared"][-30:],
            "actions": [
                {
                    "round": e["data"]["round"],
                    "action": e["data"]["action"],
                    "skill_id": e["data"]["skill_id"],
                }
                for e in records
                if e["stage"] == "agentic_action"
            ],
            "human_review": (job.get("result") or {}).get("human_review"),
        }
        return result

    @routes.get("/v1/memory/experts", dependencies=[Depends(user)])
    def memories(request: Request):
        return AgentMemory(request.app.state.store, settings).listing()

    @routes.post("/v1/cases/{task_id}/memory/publish", dependencies=[Depends(reviewer)])
    def publish(task_id: str, body: MemoryPublication, request: Request):
        return AgentMemory(request.app.state.store, settings).publish(
            task_id, body.expected_revision, request.state.actor["username"], knowledge(), body.ttl_days
        )

    @routes.post("/v1/memory/experts/{memory_id}/revoke", dependencies=[Depends(reviewer)])
    def revoke(memory_id: str, body: MemoryRevocation, request: Request):
        return AgentMemory(request.app.state.store, settings).revoke(
            memory_id, body.reason, request.state.actor["username"]
        )

    @routes.get("/v1/tasks/{task_id}/trace", dependencies=[Depends(user)])
    def trace(task_id: str, request: Request):
        store = request.app.state.store
        store.get(task_id)
        with store.connect() as db:
            rows = [
                dict(r._mapping)
                for r in db.execute(
                    select(agent_spans)
                    .where(agent_spans.c.job_id == task_id)
                    .order_by(agent_spans.c.started)
                    .limit(1000)
                )
            ]
        for row in rows:
            row["attributes"] = json.loads(row["attributes"])
        return {
            "items": rows,
            "backend": "opentelemetry-local-sql",
            "external_export_enabled": bool(settings.otlp_endpoint),
        }

    @routes.get("/v1/tasks/{task_id}/context/{artifact_id}", dependencies=[Depends(user)])
    def context_artifact(task_id: str, artifact_id: str, request: Request):
        store = request.app.state.store
        store.get(task_id)
        with store.connect() as db:
            records = db.execute(
                select(events.c.data)
                .where(events.c.job_id == task_id, events.c.stage == "context_artifact")
                .order_by(events.c.seq.desc())
                .limit(200)
            )
            for row in records:
                record = json.loads(row.data)
                if record["artifact_id"] == artifact_id:
                    return record
        raise HTTPException(404, "Context artifact not found in this task")

    return routes
