import json

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tariff_agent.badcases import category, compare, mine
from tariff_agent.clients import Models
from tariff_agent.domain import Attributes
from tariff_agent.gpu_gateway import create_app
from tariff_agent.retrieval import fuse_headings


def test_gpu_endpoints_auth_and_query_document_separation(tmp_path):
    class Backend:
        def embed(self, texts, query):
            assert query
            return np.array([[1.0, 0.0] for _ in texts])

        def rerank(self, query, documents):
            return [0.1, 0.9]

        def generate(self, messages, max_tokens, temperature):
            return '{"material":"牛肉"}', {"total_tokens": 12}

    with TestClient(create_app(tmp_path, "x" * 32, Backend())) as client:
        assert client.get("/health").status_code == 401
        headers = {"Authorization": "Bearer " + "x" * 32}
        assert client.get("/health", headers=headers).json()["small_quantization"] == "nf4"
        result = client.post(
            "/v1/embeddings", headers=headers, json={"input": ["冻牛肉"], "input_type": "query"}
        )
        assert result.json()["data"][0]["embedding"] == [1.0, 0.0]
        assert (
            client.post(
                "/rerank", headers=headers, json={"query": "肉", "documents": ["塑料", "牛肉"]}
            ).json()["results"][0]["index"]
            == 1
        )
        assert (
            client.post(
                "/v1/chat/completions",
                headers=headers,
                json={"messages": [{"role": "user", "content": "抽取"}]},
            ).json()["usage"]["total_tokens"]
            == 12
        )
        assert (
            client.post(
                "/v1/embeddings", headers=headers, json={"input": ["x"], "model": "wrong"}
            ).status_code
            == 400
        )


def test_local_small_model_failure_escalates_with_separate_credentials(settings, monkeypatch):
    monkeypatch.setattr("tariff_agent.clients.time.sleep", lambda _: None)
    settings.small_model_enabled = True
    settings.small_model_url = "https://gpu.test/v1"
    settings.deepseek_url = "https://teacher.test/v1"
    settings.model_gateway_key = SecretStr("g" * 32)
    settings.deepseek_api_key = SecretStr("d" * 32)
    requests = []

    def handler(request):
        requests.append(request.url.host)
        if request.url.host == "gpu.test":
            assert request.headers["Authorization"] == "Bearer " + "g" * 32
            return httpx.Response(503)
        assert request.headers["Authorization"] == "Bearer " + "d" * 32
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"material":"牛肉"}'}}]})

    models = Models(settings)
    models.http.close()
    models.http = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        assert models.structured("extract_attributes", {}, Attributes).material == "牛肉"
        assert requests == ["gpu.test", "gpu.test", "teacher.test"]
        requests.clear()
        models.structured("extract_attributes", {}, Attributes, teacher=True)
        assert requests == ["teacher.test"]
    finally:
        models.close()


def test_history_candidates_can_recover_missing_heading_without_invalid_codes():
    refs = [{"reference_code": "0202300090"}, {"reference_code": "9999999999"}]
    candidates = fuse_headings(["0101", "0102"], refs, {"0101", "0102", "0202"})
    assert "0202" in candidates and "9999" not in candidates


def test_badcase_triage_and_regression_keep_missing_cases(tmp_path):
    lost = {"gold_code": "0202300090", "retrieved": ["0101"], "retrieval_stages": {"fused_recall": ["0202"]}}
    assert category(lost) == "rerank_dropped_heading"
    benchmark, run = tmp_path / "benchmark", tmp_path / "run"
    benchmark.mkdir()
    run.mkdir()
    (benchmark / "dev.jsonl").write_text(
        json.dumps(
            {
                "id": "a",
                "description": "牛肉",
                "gold_code": "0202300090",
                "group_id": "family",
                "label_status": "historical_reference_unverified",
            }
        ),
        encoding="utf-8",
    )
    (run / "experiment.json").write_text('{"split":"dev"}')
    (run / "results.jsonl").write_text(json.dumps(lost | {"id": "a", "status": "completed"}))
    bank = tmp_path / "bank.json"
    assert mine(benchmark, run, bank)["n"] == 1
    (run / "results.jsonl").write_text("")
    report = compare(bank, run)
    assert report["counts"]["missing"] == 1 and report["resolved_rate"] == 0
    (run / "experiment.json").write_text('{"split":"heldout"}')
    with pytest.raises(ValueError, match="blind"):
        mine(benchmark, run, tmp_path / "forbidden.json")


