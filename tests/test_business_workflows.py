import io
import uuid

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import func, select

from tariff_agent.api import create_app
from tariff_agent.asset_batches import AssetBatches, csv_bytes, process_batch, read_rows
from tariff_agent.asset_operations import DictionaryEditor
from tariff_agent.assets import Assets
from tariff_agent.business_rules import validate_entries
from tariff_agent.declaration import language_check
from tariff_agent.domain import CaseInput, Element
from tariff_agent.persistence import audit, data_imports
from tariff_agent.store import Conflict, Store
from tariff_agent.worker import process_one


def create_batch(
    settings,
    data,
    *,
    synthetic=False,
    filename="terms.csv",
    kind="dictionary",
    dictionary_kind="synonym",
    idem=None,
):
    batches = AssetBatches(settings.database)
    return batches, batches.create(
        data,
        filename,
        {
            "operation": "import",
            "asset_kind": kind,
            "dictionary_kind": dictionary_kind,
            "name": "测试导入词典",
            "synthetic": synthetic,
        },
        "test-admin",
        idem or uuid.uuid4().hex,
    )


def import_terms(settings, rows, synthetic=False):
    data = csv_bytes(["term", "canonical", "element_code", "scope"], rows)
    batches, task = create_batch(settings, data, synthetic=synthetic)
    process_one(settings)
    return batches, batches.get(task["id"])


def editor_seed(settings):
    editor = DictionaryEditor(settings.database)
    imported = editor.import_bundle(
        {
            "kind": "test",
            "source": "generated-in-test",
            "synthetic": False,
            "dictionaries": [
                {
                    "kind": "synonym",
                    "name": "测试词典",
                    "entries": [
                        {"term": "冷冻", "canonical": "冷冻", "scope": "02", "element_code": "STORAGE"},
                        {"term": "冻制", "canonical": "冷冻", "scope": "02", "element_code": "STORAGE"},
                        {"term": "冰冻", "canonical": "冰冻", "scope": "02", "element_code": "STORAGE"},
                        {"term": "无糖", "canonical": "无糖", "scope": "02", "element_code": "STORAGE"},
                        {"term": "冷藏", "canonical": "冷藏", "scope": "03", "element_code": "STORAGE"},
                    ],
                }
            ],
        }
    )
    key = next(
        r["id"] for r in editor.list_assets("dictionaries")["items"] if r["import_id"] == imported["id"]
    )
    return editor, editor.detail(key)


def entry_id(value, term):
    return next(row["id"] for row in value["entries"] if row["term"] == term)


def test_import_validates_all_rows_before_writing_and_exposes_errors(settings):
    batches, task = import_terms(settings, [["冻制", "冷冻", "STORAGE", "02"], ["", "冷冻", "STORAGE", "02"]])
    assert task["status"] == "completed" and task["result"]["invalid"] == 1
    assert task["result"]["applied"] is False and batches.stats()["dictionary_entry"] == 0
    detail = batches.detail(task["id"], errors_only=True)
    assert len(detail["rows"]) == 1 and detail["rows"][0]["row_number"] == 3
    assert b"invalid" in batches.report(task["id"], True)[1]


def test_business_import_is_unreviewed_until_explicit_publication(settings):
    batches, task = import_terms(settings, [["冻制", "冷冻", "STORAGE", "02"]])
    key = task["result"]["dictionary_id"]
    editor = DictionaryEditor(settings.database)
    assert editor.detail(key)["status"] == "unreviewed"
    assert batches.language_policy(False, "0202300090") == {}
    editor.publish(key, "expert", "人工核对导入词典")
    assert batches.language_policy(False, "0202300090")["entries"][0]["term"] == "冻制"


def test_upload_idempotency_and_content_mismatch(settings):
    data = csv_bytes(["term"], [["牛肉"]])
    batches, first = create_batch(settings, data, idem="same-file-request")
    _, replay = create_batch(settings, data, idem="same-file-request")
    assert replay["id"] == first["id"]
    with pytest.raises(Conflict):
        create_batch(settings, data + b"x", idem="same-file-request")
    assert batches.list_jobs(kind="asset_batch")["total"] == 1


def test_same_file_new_request_reuses_the_existing_import(settings):
    batches, first = import_terms(settings, [["冻制", "冷冻", "STORAGE", "02"]])
    _, second = import_terms(settings, [["冻制", "冷冻", "STORAGE", "02"]])
    assert second["result"]["replayed"]
    assert first["result"]["import_id"] == second["result"]["import_id"]
    assert batches.stats()["dictionary_entry"] == 1


