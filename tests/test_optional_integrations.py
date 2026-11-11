import asyncio
import json

import numpy as np
import pytest


def test_qdrant_excludes_evaluation_texts(tmp_path, settings):
    pytest.importorskip("qdrant_client")
    from qdrant_client import QdrantClient

    from tariff_agent.history import History

    rows = [
        {"description": "留出集商品", "gold_code": "990101", "source": "synthetic"},
        {"description": "训练参考商品", "gold_code": "990101", "source": "synthetic"},
    ]
    source, exclude = tmp_path / "source.jsonl", tmp_path / "heldout.jsonl"
    source.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    exclude.write_text(json.dumps(rows[0], ensure_ascii=False), encoding="utf-8")
    settings.history_directory = tmp_path / "qdrant-history"
    settings.embedding_model = "synthetic-encoder"
    settings.embedding_dimensions = 3
    history = History(settings, QdrantClient(":memory:"))

    def embed(texts):
        return np.asarray([[1.0, 0.0, 0.0] for _ in texts])

    result = history.build(source, [exclude], embed, {"990101"}, 10)
    assert result["indexed"] == 1
    assert history.search("商品", embed)[0]["description"] == "训练参考商品"


def test_mcp_tools_are_discoverable_and_validate_paths(settings, monkeypatch):
    pytest.importorskip("fastmcp")
    from fastmcp import Client

    import tariff_agent.mcp_server as module

    monkeypatch.setattr(module, "Settings", lambda: settings)

    async def run():
        async with Client(module.create_server()) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == {
                "search_headings",
                "lookup_notes",
                "validate_code_path",
                "tax_source_risk_check",
                "tax_language_normalize",
                "search_historical_cases",
            }
            risk = await client.call_tool(
                "tax_source_risk_check",
                {"description": "变质牛肉", "declared_code": "0202300090", "governance_profile": "sandbox"},
            )
            assert "blocked" in risk.content[0].text
            result = await client.call_tool("validate_code_path", {"code": "990101"})
            assert "990101" in result.content[0].text

    asyncio.run(run())
