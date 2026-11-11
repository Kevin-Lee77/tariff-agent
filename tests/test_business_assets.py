import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from tariff_agent.api import create_app
from tariff_agent.assets import Assets
from tariff_agent.declaration import language_check, source_check
from tariff_agent.domain import CaseInput
from tariff_agent.flask_services import create_service
from tariff_agent.persistence import governance_report
from tariff_agent.store import Conflict


@pytest.fixture
def seeded(settings):
    settings.business_assets_enabled = True
    assets = Assets(settings.database)
    seed = {
        "kind": "synthetic",
        "source": "test-only",
        "synthetic": True,
        "bills": [
            {
                "source_key": "1",
                "codeTs": "0202300090",
                "description": "商品名称：变质牛肉",
                "alignment_status": "aligned",
                "elements": [{"code": "0000", "name": "商品名称", "value": "变质牛肉"}],
            },
            {
                "source_key": "2",
                "codeTs": "0202300090",
                "description": "原始待映射",
                "alignment_status": "requires_mapping",
                "elements": [
                    {"code": "0000", "name": "商品名称", "value": "牛肉"},
                    {"code": "OTHER", "value": ""},
                ],
            },
        ],
        "negative": [
            {
                "knowledge_id": "SYNTHETIC",
                "fit_area": "02",
                "element_code": "0000",
                "vocabulary": "变质",
                "threshold": 1.0,
                "status": "sandbox",
            }
        ],
        "dictionaries": [
            {
                "kind": "risk",
                "name": "测试风险",
                "config": {
                    "matching": "exact",
                    "coverage": [{"prefix": "02", "elements": ["0000"]}],
                    "element_names": {"商品名称": "0000"},
                },
                "entries": [],
            },
            {"kind": "synonym", "name": "测试同义词", "entries": [{"term": "冻制", "canonical": "冷冻"}]},
            {"kind": "independent", "name": "测试独立词", "entries": [{"term": "不锈钢"}]},
            {"kind": "nonstandard", "name": "测试非规范词", "entries": [{"term": "一批货"}]},
            {
                "kind": "mutex",
                "name": "测试互斥",
                "entries": [{"term": "冷冻", "payload": {"group1": ["冷冻"], "group2": ["冷藏"]}}],
            },
        ],
    }
    assets.import_bundle(seed)
    return settings, assets, seed


def test_schema_and_idempotent_import(seeded):
    settings, assets, seed = seeded
    assert {
        "bill",
        "bill_element",
        "negative",
        "dictionary_version",
        "dictionary_entry",
        "governance_report",
        "governance_annotation",
        "data_imports",
    } <= set(inspect(assets.engine).get_table_names())
    first = assets.stats()
    assert assets.import_bundle(seed)["replayed"] is True
    assert assets.stats()["bill"] == first["bill"] == 2
    with pytest.raises(KeyError):
        assets.import_bundle(
            {"kind": "bill", "source": "broken", "synthetic": False, "bills": [{"codeTs": "0202300090"}]}
        )
    assert len(assets.stats()["imports"]) == 1


def test_database_policy_and_bill_mismatch_guard(seeded):
    settings, assets, _ = seeded
    row = next(r for r in assets.list_assets("bill")["items"] if r["alignment_status"] == "aligned")
    case = CaseInput(
        description=row["description"],
        declared_code=row["code_ts"],
        bill_id=row["id"],
        governance_profile="sandbox",
        elements=[{"name": "商品名称", "code": "0000", "value": "变质牛肉"}],
    )
    assert source_check(case, settings)["decision"] == "blocked"
    assert (
        source_check(case.model_copy(update={"description": "正常牛肉"}), settings)["decision"]
        == "review_required"
    )
    unaligned = next(r for r in assets.list_assets("bill")["items"] if r["alignment_status"] != "aligned")
    assert (
        source_check(case.model_copy(update={"bill_id": unaligned["id"]}), settings)["decision"]
        == "information_required"
    )
    assert (
        source_check(CaseInput(description="变质牛肉", declared_code="0202300090"), settings)["decision"]
        == "review_required"
    )


def test_database_term_types_and_scoping(seeded):
    settings, assets, _ = seeded
    assert (
        language_check(
            CaseInput(description="冻制牛肉，不含添加剂；不锈钢", governance_profile="sandbox"), settings
        )["normalized_text"]
        == "冷冻牛肉，不含添加剂；不锈钢"
    )
    assert (
        language_check(CaseInput(description="一批货", governance_profile="sandbox"), settings)["status"]
        == "review_required"
    )
    assert (
        language_check(CaseInput(description="冷冻冷藏牛肉", governance_profile="sandbox"), settings)[
            "status"
        ]
        == "review_required"
    )
    assert language_check(CaseInput(description="冻制牛肉"), settings)["status"] == "dictionary_unavailable"