def test_cancel_during_validation_blocks_entire_business_commit(settings, monkeypatch):
    import tariff_agent.asset_batches as module

    batches, job = create_batch(settings, csv_bytes(["term"], [["牛肉"], ["鸡肉"]]))
    original = module.normalize_row
    calls = []

    def cancel_on_second(*args):
        calls.append(1)
        if len(calls) == 2:
            batches.cancel(job["id"])
        return original(*args)

    monkeypatch.setattr(module, "normalize_row", cancel_on_second)
    process_one(settings)
    assert batches.get(job["id"])["status"] == "cancelled"
    assert batches.stats()["dictionary_entry"] == 0
    assert batches.detail(job["id"])["processed"] == 1


def test_failed_commit_retries_without_duplicate_business_imports(settings, monkeypatch):
    original = AssetBatches.import_bundle
    batches, job = create_batch(settings, csv_bytes(["term"], [["牛肉"]]))

    def fail_once(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("Injected failure before atomic terminal commit")

    monkeypatch.setattr(AssetBatches, "import_bundle", fail_once)
    process_one(settings)
    assert batches.get(job["id"])["status"] == "failed"
    assert batches.stats()["dictionary_entry"] == 0
    monkeypatch.setattr(AssetBatches, "import_bundle", original)
    batches.retry(job["id"], 3)
    process_one(settings)
    assert batches.get(job["id"])["result"]["applied"]
    assert batches.stats()["dictionary_entry"] == 1
    assert batches.detail(job["id"])["processed"] == 1


def test_obsolete_batch_worker_cannot_write(settings):
    batches, job = create_batch(settings, csv_bytes(["term"], [["牛肉"]]))
    claimed = batches.claim(100, 3)
    batches.cancel(job["id"])
    with pytest.raises(Conflict):
        process_batch(settings, batches, claimed)
    assert batches.stats()["dictionary_entry"] == 0


def workbook(rows):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def test_xlsx_import_and_formula_rejection(settings):
    batches, task = create_batch(
        settings, workbook([["term", "canonical"], ["冻制", "冷冻"]]), filename="terms.xlsx"
    )
    process_one(settings)
    assert batches.get(task["id"])["result"]["applied"]
    before = batches.stats()["dictionary_entry"]
    _, bad = create_batch(settings, workbook([["term"], ["=1+1"]]), filename="formula.xlsx")
    process_one(settings)
    assert batches.get(bad["id"])["result"]["invalid"] == 1
    assert batches.stats()["dictionary_entry"] == before


@pytest.mark.parametrize(
    "kind,headers,values,table",
    [
        (
            "bill",
            ["code_ts", "description", "elements"],
            ["0202300090", "测试申报", '[{"code":"0000","name":"商品名称","value":"牛肉"}]'],
            "bill",
        ),
        (
            "negative",
            ["knowledge_id", "fit_area", "element_code", "vocabulary", "threshold"],
            ["TEST", "02", "0000", "测试风险", "0.8"],
            "negative",
        ),
    ],
)
def test_bill_and_risk_file_import_and_export_roundtrip(settings, kind, headers, values, table):
    batches, task = create_batch(settings, csv_bytes(headers, [values]), kind=kind)
    process_one(settings)
    assert batches.get(task["id"])["result"]["applied"]
    assert batches.stats()[table] == 1
    assert batches.risk_policy(False) == {}
    export = batches.create_export(kind, None, False, "expert", "roundtrip-export")
    process_one(settings)
    _, content = batches.report(export["id"])
    parsed = read_rows("roundtrip.csv", content)
    assert len(parsed) == 1 and not parsed[0][2]
    assert set(parsed[0][1]) == set(headers)


def test_expanded_workbook_limit_is_checked_before_loading(monkeypatch):
    import tariff_agent.asset_batches as module

    monkeypatch.setattr(module, "MAX_EXPANDED", 16)
    with pytest.raises(ValueError, match="expanded size"):
        read_rows("large.xlsx", workbook([["term"], ["测试词"]]))


@pytest.mark.parametrize("data", [b"term,term\na,b\n", b"term\n", b"\xff\xfe\x00", b"term\n" + b"a\n" * 5001])
def test_bounded_file_parser_rejects_invalid_headers_encoding_and_size(data):
    with pytest.raises(ValueError):
        read_rows("test.csv", data)


def test_duplicate_terms_fail_the_whole_batch(settings):
    batches, task = import_terms(
        settings, [["冻制", "冷冻", "STORAGE", "02"], ["冻制", "冷藏", "STORAGE", "02"]]
    )
    assert task["result"]["invalid"] == 1 and not task["result"]["applied"]
    assert batches.stats()["dictionary_entry"] == 0


def test_promote_split_confirm_and_restore_keep_history(settings):
    editor, base = editor_seed(settings)
    changed = editor.edit(
        base["id"],
        base["revision"],
        [{"action": "promote", "entry_ids": [entry_id(base, "冻制")]}],
        "expert",
        "人工切换主词",
    )
    value = editor.detail(changed["id"])
    assert {r["canonical"] for r in value["entries"] if r["term"] in ("冻制", "冷冻")} == {"冻制"}
    assert next(r for r in editor.detail(base["id"])["entries"] if r["term"] == "冷冻")["canonical"] == "冷冻"
    split = editor.edit(
        value["id"],
        value["revision"],
        [{"action": "split", "entry_ids": [entry_id(value, "冷冻")], "value": "冷冻"}],
        "expert",
        "拆分错误分组",
    )
    current = editor.detail(split["id"])
    restored = editor.restore(current["id"], base["id"], current["revision"], "expert", "恢复历史词典")
    assert editor.detail(restored["id"])["status"] == "unreviewed"
    assert sorted((r["term"], r["canonical"]) for r in editor.detail(restored["id"])["entries"]) == sorted(
        (r["term"], r["canonical"]) for r in base["entries"]
    )


@pytest.mark.parametrize("terms,target", [(["冻制", "无糖"], "冷冻"), (["冻制", "冷藏"], "冷冻")])
def test_group_edit_rejects_polarity_and_scope_mixing_atomically(settings, terms, target):
    editor, value = editor_seed(settings)
    with pytest.raises(Conflict):
        editor.edit(
            value["id"],
            value["revision"],
            [{"action": "merge", "entry_ids": [entry_id(value, term) for term in terms], "value": target}],
            "expert",
            "测试冲突处理",
        )
    assert editor.list_assets("dictionaries")["total"] == 1


def test_approval_blocks_stale_edits_and_conflicting_branch_publish(settings):
    editor, base = editor_seed(settings)
    first = editor.edit(
        base["id"],
        base["revision"],
        [{"action": "confirm", "entry_ids": [entry_id(base, "冻制")]}],
        "expert",
        "确认词条版本",
    )
    second = editor.edit(
        base["id"],
        base["revision"],
        [{"action": "confirm", "entry_ids": [entry_id(base, "冰冻")]}],
        "expert",
        "并行词条版本",
    )
    editor.publish(first["id"], "expert", "审核第一分支")
    with pytest.raises(Conflict):
        editor.publish(second["id"], "expert", "禁止发布过期分支")
    current = editor.detail(first["id"])
    editor.withdraw(current["id"], current["revision"], "expert", "撤销错误词典")
    assert not editor.language_policy(False, "0202300090")
    with pytest.raises(Conflict):
        editor.publish(current["id"], "expert", "不能直接重启历史版本")
    with pytest.raises(Conflict):
        editor.edit(
            current["id"],
            current["revision"],
            [{"action": "delete", "entry_ids": [entry_id(current, "冻制")]}],
            "expert",
            "过期快照修改",
        )


def test_rule_matching_requires_each_explicit_element_and_protects_negation(settings):
    settings.business_assets_enabled = True
    assets = Assets(settings.database)
    conditions = [
        {"element_code": "STORAGE", "terms": ["冷冻"]},
        {"element_code": "PROCESS", "terms": ["熟制"]},
    ]
    assets.import_bundle(
        {
            "kind": "test",
            "source": "generated-rules",
            "synthetic": True,
            "dictionaries": [
                {
                    "kind": "classification_mutex",
                    "name": "测试互斥",
                    "entries": [{"term": "测试跨要素", "scope": "02", "payload": {"conditions": conditions}}],
                },
                {
                    "kind": "score",
                    "name": "测试评分",
                    "entries": [
                        {
                            "term": "测试规则分数",
                            "scope": "02",
                            "payload": {"conditions": conditions, "score": 3},
                        }
                    ],
                },
            ],
        }
    )

    def check(first, second):
        return language_check(
            CaseInput(
                description="测试商品",
                declared_code="0202300090",
                governance_profile="sandbox",
                elements=[
                    Element(code="STORAGE", name="保存方法", value=first),
                    Element(code="PROCESS", name="加工方式", value=second),
                ],
            ),
            settings,
        )

    hit = check("冷冻", "熟制")
    assert any(r["reason"] == "cross_element_mutex" for r in hit["warnings"])
    assert hit["rule_scores"][0]["score"] == 3
    assert not check("冷冻熟制", "生制")["rule_scores"]
    assert not check("不含冷冻处理", "熟制")["rule_scores"]


def test_rule_validation_rejects_nonfinite_score_and_unscoped_conditions():
    with pytest.raises(Conflict):
        validate_entries("score", [{"term": "规则", "payload": {"score": float("inf")}}])
    with pytest.raises(Conflict):
        validate_entries(
            "mutex",
            [
                {
                    "term": "规则",
                    "payload": {
                        "conditions": [{"terms": ["冷冻"]}, {"element_code": "X", "terms": ["熟制"]}]
                    },
                }
            ],
        )


def test_restore_restores_configuration_as_well_as_entries(settings):
    editor, base = editor_seed(settings)
    changed = editor.revise(base["id"], "", None, {"priority": 7}, "expert", "修订版本配置")
    current = editor.detail(changed["id"])
    assert current["config"] == {"priority": 7}
    restored = editor.restore(current["id"], base["id"], current["revision"], "expert", "恢复历史配置")
    assert editor.detail(restored["id"])["config"] == base["config"]


def test_repeated_sandbox_import_points_to_current_revision(settings):
    batches, first = import_terms(settings, [["冻制", "冷冻", "STORAGE", "02"]], synthetic=True)
    editor = DictionaryEditor(settings.database)
    base = editor.detail(first["result"]["dictionary_id"])
    revised = editor.edit(
        base["id"],
        base["revision"],
        [{"action": "confirm", "entry_ids": [base["entries"][0]["id"]]}],
        "expert",
        "确认联调词条",
    )
    stale = editor.detail(base["id"])
    with pytest.raises(Conflict):
        editor.edit(
            stale["id"],
            stale["revision"],
            [{"action": "confirm", "entry_ids": [stale["entries"][0]["id"]]}],
            "expert",
            "禁止重新激活旧分支",
        )
    _, repeated = import_terms(settings, [["冻制", "冷冻", "STORAGE", "02"]], synthetic=True)
    assert repeated["result"]["dictionary_id"] == revised["id"]


def test_export_is_durable_and_spreadsheet_formula_safe(settings):
    batches, task = import_terms(settings, [["=1+1", "普通主词", "", ""]], synthetic=True)
    exported = batches.create_export(
        "entries", task["result"]["dictionary_id"], True, "expert", "export-test-key"
    )
    process_one(settings)
    value = batches.get(exported["id"])
    assert value["result"]["download_ready"] and value["result"]["total"] == 1
    filename, content = batches.report(exported["id"])
    assert filename.endswith(".csv") and "'=1+1" in content.decode("utf-8-sig")
    assert not batches.detail(exported["id"])["rows"]


def test_file_batch_api_permissions_progress_and_report(settings):
    with TestClient(create_app(settings)) as client:
        operator = {"X-API-Key": "a" * 32, "Idempotency-Key": "api-import-task"}
        assert (
            client.post("/v1/business/imports?filename=words.csv", content=b"term\nword\n").status_code == 401
        )
        response = client.post(
            "/v1/business/imports?filename=words.csv&synthetic=false",
            headers=operator,
            content=b"term\nword\n",
        )
        assert response.status_code == 202
        key = response.json()["id"]
        assert client.get(f"/v1/business/batches/{key}/report").status_code == 401
        process_one(settings)
        detail = client.get(f"/v1/business/batches/{key}", headers=operator).json()
        assert detail["processed"] == 1 and detail["job"]["result"]["applied"]
        dictionary = detail["job"]["result"]["dictionary_id"]
        snap = client.get(f"/v1/business/dictionaries/{dictionary}", headers=operator).json()
        body = {
            "expected_revision": snap["revision"],
            "reason": "权限验证测试",
            "operations": [{"action": "confirm", "entry_ids": [snap["entries"][0]["id"]]}],
        }
        assert (
            client.post(
                f"/v1/business/dictionaries/{dictionary}/operations", headers=operator, json=body
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/v1/business/dictionaries/{dictionary}/operations",
                headers={"X-API-Key": "b" * 32},
                json=body,
            ).status_code
            == 200
        )
        assert client.get(f"/v1/business/batches/{key}/report", headers=operator).status_code == 200
    with Store(settings.database).connect() as db:
        assert db.scalar(select(func.count()).select_from(audit)) > 0
        assert db.scalar(select(func.count()).select_from(data_imports)) == 1