def test_rerank_fetch_does_not_silently_cap_recall_at_20(settings, tmp_path):
    pytest.importorskip("qdrant_client")
    from qdrant_client import QdrantClient

    from tariff_agent.history import History

    settings.history_directory = tmp_path / "index"
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"description": "商品案例" + chr(0x4E00 + i), "gold_code": "990101", "source": "test"})
            for i in range(35)
        ),
        encoding="utf-8",
    )
    index = History(settings, QdrantClient(":memory:"))
    try:
        index.build(path, [])
        result = index.search("商品", limit=40, rerank=lambda q, d: list(range(len(d))))
        assert len(result) == 35
    finally:
        index.close()


def test_partial_dense_index_is_searchable_without_claiming_full_coverage(settings, tmp_path):
    pytest.importorskip("qdrant_client")
    from qdrant_client import QdrantClient

    from tariff_agent.history import History

    settings.history_directory = tmp_path / "partial"
    settings.embedding_dimensions = 3
    source = tmp_path / "cases.jsonl"
    source.write_text(
        "\n".join(
            json.dumps(
                {
                    "description": "历史参考" + chr(0x4E00 + i),
                    "gold_code": "990101" if i < 8 else "990201",
                    "source": "test",
                }
            )
            for i in range(15)
        ),
        encoding="utf-8",
    )
    index = History(settings, QdrantClient(":memory:"))

    def embed(texts):
        return np.asarray([[1.0, 0.0, 0.0] for _ in texts])

    try:
        index.build(source, [])
        index.attach_dense(embed, limit=4)
        status = index.status()
        assert status["dense_count"] == 4 and status["points"] == 15
        assert status["dense_search_ready"] and not status["dense_ready"]
        assert len(index.search("no-lexical-overlap", embed=embed, limit=3)) == 3
        with pytest.raises(ValueError, match="shrink"):
            index.attach_dense(embed, limit=3)
        index.attach_dense(embed)
        assert index.status()["dense_ready"] and index.status()["dense_count"] == 15
        index.attach_dense(embed, limit=4)
        assert index.status()["dense_ready"] and index.status()["dense_count"] == 15
    finally:
        index.close()


def test_badcase_regression_detects_new_failures_and_dataset_drift(tmp_path):
    seed, candidate = tmp_path / "seed", tmp_path / "candidate"
    seed.mkdir()
    candidate.mkdir()
    good = {"gold_code": "0202300090", "retrieved": ["0202"], "status": "completed"}
    bad = good | {"retrieved": ["0101"]}
    for folder in (seed, candidate):
        (folder / "experiment.json").write_text('{"dataset_sha256":"frozen"}')
    (seed / "results.jsonl").write_text(json.dumps(bad | {"id": "a"}) + "\n" + json.dumps(good | {"id": "b"}))
    (candidate / "results.jsonl").write_text(
        json.dumps(good | {"id": "a"}) + "\n" + json.dumps(bad | {"id": "b"})
    )
    bank = tmp_path / "bank.json"
    bank.write_text('{"seed_experiment":"seed","n":1,"cases":[{"id":"a"}]}')
    report = compare(bank, candidate)
    assert report["counts"]["resolved"] == 1 and report["new_failure_ids"] == ["b"]
    assert report["all_seed_cases_covered"]
    (candidate / "experiment.json").write_text('{"dataset_sha256":"changed"}')
    with pytest.raises(ValueError, match="same frozen dataset"):
        compare(bank, candidate)
