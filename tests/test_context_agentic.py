import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from test_agent_memory_runtime import submit
from test_engineering import FakeModels

from tariff_agent.agent_evaluation import inspect_run
from tariff_agent.agent_skills import catalog, load_skill
from tariff_agent.agent_telemetry import span, task_trace
from tariff_agent.agentic_retrieval import ExploreAction, ScopedExplorer
from tariff_agent.context_engineering import ContextBudgetExceeded, ContextPacker
from tariff_agent.domain import Attributes, Element, Feedback, Ranking
from tariff_agent.durable import checkpoint_store
from tariff_agent.human_review import review_graph
from tariff_agent.knowledge import Knowledge
from tariff_agent.persistence import agent_spans
from tariff_agent.store import Conflict, Store
from tariff_agent.worker import process_one


def test_context_selects_whole_sources_and_preserves_facts(settings):
    settings.context_max_input_tokens = 4500
    packer = ContextPacker(settings)
    payload = {
        "original_description": "不含糖；未经熟制；净重20千克",
        "submitted_elements": [{"name": "加工方式", "value": "冷冻、未调味"}],
        "attributes": {"processing": "未经熟制"},
        "allowed_codes": ["9901"],
        "candidates": [
            {"code": "9901", "text": "测试条文", "source_id": "s9901", "note_ids": ["large", "short"]}
        ],
        "evidence": [
            {"id": "large", "text": "未经熟制，不含糖；" * 1000},
            {"id": "short", "text": "原始短注释，未经熟制。"},
            {"id": "short", "text": "原始短注释，未经熟制。"},
        ],
    }
    original = json.loads(json.dumps(payload))
    packed, report = packer.pack("l2", payload, Ranking)
    assert payload == original
    assert packed["original_description"] == original["original_description"]
    assert packed["submitted_elements"] == original["submitted_elements"]
    assert packed["attributes"] == original["attributes"]
    assert packed["evidence"] == [original["evidence"][1]]
    assert packed["candidates"][0]["note_ids"] == ["short"]
    assert report["input_after"] < report["input_before"] and report["omitted_records"] == 2
    assert report["counter_kind"] == "utf8_byte_upper_bound_proxy"


def test_oversized_facts_are_rejected_not_truncated(settings):
    settings.context_max_input_tokens = 2000
    with pytest.raises(ContextBudgetExceeded):
        ContextPacker(settings).pack("test", {"original_description": "不含糖" * 2000}, Ranking)


def test_teacher_code_list_is_preserved_without_treating_it_as_candidate_rows(settings):
    from tariff_agent.domain import Report

    payload = {
        "case": {"description": "未调味冷冻牛肉"},
        "candidates": ["990101"],
        "issues": ["需要专家核查"],
    }
    packed, report = ContextPacker(settings).pack("teacher_report_not_verified_authority", payload, Report)
    assert packed["candidates"] == ["990101"] and packed["case"] == payload["case"]
    assert report["protected_facts_unchanged"]


def test_skills_are_metadata_first_and_versioned():
    assert all("instructions" not in item for item in catalog())
    assert "instructions" in load_skill("exclusion_review")
    assert load_skill("exclusion_review")["version"] == load_skill("exclusion_review")["version"]


@pytest.mark.parametrize(
    "action",
    [
        ExploreAction(skill_id="exclusion_review", action="lookup_notes", codes=["0000"]),
        ExploreAction(skill_id="exclusion_review", action="search_cases", codes=["9901"]),
    ],
)
def test_planner_rejects_candidate_and_skill_scope(settings, action):
    records = []
    explorer = ScopedExplorer(
        settings,
        Knowledge(settings.corpus, True),
        None,
        lambda *a, **kw: action,
        lambda *args: records.append(args),
    )
    output = explorer.run(
        {"description": "测试", "elements": []}, {}, ["9901"], Ranking(codes=["9901"], conflicts=["不足"])
    )
    assert output["stop_reason"] == "invalid_plan" and output["rounds"] == 0
    assert not any(stage == "agentic_action" for stage, data in records)


def test_exploration_stops_on_repeat_and_never_expands_candidates(settings):
    records = []
    action = ExploreAction(skill_id="exclusion_review", action="lookup_notes", codes=["9901"])
    explorer = ScopedExplorer(
        settings,
        Knowledge(settings.corpus, True),
        None,
        lambda *a, **kw: action,
        lambda *args: records.append(args),
    )
    output = explorer.run({"description": "测试", "elements": []}, {}, ["9901"], Ranking(codes=["9901"]))
    assert output["stop_reason"] == "repeated_action" and output["rounds"] == 1
    assert [data["codes"] for stage, data in records if stage == "agentic_action"] == [["9901"]]


