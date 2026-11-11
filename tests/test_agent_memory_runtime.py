import json
import time
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from test_engineering import FakeModels

from tariff_agent.agent_evaluation import inspect_run
from tariff_agent.agent_memory import AgentMemory
from tariff_agent.agent_telemetry import span, task_trace
from tariff_agent.api import create_app
from tariff_agent.clients import Models
from tariff_agent.domain import Attributes, Element, Feedback
from tariff_agent.durable import GuardedSaver
from tariff_agent.knowledge import Knowledge
from tariff_agent.persistence import agent_spans, expert_memories
from tariff_agent.runtime_context import input_revision, versions
from tariff_agent.store import Conflict, Store
from tariff_agent.worker import process_one


def submit(store, text="测试塑料专用部件", idem="memory-case"):
    return store.submit(
        "case", {"description": text, "declared_code": "990101", "target_digits": 6, "elements": []}, idem
    )


class FailOnce(FakeModels):
    def __init__(self):
        self.failed = False
        self.calls = Counter()

    def structured(self, stage, payload, schema, teacher=False):
        self.calls[stage] += 1
        if stage == "l2_note_confirmation" and not self.failed:
            self.failed = True
            raise RuntimeError("injected test failure before L2")
        return super().structured(stage, payload, schema, teacher)


def test_native_checkpoint_recovers_without_repeating_models_and_rechecks_risk(settings, monkeypatch):
    from tariff_agent.governance_tools import GovernanceTools

    original = GovernanceTools.tax_source_risk_check
    checks = []

    def check(self, case):
        checks.append(case.description)
        return original(self, case)

    monkeypatch.setattr(GovernanceTools, "tax_source_risk_check", check)
    store = Store(settings.database)
    job = submit(store)
    model = FailOnce()
    assert process_one(settings, model) and store.get(job["id"])["status"] == "failed"
    store.retry(job["id"], 3)
    assert process_one(settings, model)
    done = store.get(job["id"])
    assert done["status"] == "awaiting_review"
    assert model.calls["extract_attributes"] == 1 and model.calls["l1_heading_shortlist"] == 1
    assert model.calls["l2_note_confirmation"] == 2 and len(checks) == 2
    events = store.events(job["id"])
    assert any(e["stage"] == "checkpoint_resume" and e["data"]["preflight_rechecked"] for e in events)
    result = inspect_run(done["result"], events, Knowledge(settings.corpus, True))
    assert result["trace"]["score"]
    memory = AgentMemory(store, settings).case_memory(job["id"])
    assert len(memory["runs"]) == 1 and memory["runs"][0]["state"]["confirmed_elements"] == []
    assert settings.database.with_name("app.checkpoints.db").is_file()


def test_risk_change_blocks_old_checkpoint_recovery(settings):
    store = Store(settings.database)
    job = submit(store)
    model = FailOnce()
    process_one(settings, model)
    rules = json.loads(settings.risk_rules.read_text())
    rules["rules"][0]["vocabulary"] = "塑料"
    settings.risk_rules.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    store.retry(job["id"], 3)
    process_one(settings, model)
    result = store.get(job["id"])
    assert result["status"] == "risk_blocked" and "votes" not in result["result"]
    assert model.calls["l2_note_confirmation"] == 1
    assert len(AgentMemory(store, settings).case_memory(job["id"])["runs"]) == 2


def test_checkpoint_writer_is_fenced_after_cancellation(settings):
    class Saver:
        serde = None

        def put(self, *args):
            pytest.fail("Cancelled worker wrote a checkpoint")

    store = Store(settings.database)
    job = submit(store)
    lease = store.claim(100, 3)
    saver = GuardedSaver(Saver(), store, lease)
    store.cancel(job["id"])
    with pytest.raises(Conflict):
        saver.put({}, {}, {}, {})


def test_clarification_creates_new_input_version_and_preserves_only_submitted_facts(settings):
    class Ask(FakeModels):
        def structured(self, stage, payload, schema, teacher=False):
            if schema is Attributes and not payload.get("elements"):
                return Attributes(missing=["用途"])
            return super().structured(stage, payload, schema, teacher)

    store = Store(settings.database)
    job = submit(store)
    process_one(settings, Ask())
    old = store.get(job["id"])
    assert old["status"] == "awaiting_information"
    with pytest.raises(Conflict, match="revision"):
        store.clarify(job["id"], [Element(name="用途", value="工业")], "0" * 64)
    store.clarify(job["id"], [Element(name="用途", value="工业")], old["revision"])
    process_one(settings, Ask())
    new = store.get(job["id"])
    assert new["revision"] != old["revision"] and new["status"] == "awaiting_review"
    memo = AgentMemory(store, settings).case_memory(job["id"])
    assert memo["confirmed_elements"] == [{"code": "", "name": "用途", "value": "工业"}]
    assert len(memo["runs"]) == 2
    assert memo["runs"][0]["state"]["confirmed_elements"] == memo["confirmed_elements"]
    assert memo["runs"][1]["state"]["pending_questions"] == ["请补充商品的用途"]


