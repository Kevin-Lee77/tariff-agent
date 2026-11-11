"""Durable SQL repository, bounded leases, row-lock claiming, and audit trail."""

import contextlib
import json
import time
import uuid

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from .persistence import audit, engine_for, ensure_schema, events, feedback, jobs, terms, worker_health


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class Conflict(Exception):
    pass


class Store:
    def __init__(self, target, auto_create=True):
        self.engine = engine_for(str(target))
        if auto_create:
            ensure_schema(str(target))

    @contextlib.contextmanager
    def connect(self, write=False):
        with self.engine.connect() as db:
            try:
                if write and self.engine.dialect.name == "sqlite":
                    db.exec_driver_sql("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def decode(row):
        if row is None:
            raise KeyError("Task not found")
        data = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
        for key in ("payload", "result"):
            data[key] = json.loads(data[key]) if data[key] else None
        for key in ("token", "idem", "original_payload"):
            data.pop(key, None)
        from .runtime_context import input_revision

        data["revision"] = input_revision(data["payload"])
        return data

    @contextlib.contextmanager
    def lease_guard(self, key, token):
        """Hold the task row while saving state, so cancelled/obsolete workers cannot commit checkpoints."""
        with self.connect(write=True) as db:
            row = db.execute(
                select(jobs.c.id)
                .where(jobs.c.id == key, jobs.c.status == "running", jobs.c.token == token)
                .with_for_update()
            ).first()
            if row is None:
                raise Conflict("Worker lease no longer owns task")
            yield db

    def submit(self, kind, payload, idem):
        now, key = time.time(), uuid.uuid4().hex
        try:
            with self.connect(write=True) as db:
                old = db.execute(select(jobs).where(jobs.c.idem == idem)).first()
                if old:
                    return self._replay(old, kind, payload)
                db.execute(
                    insert(jobs).values(
                        id=key,
                        kind=kind,
                        payload=dump(payload),
                        original_payload=dump(payload),
                        status="queued",
                        idem=idem,
                        attempts=0,
                        created=now,
                        updated=now,
                    )
                )
        except IntegrityError:
            with self.connect() as db:
                old = db.execute(select(jobs).where(jobs.c.idem == idem)).first()
                if old:
                    return self._replay(old, kind, payload)
            raise Conflict("Concurrent task conflict") from None
        return self.get(key)

    def _replay(self, old, kind, payload):
        previous = json.loads(old.original_payload or old.payload)
        if old.kind != kind or self._canonical(kind, previous) != self._canonical(kind, payload):
            raise Conflict("Idempotency key already used for different input")
        return self.decode(old)

    @staticmethod
    def _canonical(kind, payload):
        from .domain import CaseInput
        from .governance import RiskRequest, TermRequest

        schema = {
            "case": CaseInput,
            "declaration_risk": CaseInput,
            "declaration_language": CaseInput,
            "terms": TermRequest,
            "risk": RiskRequest,
        }.get(kind)
        try:
            return schema.model_validate(payload).model_dump() if schema else payload
        except ValueError:
            return payload

    def replay(self, kind, payload, idem):
        with self.connect() as db:
            old = db.execute(select(jobs).where(jobs.c.idem == idem)).first()
            return self._replay(old, kind, payload) if old else None

    def get(self, key):
        with self.connect() as db:
            return self.decode(db.execute(select(jobs).where(jobs.c.id == key)).first())

    def list_jobs(self, status=None, kind=None, offset=0, limit=20):
        conditions = []
        if status:
            conditions.append(jobs.c.status == status)
        if kind:
            conditions.append(jobs.c.kind == kind)
        predicate = and_(*conditions) if conditions else True
        with self.connect() as db:
            total = db.scalar(select(func.count()).select_from(jobs).where(predicate))
            rows = db.execute(
                select(jobs)
                .where(predicate)
                .order_by(jobs.c.created.desc(), jobs.c.id)
                .offset(offset)
                .limit(limit)
            )
            return {"total": total, "items": [self.decode(row) for row in rows]}

    def claim(self, lease_seconds, max_attempts):
        now = time.time()
        with self.connect(write=True) as db:
            expired = and_(jobs.c.status == "running", jobs.c.lease < now)
            db.execute(
                update(jobs)
                .where(expired, jobs.c.attempts >= max_attempts)
                .values(status="failed", error="lease expired: retry budget exhausted", updated=now)
            )
            db.execute(update(jobs).where(expired, jobs.c.attempts < max_attempts).values(status="queued"))
            statement = (
                select(jobs).where(jobs.c.status == "queued").order_by(jobs.c.created, jobs.c.id).limit(1)
            )
            if self.engine.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            row = db.execute(statement).first()
            if row is None:
                return None
            token = uuid.uuid4().hex
            db.execute(
                update(jobs)
                .where(jobs.c.id == row.id)
                .values(
                    status="running",
                    attempts=jobs.c.attempts + 1,
                    token=token,
                    lease=now + lease_seconds,
                    updated=now,
                )
            )
            job = self.decode(row)
            job["token"] = token
            return job

    def finish(self, key, token, status, result=None, error=None):
        with self.connect(write=True) as db:
            count = db.execute(
                update(jobs)
                .where(jobs.c.id == key, jobs.c.status == "running", jobs.c.token == token)
                .values(
                    status=status,
                    result=dump(result) if result is not None else None,
                    error=error,
                    updated=time.time(),
                    lease=None,
                )
            ).rowcount
            if count != 1:
                raise Conflict("Worker lease no longer owns task")

    def renew(self, key, token, seconds):
        with self.connect(write=True) as db:
            return (
                db.execute(
                    update(jobs)
                    .where(jobs.c.id == key, jobs.c.token == token, jobs.c.status == "running")
                    .values(lease=time.time() + seconds)
                ).rowcount
                == 1
            )

    def owns(self, key, token):
        with self.connect() as db:
            return (
                db.execute(
                    select(jobs.c.id).where(
                        jobs.c.id == key, jobs.c.token == token, jobs.c.status == "running"
                    )
                ).first()
                is not None
            )

    def retry(self, key, max_attempts):
        with self.connect(write=True) as db:
            n = db.execute(
                update(jobs)
                .where(jobs.c.id == key, jobs.c.status == "failed", jobs.c.attempts < max_attempts)
                .values(status="queued", error=None, updated=time.time())
            ).rowcount
            if n != 1:
                raise Conflict("Not retryable or retry budget exhausted")

    def cancel(self, key):
        with self.connect(write=True) as db:
            n = db.execute(
                update(jobs)
                .where(
                    jobs.c.id == key,
                    jobs.c.status.in_(["queued", "running", "awaiting_information", "awaiting_review"]),
                )
                .values(status="cancelled", token=None, lease=None, updated=time.time())
            ).rowcount
            if n != 1:
                raise Conflict("Task is already terminal")

    def event(self, key, stage, data):
        with self.connect() as db:
            db.execute(insert(events).values(job_id=key, stage=stage, data=dump(data), created=time.time()))

    def events(self, key, after=0, limit=500):
        self.get(key)
        with self.connect() as db:
            rows = db.execute(
                select(events)
                .where(events.c.job_id == key, events.c.seq > after)
                .order_by(events.c.seq)
                .limit(limit)
            )
            return [dict(r._mapping) | {"data": json.loads(r.data)} for r in rows]

    @staticmethod
    def _save_terms(db, value):
        if db.execute(select(terms.c.id).where(terms.c.id == 1)).first():
            db.execute(update(terms).where(terms.c.id == 1).values(data=dump(value), updated=time.time()))
        else:
            db.execute(insert(terms).values(id=1, data=dump(value), updated=time.time()))

    def save_terms(self, value):
        with self.connect(write=True) as db:
            self._save_terms(db, value)

    def publish_terms(self, key):
        with self.connect(write=True) as db:
            job = self.decode(db.execute(select(jobs).where(jobs.c.id == key).with_for_update()).first())
            if job["kind"] != "terms" or job["status"] != "completed":
                raise Conflict("Only completed terminology proposals may be published")
            if job["result"].get("synthetic"):
                raise Conflict("Synthetic terminology cannot be published into the live alias map")
            value = job["result"] | {"published_from": key}
            self._save_terms(db, value)
            db.execute(
                insert(events).values(
                    job_id=key, stage="terms_published", data=dump({"source_job": key}), created=time.time()
                )
            )
        return {"published_from": key, "groups": len(value["groups"])}

    def terms(self):
        with self.connect() as db:
            row = db.execute(select(terms.c.data).where(terms.c.id == 1)).first()
            return json.loads(row.data) if row else {}

    def curate_terms(self, key, groups, reason, actor, idem):
        with self.connect(write=True) as db:
            parent = self.decode(db.execute(select(jobs).where(jobs.c.id == key)).first())
            if parent["kind"] != "terms" or parent["status"] != "completed":
                raise Conflict("Only completed terminology proposals can be curated")
            payload = {
                "description": f"人工修订：{len(groups)} 个词簇",
                "pending": [],
                "known": groups,
                "_source": key,
                "_reason": reason,
                "synthetic": parent["result"].get("synthetic", False),
            }
            old = db.execute(select(jobs).where(jobs.c.idem == idem)).first()
            if old:
                return self._replay(old, "terms", payload)
            flat = {w for main, aliases in groups.items() for w in [main] + aliases}
            result = parent["result"] | {
                "groups": groups,
                "curated_from": key,
                "merge_proposals": [],
                "independent_words": [
                    w for w in parent["result"].get("independent_words", []) if w not in flat
                ],
            }
            new, now = uuid.uuid4().hex, time.time()
            db.execute(
                insert(jobs).values(
                    id=new,
                    kind="terms",
                    payload=dump(payload),
                    original_payload=dump(payload),
                    status="completed",
                    result=dump(result),
                    idem=idem,
                    attempts=0,
                    created=now,
                    updated=now,
                )
            )
            db.execute(
                insert(events).values(
                    job_id=new,
                    stage="terms_curated",
                    data=dump({"parent_task": key, "reason": reason, "actor": actor}),
                    created=now,
                )
            )
        return self.get(new)

    def review(self, key, value, valid_codes):
        with self.connect(write=True) as db:
            row = db.execute(select(jobs).where(jobs.c.id == key).with_for_update()).first()
            self.decode(row)
            if row.kind != "case" or row.status != "awaiting_review":
                raise Conflict("Task is not awaiting review")
            if value.expected_revision and value.expected_revision != self.decode(row)["revision"]:
                raise Conflict("Input revision changed; reload before review")
            result = json.loads(row.result or "{}")
            if (
                result.get("provenance")
                and result["provenance"]["input_revision"] != self.decode(row)["revision"]
            ):
                raise Conflict("Result input version is stale; rerun before review")
            if result.get("risk_check", {}).get("decision") != "clear":
                raise Conflict("Risk precheck must be cleared before classification approval")
            if value.approved and result.get("risk_check", {}).get("synthetic"):
                raise Conflict("联调词典结果不能确认成正式归类样本")
            if value.approved and value.final_code not in valid_codes:
                raise Conflict("Final code is not in the active corpus")
            from .human_review import resume_review

            reviewed = self.decode(row)
            decision_hash = resume_review(self, db, reviewed, value)
            if decision_hash:
                result["human_review"] = result["human_review"] | {
                    "status": "completed",
                    "decision_hash": decision_hash,
                }
            db.execute(
                insert(feedback).values(job_id=key, data=dump(value.model_dump()), created=time.time())
            )
            db.execute(
                update(jobs)
                .where(jobs.c.id == key)
                .values(
                    status="approved" if value.approved else "rejected",
                    result=dump(result),
                    updated=time.time(),
                )
            )

    def clarify(self, key, elements, expected_revision=None):
        with self.connect(write=True) as db:
            row = db.execute(select(jobs).where(jobs.c.id == key).with_for_update()).first()
            job = self.decode(row)
            if job["kind"] != "case" or job["status"] != "awaiting_information":
                raise Conflict("Task is not waiting for clarification")
            if expected_revision and expected_revision != job["revision"]:
                raise Conflict("Input revision changed; reload before clarification")
            payload = job["payload"]
            by_name = {e["name"]: e for e in payload.get("elements", [])}
            by_name.update({e.name: e.model_dump() for e in elements})
            payload["elements"] = list(by_name.values())
            count = payload.get("_clarifications", 0) + 1
            if count > 3:
                raise Conflict("Clarification limit reached; expert review required")
            payload["_clarifications"] = count
            db.execute(
                update(jobs)
                .where(jobs.c.id == key)
                .values(payload=dump(payload), status="queued", attempts=0, error=None, updated=time.time())
            )

    def approved_samples(self):
        with self.connect() as db:
            rows = db.execute(
                select(jobs.c.id, jobs.c.payload, jobs.c.result, feedback.c.data)
                .join(feedback, jobs.c.id == feedback.c.job_id)
                .where(jobs.c.status == "approved")
            )
            return [
                {
                    "case_id": r.id,
                    "input": json.loads(r.payload),
                    "trace_result": json.loads(r.result or "{}"),
                    "feedback": json.loads(r.data),
                }
                for r in rows
            ]

    def audit(self, actor, action, resource="", detail=None):
        with self.connect() as db:
            db.execute(
                insert(audit).values(
                    actor=actor,
                    action=action,
                    resource=resource,
                    detail=dump(detail or {}),
                    created=time.time(),
                )
            )

    def audit_records(self, limit=100):
        with self.connect() as db:
            return [
                dict(r._mapping) | {"detail": json.loads(r.detail)}
                for r in db.execute(select(audit).order_by(audit.c.seq.desc()).limit(limit))
            ]

    def ping_worker(self, identifier):
        with self.connect(write=True) as db:
            if db.execute(select(worker_health.c.id).where(worker_health.c.id == identifier)).first():
                db.execute(
                    update(worker_health)
                    .where(worker_health.c.id == identifier)
                    .values(last_seen=time.time())
                )
            else:
                db.execute(insert(worker_health).values(id=identifier, last_seen=time.time()))

    def summary(self):
        with self.connect() as db:
            grouped = db.execute(select(jobs.c.status, func.count()).group_by(jobs.c.status))
            counts = {r[0]: r[1] for r in grouped}
            recent = db.scalar(select(func.max(worker_health.c.last_seen)))
            return {
                "counts": counts,
                "total": sum(counts.values()),
                "worker_last_seen": recent,
                "worker_online": bool(recent and time.time() - recent < 45),
                "database_backend": self.engine.dialect.name,
            }


def configured_store(settings):
    from .persistence import database_target

    return Store(database_target(settings), auto_create=not settings.production)
