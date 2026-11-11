"""Case-scoped working memory and explicitly promoted, revocable expert references."""

import json
import math
import re
import time
from collections import Counter

from sqlalchemy import insert, select, update

from .benchmark_data import canonical
from .history import tokenize
from .persistence import agent_runs, expert_memories, feedback, jobs
from .runtime_context import digest, input_revision, versions
from .store import Conflict, dump

NAMESPACE = "cn:reviewed-expert-cases"
STOPWORDS = {
    "商品",
    "名称",
    "品名",
    "规格",
    "型号",
    "规格型号",
    "申报",
    "用途",
    "材质",
    "方式",
    "加工",
    "的",
    "和",
    "及",
}


def negations(text):
    return {
        canonical(m[0])
        for m in re.finditer(r"(?:不含|未添加|未经|未|非专用|无)[^，,。；;|\n:：]{0,50}", text)
    }


class AgentMemory:
    def __init__(self, store, settings):
        self.store, self.s = store, settings

    def active(self, domain_version, db=None, metadata_only=False, limit=None):
        statement = (
            select(expert_memories.c.id, expert_memories.c.expires)
            if metadata_only
            else select(expert_memories)
        )
        statement = statement.where(
            expert_memories.c.domain_version == domain_version,
            expert_memories.c.namespace == NAMESPACE,
            expert_memories.c.status == "published",
            expert_memories.c.expires > time.time(),
        ).order_by(expert_memories.c.id)
        if limit:
            statement = (
                statement.order_by(None)
                .order_by(expert_memories.c.created.desc(), expert_memories.c.id)
                .limit(limit)
            )
        if db is not None:
            return [dict(r._mapping) for r in db.execute(statement)]
        with self.store.connect() as connection:
            return [dict(r._mapping) for r in connection.execute(statement)]

    def epoch(self, domain_version):
        return digest([(r["id"], r["expires"]) for r in self.active(domain_version, metadata_only=True)])

    def begin(self, job, manifest, scope, preflight):
        run_id = digest(
            [
                job["id"],
                input_revision(job["payload"]),
                manifest["domain_version"],
                manifest["execution_version"],
                manifest["memory_version"],
                scope,
                preflight,
            ]
        )[:32]
        now = time.time()
        with self.store.lease_guard(job["id"], job["token"]) as db:
            previous = db.execute(select(agent_runs).where(agent_runs.c.id == run_id)).first()
            if previous is None:
                db.execute(
                    insert(agent_runs).values(
                        id=run_id,
                        job_id=job["id"],
                        domain_version=manifest["domain_version"],
                        execution_version=manifest["execution_version"],
                        input_revision=input_revision(job["payload"]),
                        scope=scope,
                        status="running",
                        stage="preflight",
                        state=dump(
                            {
                                "confirmed_elements": job["payload"].get("elements", []),
                                "pending_questions": [],
                                "memory_version": manifest["memory_version"],
                            }
                        ),
                        created=now,
                        updated=now,
                    )
                )
            else:
                db.execute(
                    update(agent_runs).where(agent_runs.c.id == run_id).values(status="running", updated=now)
                )
        return run_id, previous is not None

    def record(self, job, run_id, stage, state, trace_id=None, manifest=None):
        notes = {
            "confirmed_elements": state["case"].get("elements", []),
            "original_description": state["case"]["description"],
            "clarification_rounds": state["case"].get("_clarifications", 0),
            "pending_questions": state.get("report", {}).get("questions", []),
            "proposed_attributes": state.get("attributes", {}),
            "candidate_codes": state.get("resolved", state.get("ranked", state.get("retrieved", []))),
            "expert_memory_ids": [r["memory_id"] for r in state.get("expert_memories", [])],
            "evidence_manifest": state.get("evidence_manifest", {}),
            "memory_version": (manifest or {}).get("memory_version"),
        }
        with self.store.lease_guard(job["id"], job["token"]) as db:
            db.execute(
                update(agent_runs)
                .where(agent_runs.c.id == run_id)
                .values(
                    stage=stage,
                    state=dump(notes),
                    trace_id=trace_id,
                    status=state.get("status", "running"),
                    updated=time.time(),
                )
            )

    def case_memory(self, job_id):
        job = self.store.get(job_id)
        with self.store.connect() as db:
            runs = [
                dict(r._mapping)
                for r in db.execute(
                    select(agent_runs)
                    .where(agent_runs.c.job_id == job_id)
                    .order_by(agent_runs.c.updated.desc())
                    .limit(20)
                )
            ]
        for run in runs:
            run["state"] = json.loads(run["state"])
        return {
            "case_id": job_id,
            "input_revision": job["revision"],
            "confirmed_elements": job["payload"].get("elements", []),
            "runs": runs,
            "facts_source": "submitted_elements_only; inferred attributes remain proposals",
        }

    def failed(self, job, run_id, stage="unknown"):
        with self.store.lease_guard(job["id"], job["token"]) as db:
            db.execute(
                update(agent_runs)
                .where(agent_runs.c.id == run_id)
                .values(status="failed", stage="failed:" + stage, updated=time.time())
            )

    def publish(self, job_id, expected_revision, actor, knowledge, ttl_days=180):
        if not 1 <= ttl_days <= 365:
            raise Conflict("Memory expiry must be 1..365 days")
        current = versions(self.s, self.store)["domain_version"]
        with self.store.connect(write=True) as db:
            row = db.execute(select(jobs).where(jobs.c.id == job_id).with_for_update()).first()
            job = self.store.decode(row)
            if job["revision"] != expected_revision:
                raise Conflict("Input revision changed; reload before publishing memory")
            result = job.get("result") or {}
            if job["kind"] != "case" or job["status"] != "approved":
                raise Conflict("Only explicitly approved cases can become expert memory")
            if result.get("risk_check", {}).get("decision") != "clear" or any(
                result.get(key, {}).get("synthetic") for key in ("risk_check", "normalization")
            ):
                raise Conflict("Uncleared or synthetic cases cannot become expert memory")
            provenance = result.get("provenance", {})
            if (
                provenance.get("domain_version") != current
                or provenance.get("input_revision") != expected_revision
            ):
                raise Conflict("Case policy/input version is stale or unversioned; rerun and review")
            used = {r["memory_id"] for r in result.get("expert_memories", [])}
            if not used <= {r["id"] for r in self.active(current, db=db, metadata_only=True)}:
                raise Conflict("An expert reference was revoked or expired; rerun and review")
            reviewed = db.execute(select(feedback.c.data).where(feedback.c.job_id == job_id)).first()
            decision = json.loads(reviewed.data) if reviewed else {}
            code = decision.get("final_code")
            if not decision.get("approved") or code not in knowledge.formal_codes:
                raise Conflict("Expert final code is not valid in the active corpus")
            old = db.execute(select(expert_memories).where(expert_memories.c.source_job == job_id)).first()
            if old:
                return {"id": old.id, "status": old.status, "replayed": True}
            # Store the expert decision and user facts; no generated report is promoted as fact or law.
            data = {
                "description": job["payload"]["description"],
                "elements": job["payload"].get("elements", []),
                "proposed_attributes": result.get("attributes", {}),
                "expert_reason": decision["reason"],
                "evidence_manifest": result.get("evidence_manifest", {}),
                "input_revision": expected_revision,
            }
            key = digest([job_id, current, code])[:32]
            now = time.time()
            db.execute(
                insert(expert_memories).values(
                    id=key,
                    source_job=job_id,
                    domain_version=current,
                    namespace=NAMESPACE,
                    code=code,
                    status="published",
                    data=dump(data),
                    publisher=actor,
                    created=now,
                    expires=now + ttl_days * 86400,
                )
            )
        self.store.audit(
            actor, "expert_memory_publish", key, {"source_job": job_id, "domain_version": current}
        )
        return {"id": key, "status": "published", "replayed": False}

    def revoke(self, key, reason, actor):
        if not 3 <= len(reason.strip()) <= 1000:
            raise Conflict("Revocation requires a reason")
        with self.store.connect(write=True) as db:
            row = db.execute(
                select(expert_memories).where(expert_memories.c.id == key).with_for_update()
            ).first()
            if row is None:
                raise KeyError("Memory not found")
            if row.status != "revoked":
                db.execute(
                    update(expert_memories)
                    .where(expert_memories.c.id == key)
                    .values(status="revoked", revoked=time.time(), revocation_reason=reason)
                )
        self.store.audit(actor, "expert_memory_revoke", key, {"reason": reason})
        return {"id": key, "status": "revoked"}

    def listing(self):
        current = versions(self.s, self.store)["domain_version"]
        with self.store.connect() as db:
            rows = [
                dict(r._mapping)
                for r in db.execute(
                    select(expert_memories).order_by(expert_memories.c.created.desc()).limit(100)
                )
            ]
        for row in rows:
            row["data"] = json.loads(row["data"])
            row["applicable"] = (
                row["status"] == "published"
                and row["expires"] > time.time()
                and row["domain_version"] == current
            )
        return {
            "items": rows,
            "domain_version": current,
            "namespace": NAMESPACE,
            "backend": "sql-authoritative-reviewed-memory",
        }

    def recall(self, query, domain_version, headings, source_job, allowed_ids=None):
        # Small, curated expert store: precise version/authority filtering first, bounded lexical ranking second.
        rows = self.active(domain_version, limit=5000)
        query_words = set(tokenize(query)) - STOPWORDS
        candidates, frequencies = [], Counter()
        for row in rows:
            if allowed_ids is not None and row["id"] not in allowed_ids:
                continue
            data = json.loads(row["data"])
            if row["source_job"] == source_job or row["code"][:4] not in headings:
                continue
            if negations(query) != negations(data["description"]):
                continue
            words = set(tokenize(data["description"])) - STOPWORDS
            if not words & query_words:
                continue
            candidates.append((row, data, words))
            frequencies.update(words)
        scored = []
        for row, data, words in candidates:
            score = sum(
                math.log(1 + len(candidates) / (1 + frequencies[word])) for word in query_words & words
            )
            scored.append((score, row, data))
        result, remaining = [], self.s.memory_context_budget_chars
        for score, row, data in sorted(scored, key=lambda r: (-r[0], r[1]["id"])):
            item = {
                "memory_id": row["id"],
                "source_job": row["source_job"],
                "reference_code": row["code"],
                "description": data["description"][:800],
                "expert_reason": data["expert_reason"][:600],
                "authority": "reviewed_expert_reference_only_not_law",
                "domain_version": row["domain_version"],
            }
            size = len(dump(item))
            if size > remaining:
                continue
            result.append(item)
            remaining -= size
            if len(result) >= self.s.expert_memory_limit:
                break
        return result
