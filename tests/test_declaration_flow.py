import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from test_engineering import FakeModels
from werkzeug.serving import make_server

from tariff_agent.api import create_app
from tariff_agent.declaration import language_check, source_check
from tariff_agent.domain import CaseInput, Feedback
from tariff_agent.flask_services import create_service
from tariff_agent.governance_tools import GovernanceTools
from tariff_agent.store import Conflict, Store
from tariff_agent.worker import process_one


class NeverModels:
    def structured(self, *args, **kwargs):
        pytest.fail("Blocked declaration must never invoke a classification model")


def test_risk_blocks_before_language_and_classification(settings):
    store = Store(settings.database)
    task = store.submit(
        "case",
        {"description": "税号：0202300090；品名：变质牛肉", "governance_profile": "sandbox"},
        "blocked-case",
    )
    assert process_one(settings, NeverModels())
    row = store.get(task["id"])
    assert row["status"] == "risk_blocked"
    assert [e["stage"] for e in store.events(task["id"])] == ["risk_precheck", "finished"]
    risk = row["result"]["risk_check"]
    assert risk["decision"] == "blocked"
    assert risk["annotations"][0]["text"] == "变质"
    assert "normalization" not in row["result"] and "votes" not in row["result"]
    with pytest.raises(Conflict):
        store.review(
            task["id"],
            Feedback(approved=True, final_code="990101", reason="不能越过风险", reviewer="test"),
            {"990101"},
        )


@pytest.mark.parametrize(
    "text,code,expected",
    [("冷冻牛肉", None, "information_required"), ("冷冻牛肉", "390100", "review_required")],
)
def test_no_code_or_no_coverage_never_passes(settings, text, code, expected):
    result = source_check(
        CaseInput(description=text, declared_code=code, governance_profile="sandbox"), settings
    )
    assert result["decision"] == expected


def test_unreviewed_dictionary_and_missing_vectors_do_not_pass(settings):
    data = json.loads(settings.risk_rules.read_text())
    data["approved"] = False
    settings.risk_rules.write_text(json.dumps(data), encoding="utf-8")
    case = CaseInput(description="普通部件", declared_code="990101")
    assert source_check(case, settings)["decision"] == "review_required"
    data.update(approved=True, matching="word2vec")
    settings.risk_rules.write_text(json.dumps(data), encoding="utf-8")
    assert source_check(case, settings)["decision"] == "review_required"


def test_raw_element_mapping_before_any_synonym_replacement(settings):
    data = json.loads(settings.risk_rules.read_text())
    data.update(
        element_names={"保存方法": "STORAGE"},
        coverage=[{"prefix": "99", "elements": ["STORAGE"]}],
        rules=[{"knowledge_id": "r", "fit_area": "99", "element_code": "STORAGE", "vocabulary": "腐败"}],
    )
    settings.risk_rules.write_text(json.dumps(data), encoding="utf-8")
    result = source_check(CaseInput(description="税号：990101；保存方法：腐败", declared_code=None), settings)
    assert result["decision"] == "blocked" and result["hits"][0]["element_value"] == "腐败"
    assert (
        source_check(CaseInput(description="税号：990101；品名：部件"), settings)["decision"]
        == "information_required"
    )


def test_language_preserves_negation_and_independent_words(settings):
    result = language_check(
        CaseInput(description="冻制剔骨牛肉，不含冻制添加剂；不锈钢", governance_profile="sandbox"), settings
    )
    assert result["normalized_text"] == "冷冻去骨牛肉，不含冻制添加剂；不锈钢"
    assert [r["before"] for r in result["changes"]] == ["冻制", "剔骨"]
    assert any(r["text"] == "不含冻制添加剂" for r in result["protected_spans"])
    assert (
        language_check(CaseInput(description="冷藏和冷冻牛肉", governance_profile="sandbox"), settings)[
            "status"
        ]
        == "review_required"
    )


