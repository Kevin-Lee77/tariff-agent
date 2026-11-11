"""Select representative, conflict-free references from the full sanitized warehouse."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from tariff_agent.benchmark_data import group_identity, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("warehouse", type=Path)
    parser.add_argument("previous_benchmark", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--limit", type=int, default=300000)
    parser.add_argument("--per-code", type=int, default=128)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    excluded = {
        group_identity(r["description"])
        for s in ("dev", "heldout")
        for r in rows(args.previous_benchmark / (s + "_pool.jsonl"))
    }
    db = sqlite3.connect(f"file:{(args.warehouse / 'dedup.sqlite').as_posix()}?mode=ro", uri=True)
    conflicts = {r[0] for r in db.execute("SELECT DISTINCT family FROM cases WHERE conflict=1")}
    counts, labels, families = Counter(), Counter(), set()
    with (args.output / "history.jsonl").open("w", encoding="utf-8") as target:
        for norm, family, code, text, first, frequency in db.execute(
            "SELECT norm,family,code,description,first_row,frequency FROM cases WHERE conflict=0 AND covered=1 ORDER BY frequency DESC,norm"
        ):
            counts["compatible_scanned"] += 1
            bucket = int(hashlib.sha256(("tariff-grouped-v1" + family).encode()).hexdigest()[:8], 16) % 100
            if family in conflicts:
                counts["conflict_family_excluded"] += 1
                continue
            if family in excluded or bucket >= 80:
                counts["evaluation_family_excluded"] += 1
                continue
            if family in families or labels[code] >= args.per_code or counts["selected"] >= args.limit:
                counts["representative_budget_excluded"] += 1
                continue
            families.add(family)
            labels[code] += 1
            counts["selected"] += 1
            record = {
                "id": norm,
                "description": text,
                "gold_code": code,
                "source": "historical_all_data",
                "source_row": first,
                "frequency": frequency,
                "group_id": family,
                "split": "history",
                "label_status": "historical_reference_unverified",
            }
            target.write(json.dumps(record, ensure_ascii=False) + "\n")
    db.close()
    manifest = {
        "version": "large-representative-v1",
        "counts": dict(counts),
        "distinct_codes": len(labels),
        "excluded_parent_groups": len(excluded),
        "group_overlap": len(families & excluded),
        "per_code_cap": args.per_code,
        "family_cap": 1,
        "index_budget": args.limit,
        "warehouse_counts": json.loads((args.warehouse / "manifest.json").read_text())["counts"],
        "policy": "Frequency-first representatives, one per numeric template family, per-code cap; entire warehouse retained separately.",
        "label_status": "historical_reference_unverified",
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
