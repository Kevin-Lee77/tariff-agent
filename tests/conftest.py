import json

import pytest

from tariff_agent.config import Settings


@pytest.fixture
def settings(tmp_path):
    corpus = {
        "jurisdiction": "CN",
        "version": "synthetic-test-v1",
        "source": "test fixture only",
        "test_only": True,
        "entries": [
            {"code": "99", "text": "测试章", "source_id": "s99"},
            {"code": "9901", "parent": "99", "text": "测试塑料部件", "source_id": "s9901"},
            {"code": "990101", "parent": "9901", "text": "测试专用塑料部件", "source_id": "s990101"},
        ],
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    risk = tmp_path / "risk.json"
    risk.write_text(
        json.dumps(
            {
                "version": "test-only",
                "approved": True,
                "matching": "exact",
                "coverage": [{"prefix": "99", "elements": ["0000"]}],
                "rules": [
                    {
                        "knowledge_id": "fixture",
                        "fit_area": "99",
                        "element_code": "0000",
                        "vocabulary": "风险词",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    language = tmp_path / "language.json"
    language.write_text('{"version":"test-only","groups":{}}', encoding="utf-8")
    return Settings(
        _env_file=None,
        database=tmp_path / "app.db",
        corpus=path,
        rules=tmp_path / "rules.json",
        risk_rules=risk,
        language_rules=language,
        governance_transport="embedded",
        word2vec=tmp_path / "w2v.pkl",
        api_key="a" * 32,
        reviewer_key="b" * 32,
        allow_test_corpus=True,
        dense_enabled=False,
        rerank_enabled=False,
        strict_readiness=False,
    )
