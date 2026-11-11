import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_engineering import FakeModels

from tariff_agent.api import create_app
from tariff_agent.store import Store
from tariff_agent.worker import process_one


def test_risk_admission_remains_available_when_hybrid_inference_is_down(settings, monkeypatch):
    settings.strict_readiness = True
    settings.deepseek_api_key = SecretStr("test-not-a-real-key")

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("No test gateway")

    monkeypatch.setattr(httpx, "get", unavailable)
    with TestClient(create_app(settings)) as client:
        headers = {"X-API-Key": "a" * 32, "Idempotency-Key": "hybrid-not-ready"}
        assert client.post("/v1/cases", headers=headers, json={"description": "测试商品"}).status_code == 202
        headers["Idempotency-Key"] = "explicit-diagnostic"
        response = client.post(
            "/v1/cases",
            headers=headers,
            json={"description": "测试商品", "retrieval_profile": "bm25_diagnostic"},
        )
        assert response.status_code == 202
        assert response.json()["payload"]["retrieval_profile"] == "bm25_diagnostic"


def test_case_replay_survives_dependency_outage_and_schema_defaults(settings):
    store = Store(settings.database)
    job = store.submit("case", {"description": "测试商品"}, "compatible-case-replay")
    settings.strict_readiness = True
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/cases",
            headers={"X-API-Key": "a" * 32, "Idempotency-Key": "compatible-case-replay"},
            json={"description": "测试商品"},
        )
        assert response.status_code == 202 and response.json()["id"] == job["id"]


def test_diagnostic_execution_keeps_deployed_hybrid_settings(settings, monkeypatch):
    from types import SimpleNamespace

    import tariff_agent.retrieval as history

    monkeypatch.setattr(
        history, "History", lambda s: SimpleNamespace(search=lambda *args, **kwargs: [], close=lambda: None)
    )
    settings.dense_enabled = True
    settings.rerank_enabled = True
    settings.history_enabled = True
    settings.deepseek_enabled = False
    store = Store(settings.database)
    job = store.submit(
        "case",
        {
            "description": "测试塑料部件",
            "declared_code": "990101",
            "target_digits": 6,
            "elements": [],
            "retrieval_profile": "bm25_diagnostic",
        },
        "profile-isolation",
    )
    process_one(settings, FakeModels())
    result = store.get(job["id"])
    assert result["status"] == "awaiting_review"
    assert result["result"]["retrieval_profile"] == "bm25_diagnostic"
    assert settings.dense_enabled and settings.rerank_enabled and settings.history_enabled