def test_dictionary_revisions_publication_and_raw_history(seeded):
    settings, assets, _ = seeded
    row = assets.list_assets("dictionaries")["items"][0]
    with pytest.raises(Conflict, match="sandbox"):
        assets.publish(row["id"], "test", "合成不可发布")
    legacy = {
        "kind": "language",
        "source": "unreviewed-test",
        "synthetic": False,
        "dictionaries": [
            {
                "kind": "synonym",
                "name": "人工待审核",
                "entries": [{"term": "冻制", "canonical": "冷冻", "scope": "02"}],
            }
        ],
    }
    assets.import_bundle(legacy)
    original = assets.list_assets("dictionaries", synthetic=False)["items"][0]
    revision = assets.revise(
        original["id"],
        "人工修订词典",
        [{"term": "剔骨", "canonical": "去骨", "scope": "02"}],
        None,
        "expert",
        "核实同义关系",
    )
    assets.publish(revision["id"], "expert", "测试审核通过")
    assert assets.list_assets("entries", dictionary_id=original["id"])["items"][0]["term"] == "冻制"
    assert (
        language_check(CaseInput(description="剔骨牛肉", declared_code="0202300090"), settings)[
            "normalized_text"
        ]
        == "去骨牛肉"
    )
    assert (
        language_check(CaseInput(description="剔骨部件", declared_code="390100"), settings)["normalized_text"]
        == "剔骨部件"
    )


def test_flask_reports_are_written_to_business_tables(seeded):
    settings, assets, _ = seeded
    with create_service("source", settings).test_client() as client:
        response = client.post(
            "/v1/declarations/check",
            headers={"X-API-Key": "a" * 32},
            json={"description": "变质牛肉", "declared_code": "0202300090", "governance_profile": "sandbox"},
        )
        assert response.status_code == 200
        identifier = response.json["report_id"]
    with assets.connect() as db:
        row = db.execute(select(governance_report).where(governance_report.c.id == identifier)).one()
        assert json.loads(row.data)["decision"] == "blocked"
    assert assets.stats()["governance_report"] == 1


def test_asset_api_auth_paging_and_bill_submission(seeded):
    settings, assets, _ = seeded
    with TestClient(create_app(settings)) as client:
        assert client.get("/v1/assets/summary").status_code == 401
        headers = {"X-API-Key": "a" * 32, "Idempotency-Key": "bill-check-api"}
        rows = client.get("/v1/assets/bill?synthetic=true&limit=1", headers=headers).json()
        assert rows["total"] == 2 and len(rows["items"]) == 1
        response = client.post(
            "/v1/assets/bill/" + rows["items"][0]["id"] + "/execute",
            headers=headers,
            json={"classify": False},
        )
        assert response.status_code == 202 and response.json()["payload"]["bill_id"] == rows["items"][0]["id"]
        assert client.get("/v1/assets/bill?query=%25", headers=headers).json()["total"] == 0
        version = assets.list_assets("dictionaries")["items"][0]
        assert (
            client.post(
                "/v1/assets/dictionaries/" + version["id"] + "/publish",
                headers=headers,
                json={"reason": "禁止操作员发布"},
            ).status_code
            == 403
        )


def test_business_migration_preserves_existing_tasks(settings, monkeypatch):
    from alembic import command
    from alembic.config import Config

    from tariff_agent.store import Store

    monkeypatch.setenv("TARIFF_DATABASE", str(settings.database))
    monkeypatch.setenv("TARIFF_DATABASE_URL", "")
    command.upgrade(Config("alembic.ini"), "0001_initial")
    store = Store(settings.database, auto_create=False)
    first = store.submit("terms", {"pending": []}, "migration-preserve")
    command.upgrade(Config("alembic.ini"), "head")
    assert store.get(first["id"])["kind"] == "terms"
    assert "bill" in inspect(store.engine).get_table_names()


def test_bill_mapping_revision_preserves_the_unaligned_original(seeded):
    from tariff_agent.domain import Element

    settings, assets, _ = seeded
    old = next(r for r in assets.list_assets("bill")["items"] if r["alignment_status"] == "requires_mapping")
    updated = assets.revise_bill(
        old["id"],
        [Element(name="商品名称", code="0000", value="正常牛肉")],
        old["code_ts"],
        "test",
        "逐项核对原始值",
    )
    assert assets.get_bill(old["id"])["alignment_status"] == "requires_mapping"
    assert assets.get_bill(updated["id"])["alignment_status"] == "aligned"
    assert assets.get_bill(updated["id"])["raw"]["parent_bill_id"] == old["id"]


def test_bill_job_uses_database_rules_and_preserves_blocked_status(seeded):
    from tariff_agent.store import Store
    from tariff_agent.worker import process_one

    settings, assets, _ = seeded
    row = next(r for r in assets.list_assets("bill")["items"] if r["alignment_status"] == "aligned")
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/assets/bill/" + row["id"] + "/execute",
            headers={"X-API-Key": "a" * 32, "Idempotency-Key": "bill-worker-status"},
            json={"classify": False},
        )
        identifier = response.json()["id"]
    assert process_one(settings)
    task = Store(settings.database).get(identifier)
    assert task["status"] == "risk_blocked"
    assert task["result"]["bill_id"] == row["id"]
    assert task["result"]["report_id"]