def test_exploration_enforces_two_tool_limit(settings):
    settings.agentic_retrieval_max_rounds = 99
    actions = iter(
        [
            ExploreAction(skill_id="exclusion_review", action="lookup_notes", codes=["9901"]),
            ExploreAction(skill_id="exclusion_review", action="compare_candidates", codes=["9901"]),
        ]
    )
    records = []
    explorer = ScopedExplorer(
        settings,
        Knowledge(settings.corpus, True),
        None,
        lambda *a, **kw: next(actions),
        lambda *args: records.append(args),
    )
    explorer.dispatch = lambda action: {"status": "ok", "evidence": [], "tag": action.action}
    output = explorer.run({"description": "测试", "elements": []}, {}, ["9901"], Ranking(codes=["9901"]))
    assert output["rounds"] == 2 and output["stop_reason"] == "round_budget_exhausted"


def test_l2_can_ask_one_missing_attribute_and_restart_after_clarification(settings):
    class Ask(FakeModels):
        def structured(self, stage, payload, schema, teacher=False):
            if schema is Attributes:
                return Attributes(
                    material="塑料", use="工业", processing="冷冻" if payload.get("elements") else ""
                )
            if schema is ExploreAction:
                return ExploreAction(
                    skill_id="clarification",
                    action="ask_clarification",
                    attribute="processing",
                    question="请确认该商品是否经过熟制加工？",
                )
            if stage == "l2_note_confirmation" and not payload["attributes"]["processing"]:
                return Ranking(codes=["9901"], conflicts=["processing_needed"])
            return super().structured(stage, payload, schema, teacher)

    store = Store(settings.database)
    job = submit(store)
    process_one(settings, Ask())
    current = store.get(job["id"])
    assert current["status"] == "awaiting_information"
    assert current["result"]["exploration"]["rounds"] == 1
    assert not any(row["stage"] == "subheading" for row in store.events(job["id"]))
    assert inspect_run(current["result"], store.events(job["id"]), Knowledge(settings.corpus, True))["trace"][
        "score"
    ]
    store.clarify(job["id"], [Element(name="加工方式", value="冷冻")], current["revision"])
    process_one(settings, Ask())
    updated = store.get(job["id"])
    assert updated["status"] == "awaiting_review" and updated["revision"] != current["revision"]
    assert updated["result"]["human_review"]["status"] == "waiting_for_expert"


def test_known_attribute_cannot_be_reasked(settings):
    action = ExploreAction(
        skill_id="clarification", action="ask_clarification", attribute="use", question="用途是什么？"
    )
    explorer = ScopedExplorer(
        settings, Knowledge(settings.corpus, True), None, lambda *a, **kw: action, lambda *args: None
    )
    output = explorer.run(
        {"description": "测试", "elements": []}, {"use": "工业"}, ["9901"], Ranking(codes=["9901"])
    )
    assert not output["questions"] and output["stop_reason"] == "already_known_or_invalid_question"


def test_native_expert_pause_and_resume_without_models(settings):
    store = Store(settings.database)
    job = submit(store)
    process_one(settings, FakeModels())
    current = store.get(job["id"])
    review = current["result"]["human_review"]
    with checkpoint_store(store, settings, job) as saver:
        snapshot = review_graph(saver).get_state({"configurable": {"thread_id": review["thread_id"]}})
        assert snapshot.next == ("expert_decision",)
    store.review(
        job["id"],
        Feedback(approved=True, final_code="990101", reason="专家原生恢复测试", reviewer="test"),
        {"990101"},
    )
    current = store.get(job["id"])
    assert current["status"] == "approved" and current["result"]["human_review"]["status"] == "completed"
    with checkpoint_store(store, settings, job) as saver:
        snapshot = review_graph(saver).get_state({"configurable": {"thread_id": review["thread_id"]}})
        assert not snapshot.next and snapshot.values["decision"]["approved"]
    assert len(store.approved_samples()) == 1


def test_native_resume_rejects_corrupted_review_pointer(settings):
    from sqlalchemy import update

    from tariff_agent.persistence import jobs

    store = Store(settings.database)
    job = submit(store)
    process_one(settings, FakeModels())
    result = store.get(job["id"])["result"]
    result["human_review"]["request"]["proposal_hash"] = "0" * 64
    with store.connect() as db:
        db.execute(update(jobs).where(jobs.c.id == job["id"]).values(result=json.dumps(result)))
    with pytest.raises(Conflict, match="checkpoint"):
        store.review(
            job["id"],
            Feedback(approved=True, final_code="990101", reason="不能恢复错误指针", reviewer="test"),
            {"990101"},
        )
    assert store.get(job["id"])["status"] == "awaiting_review" and not store.approved_samples()


