import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import Identity, Login, UserCreate
from .config import Settings
from .domain import CaseInput, Element, Feedback, Strict
from .governance import Aliases, RiskRequest, TermRequest, Word, check_standard, negative
from .knowledge import cached_knowledge
from .legacy import JavaSynonymRequest, convert_synonyms
from .observability import HTTP_LATENCY, HTTP_REQUESTS, metrics_response
from .store import Conflict, configured_store


class Clarification(Strict):
    elements: list[Element] = Field(min_length=1, max_length=50)
    expected_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class StandardInput(Strict):
    chapter: str = Field(pattern=r"^\d{2}$")
    elements: list[Element] = Field(max_length=50)


class TermCuration(Strict):
    groups: dict[Word, Aliases] = Field(max_length=2000)
    reason: str = Field(min_length=3, max_length=1000)


def create_app(settings=None):
    s = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        keys = [s.api_key.get_secret_value(), s.reviewer_key.get_secret_value()]
        if any(len(k) < 24 or k.startswith("replace-") for k in keys) or keys[0] == keys[1]:
            raise RuntimeError("Configure two distinct API/reviewer keys, each >=24 characters")
        if s.production and (
            not s.database_url.get_secret_value().startswith("postgresql+psycopg://") or not s.cookie_secure
        ):
            raise RuntimeError("Production requires PostgreSQL and secure session cookies behind HTTPS")
        app.state.store = configured_store(s)
        app.state.identity = Identity(app.state.store, s)
        yield

    app = FastAPI(title="TariffAgent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=s.allowed_hosts)

    def user(request: Request):
        roles = (
            ("operator", "reviewer")
            if request.method == "GET" or request.url.path.startswith("/auth/")
            else ("operator",)
        )
        return request.app.state.identity.authenticate(request, roles)

    def reviewer(request: Request):
        return request.app.state.identity.authenticate(request, ("reviewer",))

    def admin(request: Request):
        return request.app.state.identity.authenticate(request, ("admin",))

    @app.middleware("http")
    async def request_context(request, call_next):
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, route, response.status_code).inc()
        HTTP_LATENCY.labels(request.method, route).observe(time.monotonic() - started)
        actor = getattr(request.state, "actor", None)
        if actor and request.method == "POST" and response.status_code < 400:
            request.app.state.store.audit(
                actor["username"], request.url.path, detail={"request_id": request_id}
            )
        response.headers.update(
            {
                "X-Request-ID": request_id,
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "same-origin",
                "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
            }
        )
        if request.url.path.startswith(("/v1/", "/auth/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/auth/login")
    def login(body: Login, request: Request, response: Response):
        if len(body.password.get_secret_value()) > 256:
            raise HTTPException(400, "Invalid credentials")
        token, actor = request.app.state.identity.login(
            body.username, body.password.get_secret_value(), request.client.host
        )
        response.set_cookie(
            "tariff_session",
            token,
            httponly=True,
            secure=s.cookie_secure,
            samesite="strict",
            max_age=s.session_seconds,
            path="/",
        )
        response.set_cookie(
            "tariff_csrf",
            actor["csrf"],
            httponly=False,
            secure=s.cookie_secure,
            samesite="strict",
            max_age=s.session_seconds,
            path="/",
        )
        return actor

    @app.get("/auth/me", dependencies=[Depends(user)])
    def me(request: Request):
        return request.state.actor | {"csrf": request.cookies.get("tariff_csrf", "")}

    @app.post("/auth/logout", dependencies=[Depends(user)])
    def logout(request: Request, response: Response):
        request.app.state.identity.logout(request.cookies.get("tariff_session", ""))
        response.delete_cookie("tariff_session", path="/")
        response.delete_cookie("tariff_csrf", path="/")
        return {"status": "logged_out"}

    @app.get("/v1/users", dependencies=[Depends(admin)])
    def users(request: Request):
        return request.app.state.identity.list_users()

    @app.post("/v1/users", status_code=201, dependencies=[Depends(admin)])
    def create_user(body: UserCreate, request: Request):
        try:
            request.app.state.identity.create_user(body.username, body.password.get_secret_value(), body.role)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"username": body.username, "role": body.role}

    def idem(idempotency_key: str = Header(min_length=8, max_length=160)):
        return idempotency_key

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    async def missing_handler(request, exc):
        return JSONResponse(status_code=404, content={"detail": "Task not found"})

    def knowledge():
        try:
            return cached_knowledge(s.corpus, s.allow_test_corpus)
        except (OSError, ValueError):
            raise HTTPException(503, "Versioned CN tariff corpus unavailable") from None

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/metrics", dependencies=[Depends(user)])
    def metrics():
        return metrics_response()

    @app.get("/v1/dashboard", dependencies=[Depends(user)])
    def dashboard(request: Request):
        return request.app.state.store.summary()

    @app.get("/v1/audit", dependencies=[Depends(admin)])
    def audit(request: Request):
        return request.app.state.store.audit_records()

    @app.get("/v1/knowledge/summary", dependencies=[Depends(user)])
    def knowledge_summary():
        k = knowledge()
        return {
            "version": k.corpus.version,
            "source": k.corpus.source,
            "jurisdiction": k.corpus.jurisdiction,
            "entries": len(k.formal_codes),
            "headings": len(k.headings),
            "groups": len(k.corpus.groups),
            "evidence_fragments": len(k.sources),
            "test_only": k.corpus.test_only,
        }

    @app.get("/v1/knowledge/entries/{code}", dependencies=[Depends(user)])
    def entry(code: str, query: str = Query(default="", max_length=6000)):
        k = knowledge()
        if code not in k.entries:
            raise HTTPException(404, "Code not in active corpus")
        return {
            "path": k.path(code),
            "entry": k.brief([code], notes=True, query=query)[0],
            "children": k.brief(k.children(code)),
        }

    @app.get("/v1/knowledge/documents/notes2022.pdf", dependencies=[Depends(user)])
    def original_notes():
        if not s.notes_pdf.is_file():
            raise HTTPException(404, "Original notes PDF not installed")
        return FileResponse(s.notes_pdf, media_type="application/pdf", content_disposition_type="inline")

    @app.get("/v1/tasks", dependencies=[Depends(user)])
    def tasks(
        request: Request,
        status: str | None = None,
        kind: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ):
        return request.app.state.store.list_jobs(status, kind, offset, limit)

    @app.get("/v1/terms", dependencies=[Depends(user)])
    def terms_snapshot(request: Request):
        return request.app.state.store.terms()

    @app.get("/health/ready", dependencies=[Depends(user)])
    def ready(request: Request):
        k = knowledge()
        import httpx

        checks = {
            "deepseek_key_present": bool(s.deepseek_api_key.get_secret_value()),
            "vector_gateway": not (s.dense_enabled or s.rerank_enabled),
        }
        if s.dense_enabled or s.rerank_enabled:
            try:
                from .model_transport import gateway_health

                checks["vector_gateway"] = gateway_health(s).get("status") == "ready"
            except (httpx.HTTPError, ValueError):
                pass
        ok = all(checks.values())
        if s.history_enabled or s.qdrant_catalog_enabled:
            from .history import retrieval_ready

            checks["qdrant_indexes"] = retrieval_ready(s, dense=s.dense_enabled)
            ok = all(checks.values())
        return JSONResponse(
            status_code=200 if ok else 503,
            content={
                "status": "ready" if ok else "not_ready",
                "corpus_version": k.corpus.version,
                "checks": checks,
                "deepseek_connectivity_verified": False,
            },
        )

    @app.post("/v1/cases", status_code=202, dependencies=[Depends(user)])
    def submit(body: CaseInput, request: Request, key=Depends(idem)):
        existing = request.app.state.store.replay("case", body.model_dump(), key)
        if existing is not None:
            return existing
        knowledge()
        # Risk precheck must remain available even while inference dependencies are down.
        return request.app.state.store.submit("case", body.model_dump(), key)

    @app.get("/v1/cases/{task_id}/enterprise-feedback", dependencies=[Depends(user)])
    def enterprise_feedback(task_id: str, request: Request):
        task = request.app.state.store.get(task_id)
        result = task.get("result") or {}
        risk = result.get("risk_check", result if task["kind"] == "declaration_risk" else {})
        if not risk:
            raise HTTPException(409, "尚无税智源风险报告")
        return JSONResponse(
            {"task_id": task_id, "status": task["status"], "risk_report": risk},
            headers={"Content-Disposition": f'attachment; filename="enterprise-feedback-{task_id}.json"'},
        )

    @app.post("/v1/cases/{task_id}/resubmissions", status_code=202, dependencies=[Depends(user)])
    def resubmit(task_id: str, body: CaseInput, request: Request, key=Depends(idem)):
        parent = request.app.state.store.get(task_id)
        if parent["kind"] != "case" or parent["status"] in ("queued", "running"):
            raise HTTPException(409, "当前任务不能提交修订版本")
        result = request.app.state.store.submit("case", body.model_dump(), "revision:" + task_id + ":" + key)
        request.app.state.store.event(result["id"], "resubmitted", {"parent_task": task_id})
        return result

    @app.post("/v1/governance/declarations/check", status_code=202, dependencies=[Depends(user)])
    def declaration_check(body: CaseInput, request: Request, key=Depends(idem)):
        return request.app.state.store.submit("declaration_risk", body.model_dump(), key)

    @app.post("/v1/governance/declarations/normalize", status_code=202, dependencies=[Depends(user)])
    def declaration_normalize(body: CaseInput, request: Request, key=Depends(idem)):
        return request.app.state.store.submit("declaration_language", body.model_dump(), key)

    @app.get("/v1/tasks/{task_id}", dependencies=[Depends(user)])
    def task(task_id: str, request: Request):
        return request.app.state.store.get(task_id)

    @app.get("/v1/tasks/{task_id}/events", dependencies=[Depends(user)])
    def events(task_id: str, request: Request, after: int = Query(0, ge=0)):
        return request.app.state.store.events(task_id, after=after)

    @app.get("/v1/tasks/{task_id}/stream", dependencies=[Depends(user)])
    def stream(task_id: str, request: Request, after: int = Query(0, ge=0)):
        store = request.app.state.store
        store.get(task_id)

        async def generate():
            cursor = after
            for _ in range(40):
                if await request.is_disconnected():
                    return
                for event in store.events(task_id, after=cursor):
                    cursor = event["seq"]
                    yield f"id: {cursor}\nevent: stage\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if store.get(task_id)["status"] not in ("queued", "running"):
                    yield "event: settled\ndata: {}\n\n"
                    return
                yield ": keepalive\n\n"
                await asyncio.sleep(0.75)

        return StreamingResponse(
            generate(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.post("/v1/tasks/{task_id}/cancel", dependencies=[Depends(user)])
    def cancel(task_id: str, request: Request):
        request.app.state.store.cancel(task_id)
        return request.app.state.store.get(task_id)

    @app.post("/v1/cases/{task_id}/clarifications", status_code=202, dependencies=[Depends(user)])
    def clarify(task_id: str, body: Clarification, request: Request):
        current = request.app.state.store.get(task_id)
        if (current.get("result") or {}).get("provenance") and body.expected_revision is None:
            raise Conflict("expected_revision is required for versioned clarification")
        request.app.state.store.clarify(task_id, body.elements, body.expected_revision)
        return request.app.state.store.get(task_id)

    @app.post("/v1/tasks/{task_id}/retry", status_code=202, dependencies=[Depends(reviewer)])
    def retry(task_id: str, request: Request):
        request.app.state.store.retry(task_id, s.max_attempts)
        return request.app.state.store.get(task_id)

    @app.post("/v1/cases/{task_id}/review", dependencies=[Depends(reviewer)])
    def review(task_id: str, body: Feedback, request: Request):
        from .runtime_context import versions

        current = request.app.state.store.get(task_id)
        provenance = (current.get("result") or {}).get("provenance")
        if provenance and body.expected_revision is None:
            raise Conflict("expected_revision is required for versioned expert review")
        if (
            provenance
            and provenance["domain_version"] != versions(s, request.app.state.store)["domain_version"]
        ):
            raise Conflict("Policy version changed; rerun the case before approving")
        if provenance:
            from .agent_memory import AgentMemory

            memory = AgentMemory(request.app.state.store, s)
            used = {r["memory_id"] for r in current["result"].get("expert_memories", [])}
            if not used <= {r["id"] for r in memory.active(provenance["domain_version"], metadata_only=True)}:
                raise Conflict("Expert reference was revoked or expired; rerun before approving")
        if not request.state.actor["service"]:
            body = body.model_copy(update={"reviewer": request.state.actor["username"]})
        request.app.state.store.review(task_id, body, knowledge().formal_codes)
        return {"task_id": task_id, "status": "approved" if body.approved else "rejected"}

    @app.get("/v1/feedback/export", dependencies=[Depends(reviewer)])
    def export_feedback(request: Request):
        rows = request.app.state.store.approved_samples()
        return StreamingResponse(
            (json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="expert-approved.jsonl"'},
        )

    @app.post("/v1/governance/terms/jobs", status_code=202, dependencies=[Depends(user)])
    def terms(body: TermRequest, request: Request, key=Depends(idem)):
        return request.app.state.store.submit("terms", body.model_dump(), key)

    @app.post("/v1/integrations/java/synonym", status_code=202, dependencies=[Depends(user)])
    def java_synonym(body: JavaSynonymRequest, request: Request):
        try:
            converted = convert_synonyms(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        job = request.app.state.store.submit("terms", converted.model_dump(), "java-synonym:" + body.taskId)
        return {
            "success": True,
            "data": {"taskId": body.taskId, "algorithmTaskId": job["id"], "status": job["status"]},
        }

    @app.post("/v1/governance/terms/jobs/{task_id}/publish", dependencies=[Depends(reviewer)])
    def publish_terms(task_id: str, request: Request):
        return request.app.state.store.publish_terms(task_id)

    @app.post("/v1/governance/terms/jobs/{task_id}/curate", status_code=201, dependencies=[Depends(reviewer)])
    def curate_terms(task_id: str, body: TermCuration, request: Request, key=Depends(idem)):
        seen = set()
        for main, aliases in body.groups.items():
            for word in [main] + aliases:
                if word in seen or negative(word) != negative(main):
                    raise HTTPException(422, "词不能属于多个分组，肯定与否定表达不能合并")
                seen.add(word)
        return request.app.state.store.curate_terms(
            task_id, body.groups, body.reason, request.state.actor["username"], key
        )

    @app.post("/v1/governance/risk/jobs", status_code=202, dependencies=[Depends(user)])
    def risks(body: RiskRequest, request: Request, key=Depends(idem)):
        if not s.word2vec.exists():
            raise HTTPException(503, "Trusted Word2Vec artifact unavailable")
        return request.app.state.store.submit("risk", body.model_dump(), key)

    @app.post("/v1/governance/standard/check", dependencies=[Depends(user)])
    def standards(body: StandardInput):
        if not s.rules.exists():
            raise HTTPException(503, "Standard-value rules unavailable")
        return check_standard(body.elements, body.chapter, json.loads(s.rules.read_text(encoding="utf-8")))

    from .asset_api import router as asset_router

    app.include_router(asset_router(s, user, reviewer, idem))
    from .asset_batch_api import router as batch_router

    app.include_router(batch_router(s, user, reviewer, idem))
    from .memory_api import router as memory_router

    app.include_router(memory_router(s, user, reviewer, knowledge))
    from .evaluation_api import router as evaluation_router

    app.include_router(evaluation_router(s, user))
    if s.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(s.frontend_dist), html=True), name="console")
    return app