def approved(store, settings, text="测试塑料专用部件", idem="approved-memory"):
    job = submit(store, text, idem)
    process_one(settings, FakeModels())
    current = store.get(job["id"])
    store.review(
        job["id"],
        Feedback(
            approved=True,
            final_code="990101",
            reason="专家核对确认分类",
            reviewer="expert",
            expected_revision=current["revision"],
        ),
        {"990101"},
    )
    return store.get(job["id"])


def test_expert_memory_explicit_promotion_reuse_and_revocation(settings):
    store = Store(settings.database)
    job = approved(store, settings)
    memory = AgentMemory(store, settings)
    version = versions(settings, store)["domain_version"]
    assert memory.recall(job["payload"]["description"], version, {"9901"}, "another-job") == []
    published = memory.publish(job["id"], job["revision"], "expert", Knowledge(settings.corpus, True))
    assert published["status"] == "published"
    assert memory.publish(job["id"], job["revision"], "expert", Knowledge(settings.corpus, True))["replayed"]
    recalled = memory.recall(job["payload"]["description"], version, {"9901"}, "another-job")
    assert [r["memory_id"] for r in recalled] == [published["id"]]
    assert recalled[0]["authority"] == "reviewed_expert_reference_only_not_law"
    assert memory.recall(job["payload"]["description"], version, {"9901"}, job["id"]) == []
    assert memory.recall(job["payload"]["description"], version, {"0101"}, "another-job") == []
    old_epoch = memory.epoch(version)
    memory.revoke(published["id"], "专家决定撤销", "expert")
    assert (
        memory.epoch(version) != old_epoch
        and memory.recall(job["payload"]["description"], version, {"9901"}, "another-job") == []
    )
    assert (
        memory.publish(job["id"], job["revision"], "expert", Knowledge(settings.corpus, True))["status"]
        == "revoked"
    )


def test_expert_memory_expiry_scope_and_negation(settings, tmp_path):
    store = Store(settings.database)
    job = approved(store, settings, "测试含糖牛奶")
    memory = AgentMemory(store, settings)
    memory.publish(job["id"], job["revision"], "expert", Knowledge(settings.corpus, True))
    version = versions(settings, store)["domain_version"]
    assert memory.recall("测试不含糖牛奶", version, {"9901"}, "new") == []
    assert memory.recall("测试含糖牛奶", version, {"9901"}, "new")
    other = AgentMemory(Store(tmp_path / "other.db"), settings)
    assert other.recall("测试含糖牛奶", version, {"9901"}, "new") == []
    with store.connect() as db:
        db.execute(update(expert_memories).values(expires=time.time() - 1))
    assert memory.recall("测试含糖牛奶", version, {"9901"}, "new") == []


def test_unapproved_synthetic_and_stale_cannot_become_memory(settings):
    store = Store(settings.database)
    job = submit(store)
    process_one(settings, FakeModels())
    current = store.get(job["id"])
    memory = AgentMemory(store, settings)
    with pytest.raises(Conflict, match="approved"):
        memory.publish(job["id"], current["revision"], "expert", Knowledge(settings.corpus, True))
    store.review(
        job["id"],
        Feedback(approved=True, final_code="990101", reason="专家核查测试", reviewer="expert"),
        {"990101"},
    )
    with pytest.raises(Conflict, match="revision"):
        memory.publish(job["id"], "0" * 64, "expert", Knowledge(settings.corpus, True))
    settings.language_rules.write_text('{"version":"changed"}', encoding="utf-8")
    with pytest.raises(Conflict, match="stale"):
        memory.publish(job["id"], current["revision"], "expert", Knowledge(settings.corpus, True))
    # A fabricated/legacy approval with synthetic governance must still never be promoted.
    task = store.submit("case", {"description": "合成审批", "elements": []}, "synthetic-approval")
    owned = store.claim(100, 3)
    store.finish(
        task["id"],
        owned["token"],
        "awaiting_review",
        {"risk_check": {"decision": "clear"}, "normalization": {"synthetic": True}},
    )
    store.review(
        task["id"],
        Feedback(approved=True, final_code="990101", reason="测试拦截条件", reviewer="expert"),
        {"990101"},
    )
    with pytest.raises(Conflict, match="synthetic"):
        memory.publish(
            task["id"], store.get(task["id"])["revision"], "expert", Knowledge(settings.corpus, True)
        )