@pytest.mark.parametrize("field", ["批次", "订单号", "SKU", "taskId", "税号", "报关单号"])
def test_terminology_never_changes_business_identifiers(settings, field):
    settings.language_rules.write_text(
        json.dumps(
            {"version": "identifier-regression", "groups": {"3+": ["3"], "冷冻": ["冻制"]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    text = f"商品名称：冻制牛肉；{field}：ABC3130"
    result = language_check(CaseInput(description=text), settings)
    assert result["normalized_text"] == f"商品名称：冷冻牛肉；{field}：ABC3130"
    assert all(change["before"] != "3" for change in result["changes"])


def test_clear_flow_traverses_to_ten_digits(settings):
    data = json.loads(settings.corpus.read_text())
    data["entries"] += [
        {"code": "99010101", "parent": "990101", "text": "八位测试", "source_id": "s8"},
        {"code": "9901010101", "parent": "99010101", "text": "十位测试", "source_id": "s10"},
    ]
    settings.corpus.write_text(json.dumps(data), encoding="utf-8")
    store = Store(settings.database)
    task = store.submit("case", {"description": "测试塑料部件", "declared_code": "9901010101"}, "ten-digits")
    process_one(settings, FakeModels())
    result = store.get(task["id"])
    assert result["status"] == "awaiting_review"
    assert result["result"]["votes"]["top_code"] == "9901010101"
    assert set(result["result"]["votes"]["levels"]) == {"2", "4", "6", "8", "10"}
    assert [
        e["stage"]
        for e in store.events(task["id"])
        if e["stage"] in ("risk_precheck", "normalize", "attributes")
    ][:3] == ["risk_precheck", "normalize", "attributes"]


def test_independent_flask_services_and_http_agent_tools(settings):
    servers = []
    for kind in ("source", "language"):
        app = create_service(kind, settings)
        with app.test_client() as client:
            endpoint = "/v1/declarations/check" if kind == "source" else "/v1/declarations/normalize"
            assert client.post(endpoint, json={}).status_code == 401
            assert client.post(endpoint, json={}, headers={"X-API-Key": "a" * 32}).status_code == 422
        server = make_server("127.0.0.1", 0, app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
    try:
        settings.governance_transport = "http"
        settings.tax_source_url = f"http://127.0.0.1:{servers[0][0].server_port}"
        settings.tax_language_url = f"http://127.0.0.1:{servers[1][0].server_port}"
        tools = GovernanceTools(settings)
        case = CaseInput(
            description="冻制牛肉，不含糖", declared_code="0202300090", governance_profile="sandbox"
        )
        assert tools.tax_source_risk_check(case)["decision"] == "clear"
        assert tools.tax_language_normalize(case)["normalized_text"] == "冷冻牛肉，不含糖"
    finally:
        for server, thread in servers:
            server.shutdown()
            thread.join(timeout=3)


def test_enterprise_feedback_and_corrected_resubmission(settings):
    headers = {"X-API-Key": "a" * 32, "Idempotency-Key": "risk-feedback-test"}
    with TestClient(create_app(settings)) as client:
        payload = {"description": "变质牛肉", "declared_code": "0202300090", "governance_profile": "sandbox"}
        first = client.post("/v1/cases", headers=headers, json=payload).json()
        process_one(settings, NeverModels())
        report = client.get(f"/v1/cases/{first['id']}/enterprise-feedback", headers=headers)
        assert report.status_code == 200 and report.json()["risk_report"]["decision"] == "blocked"
        assert "attachment" in report.headers["content-disposition"]
        revised = client.post(
            f"/v1/cases/{first['id']}/resubmissions",
            headers=headers,
            json=payload | {"description": "冷冻牛肉"},
        )
        assert revised.status_code == 202 and revised.json()["id"] != first["id"]
        assert client.get("/v1/tasks/" + first["id"], headers=headers).json()["status"] == "risk_blocked"
        assert Store(settings.database).events(revised.json()["id"])[0]["data"]["parent_task"] == first["id"]


def test_unavailable_flask_does_not_fall_through_to_models(settings, monkeypatch):
    settings.governance_transport = "http"

    def down(*args, **kwargs):
        raise httpx.ConnectError("test unavailable")

    monkeypatch.setattr(httpx, "post", down)
    store = Store(settings.database)
    task = store.submit("case", {"description": "普通商品"}, "unavailable-service")
    process_one(settings, NeverModels())
    assert store.get(task["id"])["status"] == "risk_review_required"
