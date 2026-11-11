"""Isolated browser-test server. Synthetic data, no credentials or model calls from .env."""

import json
import os
import secrets
import tempfile
import threading
from pathlib import Path

import uvicorn

from tariff_agent.api import create_app
from tariff_agent.assets import Assets
from tariff_agent.auth import Identity
from tariff_agent.config import Settings
from tariff_agent.store import configured_store
from tariff_agent.worker import process_one


def main():
    root = Path(__file__).resolve().parents[1]
    parent = root / ".runtime" / "e2e"
    parent.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(dir=parent))
    corpus = {
        "jurisdiction": "CN",
        "version": "synthetic-browser-test",
        "source": "isolated UI fixture; not legal data",
        "test_only": True,
        "entries": [
            {"code": "01", "text": "测试章", "source_id": "synthetic:01"},
            {"code": "0101", "parent": "01", "text": "测试品目", "source_id": "synthetic:0101"},
            {"code": "010121", "parent": "0101", "text": "测试子目", "source_id": "synthetic:010121"},
        ],
        "groups": [{"code": "01012", "parent": "0101", "text": "测试分组", "source_id": "synthetic:group"}],
    }
    (run / "corpus.json").write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    (run / "rules.json").write_text(
        json.dumps({"02": {"保存方法": ["冷冻"]}}, ensure_ascii=False), encoding="utf-8"
    )
    s = Settings(
        _env_file=None,
        production=False,
        database=run / "app.db",
        database_url="",
        corpus=run / "corpus.json",
        rules=run / "rules.json",
        frontend_dist=root / "frontend" / "dist",
        allow_test_corpus=True,
        dense_enabled=False,
        rerank_enabled=False,
        history_enabled=False,
        governance_transport="embedded",
        history_directory=run / "history",
        benchmark_directory=run / "benchmark",
        evaluation_directory=run / "evaluations",
        qdrant_url="http://127.0.0.1:18999",
        business_assets_enabled=True,
        deepseek_api_key="",
        api_key=secrets.token_urlsafe(32),
        reviewer_key=secrets.token_urlsafe(32),
        cookie_secure=False,
    )
    password = secrets.token_urlsafe(24)
    Identity(configured_store(s), s).create_user("admin", password, "admin")
    bank_directory = s.evaluation_directory / "badcases"
    bank_directory.mkdir(parents=True)
    (bank_directory / "synthetic-ci.json").write_text(
        json.dumps(
            {
                "version": 1,
                "seed_experiment": "synthetic-ci",
                "split": "dev",
                "n": 1,
                "categories": {"retrieval_miss": 1},
                "cases": [
                    {
                        "id": "synthetic-ci-case",
                        "description": "合成未调味冻牛肉（仅CI）",
                        "reference_code": "0202300090",
                        "category": "retrieval_miss",
                        "eligible_for_training": False,
                        "review_status": "pending_expert_review",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    Assets(s.database).import_bundle(
        {
            "kind": "synthetic",
            "source": "browser-ci-fixture",
            "synthetic": True,
            "bills": [
                {
                    "codeTs": "0202300090",
                    "description": "商品名称：变质牛肉",
                    "alignment_status": "aligned",
                    "elements": [{"code": "0000", "name": "商品名称", "value": "变质牛肉"}],
                }
            ],
            "negative": [
                {
                    "knowledge_id": "CI-RISK",
                    "fit_area": "02",
                    "element_code": "0000",
                    "vocabulary": "变质",
                    "threshold": 1.0,
                }
            ],
            "dictionaries": [
                {
                    "kind": "risk",
                    "name": "CI合成风险词典",
                    "entries": [],
                    "config": {
                        "matching": "exact",
                        "coverage": [{"prefix": "02", "elements": ["0000"]}],
                        "element_names": {"商品名称": "0000"},
                    },
                },
                {
                    "kind": "synonym",
                    "name": "合成synonym词典",
                    "entries": [{"term": "冻制", "canonical": "冷冻"}, {"term": "剔骨", "canonical": "去骨"}],
                },
            ],
        }
    )
    fd = os.open(parent / "credentials.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump({"username": "admin", "password": password}, out)
    stopping = threading.Event()

    def worker():
        while not stopping.is_set():
            if not process_one(s):
                stopping.wait(0.25)

    threading.Thread(target=worker, daemon=True).start()
    try:
        uvicorn.run(create_app(s), host="127.0.0.1", port=18001, log_level="warning")
    finally:
        stopping.set()


if __name__ == "__main__":
    main()