def test_version_change_during_model_work_stops_old_run(settings):
    store = Store(settings.database)

    class Change(FakeModels):
        def structured(self, stage, payload, schema, teacher=False):
            if stage == "extract_attributes":
                store.save_terms({"groups": {"新规范": ["旧别名"]}})
            return super().structured(stage, payload, schema, teacher)

    job = submit(store)
    process_one(settings, Change())
    result = store.get(job["id"])
    assert result["status"] == "failed" and result["error"] == "RunContextChanged"
    assert not any(e["stage"] == "retrieve" for e in store.events(job["id"]))


def test_versioned_api_and_memory_permissions(settings):
    store = Store(settings.database)
    job = submit(store)
    process_one(settings, FakeModels())
    revision = store.get(job["id"])["revision"]
    with TestClient(create_app(settings)) as client:
        user = {"X-API-Key": "a" * 32}
        reviewer = {"X-API-Key": "b" * 32}
        assert client.get("/v1/memory/experts").status_code == 401
        assert (
            client.post(
                f"/v1/cases/{job['id']}/memory/publish", headers=user, json={"expected_revision": revision}
            ).status_code
            == 403
        )
        payload = {"approved": True, "final_code": "990101", "reason": "专家核验通过", "reviewer": "test"}
        assert client.post(f"/v1/cases/{job['id']}/review", headers=reviewer, json=payload).status_code == 409
        assert (
            client.post(
                f"/v1/cases/{job['id']}/review",
                headers=reviewer,
                json=payload | {"expected_revision": revision},
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/v1/cases/{job['id']}/memory/publish",
                headers=reviewer,
                json={"expected_revision": revision},
            ).status_code
            == 200
        )
        assert len(client.get("/v1/memory/experts", headers=user).json()["items"]) == 1
        assert client.get(f"/v1/cases/{job['id']}/memory", headers=user).json()["confirmed_elements"] == []
        assert client.get(f"/v1/tasks/{job['id']}/trace", headers=user).json()["items"]


def test_trace_has_linked_spans_without_product_text_or_keys(settings):
    store = Store(settings.database)
    job = submit(store)
    model = Models(settings)
    model.http.close()
    seen = []

    def transport(request):
        seen.append(request.headers.get("traceparent"))
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    model.http = httpx.Client(transport=httpx.MockTransport(transport))
    try:
        with task_trace(store, settings, job["id"], "local-test-run"):
            with span("node.test", {"agent.stage": "test"}):
                model.embed(["私有商品文本不应进入Trace"])
        assert seen[0].startswith("00-")
        with store.connect() as db:
            rows = [dict(r._mapping) for r in db.execute(select(agent_spans))]
        serialized = json.dumps(rows, ensure_ascii=False)
        assert "私有商品文本" not in serialized and "a" * 32 not in serialized
        assert len(rows) == 3 and len({r["trace_id"] for r in rows}) == 1
        assert all(r["parent_id"] for r in rows if r["name"] != "agent.run")
    finally:
        model.close()


def test_policy_and_input_versions_are_stable_and_sensitive(settings):
    store = Store(settings.database)
    first = versions(settings, store)
    assert versions(settings, store) == first
    assert input_revision({"description": "不含糖", "_clarifications": 1}) == input_revision(
        {"description": "不含糖", "_clarifications": 2}
    )
    assert input_revision({"description": "含糖"}) != input_revision({"description": "不含糖"})
    store.save_terms({"groups": {"主词": ["别名"]}})
    assert versions(settings, store)["domain_version"] != first["domain_version"]


def test_new_publication_is_only_visible_to_later_runs(settings):
    store = Store(settings.database)
    source = approved(store, settings)
    memory = AgentMemory(store, settings)

    class PublishDuringRun(FakeModels):
        def structured(self, stage, payload, schema, teacher=False):
            if stage == "extract_attributes":
                memory.publish(source["id"], source["revision"], "expert", Knowledge(settings.corpus, True))
            return super().structured(stage, payload, schema, teacher)

    first = submit(store, idem="new-memory-pinned")
    process_one(settings, PublishDuringRun())
    assert store.get(first["id"])["status"] == "awaiting_review"
    assert store.get(first["id"])["result"]["expert_memories"] == []
    later = submit(store, idem="new-memory-following-run")
    process_one(settings, FakeModels())
    assert len(store.get(later["id"])["result"]["expert_memories"]) == 1


