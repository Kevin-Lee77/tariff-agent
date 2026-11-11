import pytest
from fastapi.testclient import TestClient

from tariff_agent.api import create_app
from tariff_agent.auth import Identity
from tariff_agent.store import Conflict, Store


def make_user(settings, username="operator", role="operator"):
    Identity(Store(settings.database), settings).create_user(username, "test-password-123!", role)


def test_session_csrf_roles_and_logout(settings):
    make_user(settings)
    make_user(settings, "reviewer", "reviewer")
    with TestClient(create_app(settings)) as client:
        login = client.post("/auth/login", json={"username": "operator", "password": "test-password-123!"})
        assert login.status_code == 200
        assert "HttpOnly" in login.headers.get_list("set-cookie")[0]
        assert client.get("/auth/me").json()["role"] == "operator"
        assert (
            client.post(
                "/v1/governance/terms/jobs", headers={"Idempotency-Key": "csrf-block"}, json={"pending": []}
            ).status_code
            == 403
        )
        headers = {"X-CSRF-Token": login.json()["csrf"], "Idempotency-Key": "csrf-pass"}
        assert (
            client.post("/v1/governance/terms/jobs", headers=headers, json={"pending": []}).status_code == 202
        )
        assert client.get("/v1/audit").status_code == 403
        assert client.post("/auth/logout", headers=headers).status_code == 200
        assert client.get("/auth/me").status_code == 401
        reviewer = client.post("/auth/login", json={"username": "reviewer", "password": "test-password-123!"})
        assert (
            client.post(
                "/v1/cases",
                headers={"X-CSRF-Token": reviewer.json()["csrf"], "Idempotency-Key": "review-no-create"},
                json={"description": "测试商品"},
            ).status_code
            == 403
        )
        assert (
            client.post("/auth/logout", headers={"X-CSRF-Token": reviewer.json()["csrf"]}).status_code == 200
        )


def test_login_lockout_persists_across_api_restarts(settings):
    make_user(settings)
    for _ in range(5):
        with TestClient(create_app(settings)) as c:
            assert (
                c.post("/auth/login", json={"username": "operator", "password": "wrong"}).status_code == 401
            )
    with TestClient(create_app(settings)) as c:
        assert (
            c.post("/auth/login", json={"username": "operator", "password": "test-password-123!"}).status_code
            == 429
        )


def test_cancel_invalidates_worker_ownership(settings):
    store = Store(settings.database)
    job = store.submit("case", {"description": "商品"}, "cancel-test")
    claim = store.claim(100, 3)
    assert store.owns(job["id"], claim["token"])
    store.cancel(job["id"])
    assert not store.owns(job["id"], claim["token"])
    with pytest.raises(Conflict):
        store.finish(job["id"], claim["token"], "completed", {})
    assert store.get(job["id"])["status"] == "cancelled"


def test_dashboard_filters_audit_and_metrics(settings):
    make_user(settings, "admin", "admin")
    with TestClient(create_app(settings)) as client:
        login = client.post("/auth/login", json={"username": "admin", "password": "test-password-123!"})
        headers = {"X-CSRF-Token": login.json()["csrf"], "Idempotency-Key": "console-task"}
        task = client.post("/v1/governance/terms/jobs", json={"pending": []}, headers=headers).json()
        assert client.get("/v1/tasks?kind=terms").json()["total"] == 1
        assert client.get("/v1/tasks?kind=case").json()["total"] == 0
        assert client.get("/v1/dashboard").json()["counts"]["queued"] == 1
        assert client.get("/v1/audit").json()
        assert client.get("/metrics").status_code == 200
        response = client.post("/v1/tasks/" + task["id"] + "/cancel", headers=headers)
        assert response.json()["status"] == "cancelled"


def test_request_budget_stops_generation(settings):
    from tariff_agent.clients import ModelError, Models

    settings.max_model_calls = 1
    store = Store(settings.database)
    job = store.submit("case", {"description": "商品"}, "budget-test")
    claim = store.claim(100, 3)
    model = Models(settings)
    model.bind(store, job["id"], claim["token"])
    model._guard("first")
    with pytest.raises(ModelError, match="budget"):
        model._guard("second")
    model.close()


def test_java_adapter_preserves_confirmed_tree_and_rejects_sql(settings):
    from tariff_agent.legacy import JavaSynonymRequest, convert_synonyms

    request = JavaSynonymRequest(
        taskId="java-test",
        rows=[
            {"uuid": "main", "name": "鸡翅尖", "status": "CONFIRMED"},
            {"uuid": "alias", "name": "鸡翼尖", "parentUuid": "main", "status": "CONFIRMED"},
            {"uuid": "new", "name": "鸡肌腱", "status": "UNTRAINED"},
        ],
    )
    result = convert_synonyms(request)
    assert result.known == {"鸡翅尖": ["鸡翼尖"]}
    assert result.pending == ["鸡肌腱"]
    with pytest.raises(ValueError):
        JavaSynonymRequest(taskId="unsafe", rows=[], params="select * from users")


def test_manual_curation_creates_traceable_new_version(settings):
    store = Store(settings.database)
    job = store.submit("terms", {"pending": []}, "original-terms")
    claim = store.claim(100, 3)
    store.finish(
        job["id"], claim["token"], "completed", {"groups": {"鸡翅尖": ["鸡翼尖"]}, "synthetic": False}
    )
    updated = store.curate_terms(job["id"], {"鸡翼尖": ["鸡翅尖"]}, "专家调整主词", "expert", "curate-key")
    assert updated["id"] != job["id"]
    assert store.get(job["id"])["result"]["groups"] == {"鸡翅尖": ["鸡翼尖"]}
    assert store.events(updated["id"])[0]["data"]["parent_task"] == job["id"]
    assert (
        store.curate_terms(job["id"], {"鸡翼尖": ["鸡翅尖"]}, "专家调整主词", "expert", "curate-key")["id"]
        == updated["id"]
    )


def test_pdf_whitespace_canonicalization_never_accepts_changed_content(settings):
    from tariff_agent.domain import Citation, Ranking
    from tariff_agent.knowledge import EvidenceError, Knowledge

    k = Knowledge(settings.corpus, True)
    value = Ranking(codes=["9901"], citations=[Citation(source_id="s9901", quote="测试 塑料 部件")])
    k.validate_ranking(value, ["9901"])
    assert value.citations[0].quote == "测试塑料部件"
    with pytest.raises(EvidenceError):
        k.validate_ranking(
            Ranking(codes=["9901"], citations=[Citation(source_id="s9901", quote="测试金属部件")]), ["9901"]
        )


def test_original_evidence_download_is_authenticated(settings, tmp_path):
    settings.notes_pdf = tmp_path / "test.pdf"
    settings.notes_pdf.write_bytes(b"%PDF-1.4\nsynthetic auth-test fixture")
    with TestClient(create_app(settings)) as c:
        assert c.get("/v1/knowledge/documents/notes2022.pdf").status_code == 401
        response = c.get("/v1/knowledge/documents/notes2022.pdf", headers={"X-API-Key": "a" * 32})
        assert response.status_code == 200 and response.content.startswith(b"%PDF")
