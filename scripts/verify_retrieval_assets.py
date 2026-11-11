"""Read back actual vector counts and audit full retrieval/evaluation family separation."""

import argparse
import hashlib
import json
from pathlib import Path

from qdrant_client import models

from tariff_agent.benchmark_data import group_identity, rows
from tariff_agent.config import Settings
from tariff_agent.history import History, catalog_settings
from tariff_agent.knowledge import Knowledge
from tariff_agent.model_transport import gateway_health


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    valid = Knowledge(settings.corpus).formal_codes
    parent = Path("data/private/benchmark-industry-v1")
    excluded = {
        group_identity(r["description"])
        for split in ("dev", "heldout")
        for r in rows(parent / (split + "_pool.jsonl"))
    }
    groups = {}
    for split in ("dev", "heldout"):
        groups[split] = {
            group_identity(r["description"]) for r in rows(settings.benchmark_directory / (split + ".jsonl"))
        }
    assert not groups["dev"] & groups["heldout"]
    assert (groups["dev"] | groups["heldout"]) <= excluded
    overlap, invalid, count = 0, 0, 0
    for row in rows(Path("data/private/history-large-v2/history.jsonl")):
        overlap += group_identity(row["description"]) in excluded
        invalid += row["gold_code"] not in valid
        count += 1
    assert overlap == 0 and invalid == 0 and count == 300000
    indexes = {}
    for name, config, kind, expected in [
        ("history", settings, "historical_case", 2048),
        ("catalog", catalog_settings(settings), "tariff_rule", 1231),
    ]:
        index = History(config, kind=kind)
        try:
            state = index.status()
            actual = index.client.count(
                index.manifest["collection"],
                exact=True,
                count_filter=models.Filter(must=[models.HasVectorCondition(has_vector="qwen3_8b")]),
            ).count
            assert actual == expected == state["dense_count"]
            indexes[name] = state | {"actual_dense_points": actual}
            if args.snapshot:
                indexes[name]["snapshot"] = index.snapshot()
        finally:
            index.close()
    archive = Path("data/private/case-warehouse-v1/historical_cases.large.jsonl.gz")
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    assert digest.hexdigest() == "b2708f31880fa0d63e7b810ec3a4fec9ecb1348930f4bd9921e64679c6fc7898"
    report = {
        "status": "passed",
        "gateway": gateway_health(settings),
        "indexes": indexes,
        "history_rows_audited": count,
        "excluded_families": len(excluded),
        "overlap": overlap,
        "invalid_codes": invalid,
        "compact_groups": {k: len(v) for k, v in groups.items()},
        "warehouse_archive_sha256": digest.hexdigest(),
    }
    Path(".runtime/retrieval-assets-verification.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
