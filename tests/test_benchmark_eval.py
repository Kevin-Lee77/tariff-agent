import json

import pytest

from tariff_agent.agent_evaluation import trajectory_score
from tariff_agent.benchmark_data import group_identity, prepare, rows, text_identity
from tariff_agent.evaluation import score


def test_grouping_preserves_negation_and_isolates_numeric_variants():
    assert group_identity("品名：冷冻牛肉；规格型号：10kg") == group_identity(
        "商品名称:冷冻牛肉；规格型号：20kg"
    )
    assert group_identity("含糖牛奶") != group_identity("不含糖牛奶")
    assert text_identity("品名：ABC；规格型号：10 kg") == text_identity("商品名称: abc 规格型号:10kg")


def test_grouped_holdout_is_disjoint_and_immutable(tmp_path):
    source = tmp_path / "cases.jsonl"

    def word(i):
        return chr(0x4E00 + i) + chr(0x5200 + i)

    records = [
        {"description": "商品" + word(i), "gold_code": "9901010101", "source": "synthetic-test"}
        for i in range(300)
    ]
    records += [
        {"description": "品名：同一产品123", "gold_code": "9901010101", "source": "synthetic-test"},
        {"description": "商品名称：同一产品456", "gold_code": "9901010101", "source": "synthetic-test"},
    ]
    source.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({"entries": [{"code": "9901010101"}]}), encoding="utf-8")
    output = tmp_path / "benchmark"
    manifest = prepare(source, corpus, output, 5, 10)
    assert manifest["counts"]["heldout"] == 10 and manifest["group_overlap"] == 0
    groups = {
        name: {r["group_id"] for r in rows(output / (name + ".jsonl"))}
        for name in ("history", "dev_pool", "heldout_pool")
    }
    assert not groups["history"] & groups["dev_pool"] and not groups["history"] & groups["heldout_pool"]
    with pytest.raises(FileExistsError):
        prepare(source, corpus, output, 5, 10)


def test_failed_cases_remain_in_denominators_and_ten_digit_metrics():
    report = score(
        [
            {"gold_code": "9901010101", "predicted_codes": ["9901010101"], "retrieved": ["9901"]},
            {"gold_code": "9901010101", "predicted_codes": [], "status": "failed", "retrieved": []},
        ]
    )
    assert report["top1_10digit"]["accuracy"] == 0.5
    assert report["heading_recall@40"]["value"] == 0.5
    assert report["failures"] == 1


def test_agentevals_detects_risk_bypass_and_correct_termination():
    pytest.importorskip("agentevals")
    result = {"risk_check": {"decision": "blocked"}, "status": "risk_blocked"}
    assert trajectory_score([{"stage": "risk_precheck"}], result)["score"] is True
    assert trajectory_score([{"stage": "risk_precheck"}, {"stage": "attributes"}], result)["score"] is False
    clear = {"risk_check": {"decision": "clear"}, "normalization": {"status": "processed"}}
    assert (
        trajectory_score([{"stage": "risk_precheck"}, {"stage": "normalize"}], clear, "governance_component")[
            "score"
        ]
        is True
    )


def test_component_evaluation_does_not_expose_gold_as_input(settings):
    from test_engineering import FakeModels

    from tariff_agent.domain import Feedback
    from tariff_agent.knowledge import Knowledge
    from tariff_agent.store import Conflict, Store
    from tariff_agent.workflow import Workflow

    settings.deepseek_enabled = False

    class Capture(FakeModels):
        def structured(self, stage, payload, schema, teacher=False):
            if stage == "extract_attributes":
                assert not payload.get("declared_code") and "gold_code" not in payload
            return super().structured(stage, payload, schema, teacher)

    store = Store(settings.database)
    task = store.submit(
        "case", {"description": "测试塑料部件", "target_digits": 6}, "isolated-component-eval"
    )
    lease = store.claim(100, 3)
    result = Workflow(Knowledge(settings.corpus, True), Capture(), store, settings).run(
        lease, "classification_component"
    )
    store.finish(task["id"], lease["token"], result["status"], result)
    assert result["votes"]["top_code"] == "990101"
    assert "risk_precheck" not in [e["stage"] for e in store.events(task["id"])]
    with pytest.raises(Conflict):
        store.review(
            task["id"],
            Feedback(approved=True, final_code="990101", reason="不能当业务确认", reviewer="test"),
            {"990101"},
        )