def test_revoked_memory_invalidates_inflight_run(settings):
    store = Store(settings.database)
    source = approved(store, settings)
    memory = AgentMemory(store, settings)
    published = memory.publish(source["id"], source["revision"], "expert", Knowledge(settings.corpus, True))

    class Revoke(FakeModels):
        def structured(self, stage, payload, schema, teacher=False):
            if stage == "l1_heading_shortlist":
                assert payload["reviewed_expert_references_not_legal_authority"]
                memory.revoke(published["id"], "已确认不再适用", "expert")
            return super().structured(stage, payload, schema, teacher)

    case = submit(store, idem="revoke-inflight")
    process_one(settings, Revoke())
    current = store.get(case["id"])
    assert current["status"] == "failed" and current["error"] == "RunContextChanged"
    assert not any(e["stage"] == "l2" for e in store.events(case["id"]))


def test_policy_change_prevents_approval_of_old_result(settings):
    store = Store(settings.database)
    job = submit(store)
    process_one(settings, FakeModels())
    revision = store.get(job["id"])["revision"]
    store.save_terms({"groups": {"变更词": ["旧词"]}})
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/v1/cases/{job['id']}/review",
            headers={"X-API-Key": "b" * 32},
            json={
                "approved": True,
                "final_code": "990101",
                "reason": "过期结果不能通过",
                "reviewer": "expert",
                "expected_revision": revision,
            },
        )
        assert response.status_code == 409
    assert store.get(job["id"])["status"] == "awaiting_review"


def test_release_gate_rejects_wrong_suite_missing_metrics_and_small_runs():
    from tariff_agent.release_gate import assess

    policy = {
        "suite": "agent",
        "min_cases": 5,
        "minimum": {"runtime_versioned_rate": 1},
        "maximum": {"memory_scope_violations": 0},
    }
    report = {
        "suite": "retrieval",
        "status": "completed",
        "name": "not-an-agent-run",
        "n_completed": 200,
        "metrics": {"heading_recall@40": {"value": 1}},
    }
    assert not assess(report, policy)["passed"]
    proper = {
        "suite": "agent",
        "status": "completed",
        "name": "agent",
        "n_completed": 5,
        "metrics": {"runtime_versioned_rate": 1, "memory_scope_violations": 0},
    }
    assert assess(proper, policy)["passed"]
    assert not assess(proper | {"n_completed": 1}, policy)["passed"]
    assert not assess(
        proper | {"metrics": {"runtime_versioned_rate": 1, "memory_scope_violations": 1}}, policy
    )["passed"]


def test_revocation_prevents_approval_of_dependent_result(settings):
    store = Store(settings.database)
    source = approved(store, settings)
    memory = AgentMemory(store, settings)
    published = memory.publish(source["id"], source["revision"], "expert", Knowledge(settings.corpus, True))
    dependent = submit(store, idem="dependent-review")
    process_one(settings, FakeModels())
    current = store.get(dependent["id"])
    assert current["result"]["expert_memories"]
    memory.revoke(published["id"], "原参考结论已失效", "expert")
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/v1/cases/{dependent['id']}/review",
            headers={"X-API-Key": "b" * 32},
            json={
                "approved": True,
                "final_code": "990101",
                "reason": "不能审核已失效的参考",
                "reviewer": "expert",
                "expected_revision": current["revision"],
            },
        )
        assert response.status_code == 409
    assert store.get(dependent["id"])["status"] == "awaiting_review"


def test_query_embedding_reused_between_indices_but_not_between_requests(settings, monkeypatch):
    from tariff_agent import retrieval

    calls = []

    class Model:
        def embed_query(self, texts):
            calls.append(texts)
            return [[1.0, 0.0]]

    class Index:
        def __init__(self, *args, **kwargs):
            pass

        def search(self, query, embed, **kwargs):
            assert embed([query]) == [[1.0, 0.0]]
            return []

        def close(self):
            pass

    monkeypatch.setattr(retrieval, "History", Index)
    enabled = settings.model_copy(
        update={
            "history_enabled": True,
            "history_dense_enabled": True,
            "qdrant_catalog_enabled": True,
            "dense_enabled": True,
            "rerank_enabled": False,
        }
    )
    for _ in range(2):
        retrieval.recall(enabled, Knowledge(settings.corpus, True), Model(), "同一个检索查询")
    assert calls == [["同一个检索查询"], ["同一个检索查询"]]
