from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from fastapi.testclient import TestClient

from tariff_agent.api import create_app
from tariff_agent.domain import Attributes, Citation, Element, Feedback, Ranking
from tariff_agent.evaluation import score
from tariff_agent.governance import (
    RiskRequest,
    TermRequest,
    check_standard,
    negative,
    normalize_terms,
    risk_check,
)
from tariff_agent.knowledge import Knowledge
from tariff_agent.store import Conflict, Store
from tariff_agent.worker import process_one


def test_auth_and_idempotency(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.post("/v1/cases", json={"description": "商品"}).status_code == 401
        headers = {"X-API-Key": "a" * 32, "Idempotency-Key": "same-case-001"}
        first = client.post("/v1/cases", headers=headers, json={"description": "塑料部件"})
        assert first.status_code == 202
        assert (
            client.post("/v1/cases", headers=headers, json={"description": "塑料部件"}).json()["id"]
            == first.json()["id"]
        )
        assert client.post("/v1/cases", headers=headers, json={"description": "其他部件"}).status_code == 409
        assert client.get("/v1/tasks/not-found", headers=headers).status_code == 404


def test_sql_input_rejected(settings):
    with TestClient(create_app(settings)) as client:
        r = client.post(
            "/v1/governance/terms/jobs",
            headers={"X-API-Key": "a" * 32, "Idempotency-Key": "unsafe-001"},
            json={"pending": [], "params": "DROP TABLE jobs"},
        )
        assert r.status_code == 422


def test_fixture_not_production(settings):
    with pytest.raises(ValueError, match="Test corpus"):
        Knowledge(settings.corpus)


def test_no_corpus_no_predictions(settings):
    settings.corpus.unlink()
    with TestClient(create_app(settings)) as c:
        assert (
            c.post(
                "/v1/cases",
                headers={"X-API-Key": "a" * 32, "Idempotency-Key": "empty-001"},
                json={"description": "商品"},
            ).status_code
            == 503
        )


def test_queue_exclusive_claim(settings):
    store = Store(settings.database)
    store.submit("case", {"description": "商品"}, "exclusive")
    with ThreadPoolExecutor(max_workers=5) as pool:
        claims = list(pool.map(lambda _: Store(settings.database).claim(100, 3), range(5)))
    assert sum(x is not None for x in claims) == 1


def test_expired_lease_cannot_overwrite(settings):
    store = Store(settings.database)
    job = store.submit("case", {}, "lease-test")
    old = store.claim(-1, 3)
    new = store.claim(100, 3)
    with pytest.raises(Conflict):
        store.finish(job["id"], old["token"], "completed", {})
    store.finish(job["id"], new["token"], "completed", {})


def test_retry_budget(settings):
    store = Store(settings.database)
    job = store.submit("case", {}, "retry-test")
    store.claim(-1, 1)
    assert store.claim(10, 1) is None
    assert store.get(job["id"])["status"] == "failed"
    with pytest.raises(Conflict):
        store.retry(job["id"], 1)


def test_terms_keep_independent_and_polarity():
    req = TermRequest(pending=["含糖", "不含糖", "不锈钢"])
    result = normalize_terms(req, lambda texts: np.eye(len(texts)))
    assert set(result["independent_words"]) == set(req.pending)
    assert negative("不含糖") and not negative("不锈钢")


def test_terms_no_mutation():
    req = TermRequest(known={"鸡翅尖": ["鸡翼尖"]}, pending=["鸡翅尖"])
    normalize_terms(req, lambda texts: np.eye(len(texts)))
    assert req.known == {"鸡翅尖": ["鸡翼尖"]}


def test_standard_not_covered():
    assert check_standard([], "01", {})["status"] == "not_covered"
    r = check_standard([Element(name="材质", value="未知")], "01", {"01": {"材质": ["塑料"]}})
    assert r["issues"][0]["reason"] == "non_standard_value"


class VectorModel:
    wv = {"塑料": np.array([1.0, 0.0]), "金属": np.array([0.0, 1.0])}


def test_risk_filters_scope_and_element():
    req = RiskRequest(
        code="390100",
        code_origin="candidate",
        elements=[Element(code="M", name="材质", value="塑料")],
        rules=[
            {"knowledge_id": "x", "fit_area": "39", "element_code": "M", "vocabulary": "塑料"},
            {"knowledge_id": "wrong-scope", "fit_area": "40", "element_code": "M", "vocabulary": "塑料"},
            {"knowledge_id": "wrong-element", "fit_area": "39", "element_code": "U", "vocabulary": "塑料"},
        ],
    )
    r = risk_check(req, VectorModel())
    assert [h["knowledge_id"] for h in r["hits"]] == ["x"]
    assert r["code_origin"] == "candidate"


def test_risk_oov_unknown():
    req = RiskRequest(
        code="390100",
        code_origin="declared",
        elements=[Element(code="M", name="材质", value="未登录词")],
        rules=[{"knowledge_id": "x", "fit_area": "39", "element_code": "M", "vocabulary": "塑料"}],
    )
    r = risk_check(req, VectorModel())
    assert r["status"] == "not_covered" and r["unknown"]


def test_citations_and_illegal_codes(settings):
    k = Knowledge(settings.corpus, True)
    with pytest.raises(ValueError):
        k.validate_ranking(Ranking(codes=["000000"]), ["9901"])
    with pytest.raises(ValueError):
        k.validate_ranking(
            Ranking(codes=["9901"], citations=[Citation(source_id="s9901", quote="编造原文")]), ["9901"]
        )
    assert k.path("990101") == ["99", "9901", "990101"]


class FakeModels:
    def structured(self, stage, payload, schema, teacher=False):
        if schema is Attributes:
            return Attributes(material="塑料", use="工业", form="部件")
        row = payload["candidates"][0]
        return Ranking(
            codes=[row["code"]], citations=[Citation(source_id=row["source_id"], quote=row["text"])]
        )


def test_six_stages_and_expert_only_export(settings):
    store = Store(settings.database)
    job = store.submit(
        "case",
        {"description": "测试塑料部件", "elements": [], "declared_code": "990101", "target_digits": 6},
        "flow-test",
    )
    assert process_one(settings, FakeModels())
    current = store.get(job["id"])
    assert current["status"] == "awaiting_review"
    assert [
        e["stage"]
        for e in store.events(job["id"])
        if e["stage"]
        in ("risk_precheck", "normalize", "attributes", "retrieve", "l1", "l2", "subheading", "verify")
    ][:8] == [
        "risk_precheck",
        "normalize",
        "attributes",
        "retrieve",
        "l1",
        "l2",
        "subheading",
        "verify",
    ]
    assert not store.approved_samples()
    store.review(
        job["id"],
        Feedback(approved=True, final_code="990101", reason="专家测试确认", reviewer="test"),
        {"990101"},
    )
    assert len(store.approved_samples()) == 1
    with pytest.raises(Conflict):
        store.review(
            job["id"],
            Feedback(approved=True, final_code="990101", reason="重复审核", reviewer="test"),
            {"990101"},
        )


def test_provider_failure_not_success(settings):
    class Broken:
        def structured(self, *args, **kwargs):
            raise TimeoutError()

    store = Store(settings.database)
    job = store.submit("case", {"description": "测试商品", "declared_code": "990101"}, "timeout-test")
    process_one(settings, Broken())
    assert store.get(job["id"])["status"] == "failed"


def test_clarification_resume(settings):
    store = Store(settings.database)
    job = store.submit("case", {"description": "测试商品", "elements": []}, "clarify-test")
    lease = store.claim(100, 3)
    store.finish(job["id"], lease["token"], "awaiting_information", {})
    store.clarify(job["id"], [Element(name="用途", value="工业")])
    assert store.get(job["id"])["status"] == "queued"
    assert store.get(job["id"])["payload"]["elements"][0]["value"] == "工业"
    assert (
        store.submit("case", {"description": "测试商品", "elements": []}, "clarify-test")["id"] == job["id"]
    )


def test_readiness_is_not_fake_success(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready", headers={"X-API-Key": "a" * 32})
        assert response.status_code == 503
        assert response.json()["checks"]["deepseek_key_present"] is False


def test_published_terms_only_from_completed_proposals(settings):
    store = Store(settings.database)
    job = store.submit("terms", {}, "publish-terms")
    with pytest.raises(Conflict):
        store.publish_terms(job["id"])
    owned = store.claim(100, 3)
    store.finish(job["id"], owned["token"], "completed", {"groups": {"鸡翅尖": ["鸡翼尖"]}})
    store.publish_terms(job["id"])
    assert store.terms()["published_from"] == job["id"]


def test_dense_cache_reuse_and_model_isolation(settings, tmp_path):
    first = Knowledge(settings.corpus, True)
    first.build_dense(lambda texts: np.array([[1.0, 0.0]]), tmp_path, "encoder-A")
    second = Knowledge(settings.corpus, True)

    def broken(_):
        raise AssertionError("Cache should avoid re-encoding")

    second.build_dense(broken, tmp_path, "encoder-A")
    assert second.dense.tolist() == [[1.0, 0.0]]
    with pytest.raises(AssertionError):
        second.build_dense(broken, tmp_path, "encoder-B")


def test_evaluation_keeps_failed_cases():
    r = score(
        [
            {"gold_code": "990101", "predicted_codes": []},
            {"gold_code": "990101", "predicted_codes": ["990101"]},
        ]
    )
    assert r["top1_6digit"] == {"hits": 1, "n": 2, "accuracy": 0.5}


def test_structural_group_cannot_be_skipped_or_approved(settings):
    import json

    data = json.loads(settings.corpus.read_text(encoding="utf-8"))
    data["groups"] = [{"code": "99010", "parent": "9901", "text": "测试同级分组", "source_id": "group99010"}]
    settings.corpus.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    k = Knowledge(settings.corpus, True)
    assert k.children("9901") == ["99010"]
    assert k.path("990101") == ["99", "9901", "99010", "990101"]
    assert "99010" not in k.formal_codes
    with pytest.raises(ValueError):
        Feedback(approved=True, final_code="99010", reason="结构节点禁止定案", reviewer="test")
    store = Store(settings.database)
    job = store.submit(
        "case",
        {"description": "测试塑料部件", "elements": [], "declared_code": "990101", "target_digits": 6},
        "group-flow",
    )
    process_one(settings, FakeModels())
    assert store.get(job["id"])["result"]["votes"]["top_code"] == "990101"


def test_bad_quotes_are_removed_and_escalated_to_review(settings):
    class BadQuotes(FakeModels):
        def structured(self, stage, payload, schema, teacher=False):
            result = super().structured(stage, payload, schema, teacher)
            if isinstance(result, Ranking):
                result.citations = [
                    Citation(source_id=payload["candidates"][0]["source_id"], quote="编造的引文")
                ]
            return result

    settings.deepseek_enabled = False
    store = Store(settings.database)
    job = store.submit(
        "case",
        {"description": "测试塑料部件", "elements": [], "declared_code": "990101", "target_digits": 6},
        "bad-quote-review",
    )
    process_one(settings, BadQuotes())
    result = store.get(job["id"])
    assert result["status"] == "awaiting_review"
    assert result["result"]["citations"] == []
    assert "unverifiable_citation" in result["result"]["conflicts"]


def test_shared_evidence_is_deduplicated_and_bounded(settings):
    from tariff_agent.domain import Note
    from tariff_agent.workflow import Workflow

    k = Knowledge(settings.corpus, True)
    k.corpus.general_notes = [Note(id=f"g{i}", text="测试依据" * 200) for i in range(10)]
    k.sources.update({n.id: n.text for n in k.corpus.general_notes})

    class Capture(FakeModels):
        def structured(self, stage, payload, schema, teacher=False):
            assert sum(len(n["text"]) for n in payload["evidence"]) <= 2000
            ids = [n["id"] for n in payload["evidence"]]
            assert len(ids) == len(set(ids))
            return super().structured(stage, payload, schema, teacher)

    settings.evidence_budget_chars = 2000
    Workflow(k, Capture(), Store(settings.database), settings).rank(
        "l2_note_confirmation",
        ["9901", "990101"],
        {"case": {"description": "测试依据"}, "attributes": {}},
        notes=True,
    )