def test_otlp_authenticated_export_uses_real_http_without_private_content(settings):
    captured = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            captured.append((dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings.otlp_endpoint = f"http://127.0.0.1:{server.server_port}/api/public/otel/v1/traces"
    settings.otlp_headers = SecretStr(
        json.dumps({"Authorization": "Basic private-test-token", "x-langfuse-ingestion-version": "4"})
    )
    store = Store(settings.database)
    job = submit(store)
    try:
        with task_trace(store, settings, job["id"], "export-test"):
            with span("model.test"):
                pass
        assert captured
        raw_headers, body = captured[0]
        headers = {key.lower(): value for key, value in raw_headers.items()}
        assert headers["authorization"] == "Basic private-test-token"
        assert headers["x-langfuse-ingestion-version"] == "4"
        assert b"langfuse.session.id" in body and b"private-test-token" not in body
        with store.connect() as db:
            rows = [dict(r._mapping) for r in db.execute(select(agent_spans))]
        assert "private-test-token" not in json.dumps(rows)
    finally:
        server.shutdown()
        server.server_close()


def test_review_recovers_after_checkpoint_written_but_feedback_transaction_failed(settings):
    from sqlalchemy import event

    store = Store(settings.database)
    job = submit(store)
    process_one(settings, FakeModels())
    value = Feedback(approved=True, final_code="990101", reason="专家确认同一决定", reviewer="test")

    def fail_feedback(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO FEEDBACK"):
            raise RuntimeError("Injected failure after native resume, before feedback commit")

    event.listen(store.engine, "before_cursor_execute", fail_feedback)
    try:
        with pytest.raises(RuntimeError, match="Injected failure"):
            store.review(job["id"], value, {"990101"})
    finally:
        event.remove(store.engine, "before_cursor_execute", fail_feedback)
    assert store.get(job["id"])["status"] == "awaiting_review" and not store.approved_samples()
    with pytest.raises(Conflict, match="different expert decision"):
        store.review(job["id"], value.model_copy(update={"reason": "换一个不同的审核决定"}), {"990101"})
    store.review(job["id"], value, {"990101"})
    assert store.get(job["id"])["status"] == "approved" and len(store.approved_samples()) == 1


def test_context_artifact_is_authenticated_and_bound_to_case(settings):
    from fastapi.testclient import TestClient

    from tariff_agent.api import create_app

    store = Store(settings.database)
    first = submit(store, idem="artifact-first")
    other = submit(store, idem="artifact-other")
    store.event(
        first["id"],
        "context_artifact",
        {"artifact_id": "f" * 64, "payload": {"original_description": "未熟制原文"}},
    )
    with TestClient(create_app(settings)) as client:
        path = f"/v1/tasks/{first['id']}/context/" + "f" * 64
        assert client.get(path).status_code == 401
        assert (
            client.get(path, headers={"X-API-Key": "a" * 32}).json()["payload"]["original_description"]
            == "未熟制原文"
        )
        assert (
            client.get(
                f"/v1/tasks/{other['id']}/context/" + "f" * 64, headers={"X-API-Key": "a" * 32}
            ).status_code
            == 404
        )


def test_experiment_identity_propagates_without_product_text(settings):
    from opentelemetry import baggage
    from opentelemetry.propagate import extract

    from tariff_agent.agent_telemetry import experiment_item, trace_headers

    store = Store(settings.database)
    job = submit(store)
    with experiment_item("exp-v1", "comparison", "dataset-hash", "item-1", "990101"):
        with task_trace(store, settings, job["id"], "test-run") as session:
            with span("model.test"):
                outgoing = trace_headers(internal=True)
                inherited = baggage.get_all(context=extract(outgoing))
                assert (
                    inherited["langfuse.experiment.item.root_observation_id"]
                    == f"{session.root.get_span_context().span_id:016x}"
                )
            session.complete({"status": "awaiting_review", "resolved": ["990101"]})
    with store.connect() as db:
        rows = [dict(r._mapping) for r in db.execute(select(agent_spans))]
    attrs = [json.loads(row["attributes"]) for row in rows]
    assert all(row["langfuse.experiment.id"] == "exp-v1" for row in attrs)
    root = next(row for row in rows if row["name"] == "agent.run")
    assert all(row["langfuse.experiment.item.root_observation_id"] == root["span_id"] for row in attrs)
    assert "测试塑料专用部件" not in json.dumps(rows, ensure_ascii=False)


def test_langfuse_config_is_private_pinned_and_does_not_launch(tmp_path):
    import runpy
    from pathlib import Path

    import yaml

    prepare = runpy.run_path("scripts/prepare_langfuse.py")["prepare"]
    target = tmp_path / "langfuse"
    report = prepare(Path("."), target)
    assert report["runtime_started"] is False and report["tracing_automatically_enabled"] is False
    compose = yaml.safe_load((target / "compose.generated.yaml").read_text())
    assert len(compose["services"]) == 6
    assert compose["services"]["langfuse-web"]["ports"] == ["127.0.0.1:18090:3000"]
    assert not compose["services"]["postgres"].get("ports")
    assert (target / ".env").stat().st_mode & 0o777 == 0o600
    assert "password" not in json.dumps(report)
    with pytest.raises(FileExistsError):
        prepare(Path("."), target)