def test_incomplete_qdrant_index_stops_before_generation(settings, monkeypatch):
    from pydantic import SecretStr

    import tariff_agent.history as history
    from tariff_agent.domain import CaseInput
    from tariff_agent.store import Store
    from tariff_agent.worker import process_one

    settings.strict_readiness = True
    settings.deepseek_api_key = SecretStr("not-a-real-provider-key")
    settings.qdrant_catalog_enabled = True
    monkeypatch.setattr(history, "retrieval_ready", lambda *args, **kwargs: False)

    class NoModel:
        def structured(self, *args, **kwargs):
            pytest.fail("Model must not run before the index is ready")

    store = Store(settings.database)
    case = CaseInput(description="测试塑料部件", declared_code="990101", retrieval_profile="bm25_diagnostic")
    task = store.submit("case", case.model_dump(), "qdrant-preflight")
    process_one(settings, NoModel())
    assert store.get(task["id"])["status"] == "awaiting_models"


def test_candidate_scope_correction_is_bounded(settings):
    from test_engineering import FakeModels

    from tariff_agent.clients import ModelError
    from tariff_agent.domain import Ranking
    from tariff_agent.knowledge import Knowledge
    from tariff_agent.store import Store
    from tariff_agent.workflow import Workflow

    class WrongFirst(FakeModels):
        calls = 0

        def structured(self, stage, payload, schema, teacher=False):
            self.calls += 1
            if self.calls == 1:
                return Ranking(codes=["990101"])
            assert "allowed_codes" in payload["validation_feedback"]
            return super().structured(stage, payload, schema, teacher)

    model = WrongFirst()
    state = {"case": {"description": "测试塑料部件"}, "attributes": {}}
    workflow = Workflow(Knowledge(settings.corpus, True), model, Store(settings.database), settings)
    assert workflow.rank("l1_heading_shortlist", ["9901"], state).codes == ["9901"]
    assert model.calls == 2

    class AlwaysWrong:
        def structured(self, *args, **kwargs):
            return Ranking(codes=["990101"])

    workflow.models = AlwaysWrong()
    with pytest.raises(ModelError, match="bounded correction"):
        workflow.rank("l1_heading_shortlist", ["9901"], state)


def test_dense_index_resumes_only_completed_batches(settings, tmp_path):
    import numpy as np
    from qdrant_client import QdrantClient

    from tariff_agent.history import History

    settings.history_directory = tmp_path / "history"
    settings.embedding_dimensions = 3
    settings.embedding_model = "test-encoder"
    source = tmp_path / "history.jsonl"
    source.write_text(
        "\n".join(
            json.dumps({"description": "历史商品" + chr(0x4E00 + i), "gold_code": "990101", "source": "test"})
            for i in range(33)
        ),
        encoding="utf-8",
    )
    index = History(settings, QdrantClient(":memory:"))
    index.build(source, [], valid_codes={"990101"})
    calls = []

    def fail_second(texts):
        calls.append(len(texts))
        if len(calls) == 2:
            raise RuntimeError("Simulated inference interruption")
        return np.ones((len(texts), 3))

    with pytest.raises(RuntimeError):
        index.attach_dense(fail_second)
    assert index.manifest["dense_ready"] is False
    resumed = []

    def encoder(texts):
        resumed.extend(texts)
        return np.ones((len(texts), 3))

    index.attach_dense(encoder)
    assert len(resumed) == 17 and index.manifest["dense_count"] == 33


def test_history_excludes_original_case_even_after_attribute_query_expansion(settings, tmp_path):
    from qdrant_client import QdrantClient

    from tariff_agent.history import History

    settings.history_directory = tmp_path / "index"
    source = tmp_path / "cases.jsonl"
    source.write_text(
        "\n".join(
            json.dumps({"description": text, "gold_code": "990101", "source": "test"}, ensure_ascii=False)
            for text in ["冷冻牛肉", "冷藏牛肉"]
        ),
        encoding="utf-8",
    )
    history = History(settings, QdrantClient(":memory:"))
    history.build(source, [], valid_codes={"990101"})
    result = history.search("冷冻牛肉 用途食用", exclude_description="品名：冷冻牛肉")
    assert result and all(row["description"] != "冷冻牛肉" for row in result)
