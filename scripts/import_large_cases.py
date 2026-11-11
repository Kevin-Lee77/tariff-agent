"""Stream raw declaration arrays, retain only product fields, deduplicate and quarantine label conflicts."""

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import ijson

from tariff_agent.benchmark_data import group_identity, text_identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--corpus", type=Path, default=Path("data/private/tariff-complete.json"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    valid = {
        r["code"]
        for r in json.loads(args.corpus.read_text(encoding="utf-8"))["entries"]
        if len(r["code"]) == 10
    }
    db = sqlite3.connect(args.output / "dedup.sqlite")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        "CREATE TABLE cases (norm TEXT PRIMARY KEY, family TEXT, code TEXT, description TEXT, first_row INTEGER, frequency INTEGER, conflict INTEGER, covered INTEGER)"
    )
    counts = {"raw_rows": 0, "invalid_rows": 0}
    batch = []
    with args.source.open("rb") as source:
        for index, row in enumerate(ijson.items(source, "item", use_float=True), 1):
            counts["raw_rows"] += 1
            code = str(row.get("commodity_num", "")).strip()
            name = str(row.get("commodity_name") or "").strip()
            spec = str(row.get("commodity_spec") or "").strip()
            if not re.fullmatch(r"\d{10}", code) or not name:
                counts["invalid_rows"] += 1
                continue
            text = "商品名称: " + name + "。规格型号: " + spec + "。"
            if len(text) > 6000:
                counts["overlong_rows"] = counts.get("overlong_rows", 0) + 1
                continue
            batch.append(
                (text_identity(text), group_identity(text), code, text, index, 1, 0, int(code in valid))
            )
            if len(batch) >= 5000:
                db.executemany(
                    "INSERT INTO cases VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(norm) DO UPDATE SET frequency=frequency+1,conflict=MAX(conflict,cases.code!=excluded.code)",
                    batch,
                )
                db.commit()
                batch = []
            if index % 100000 == 0:
                print(json.dumps({"processed": index}), flush=True)
    if batch:
        db.executemany(
            "INSERT INTO cases VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(norm) DO UPDATE SET frequency=frequency+1,conflict=MAX(conflict,cases.code!=excluded.code)",
            batch,
        )
    db.commit()
    counts.update(
        unique_texts=db.execute("SELECT COUNT(*) FROM cases").fetchone()[0],
        conflicting_texts=db.execute("SELECT COUNT(*) FROM cases WHERE conflict=1").fetchone()[0],
        compatible_unique=db.execute("SELECT COUNT(*) FROM cases WHERE conflict=0 AND covered=1").fetchone()[
            0
        ],
    )
    with (args.output / "historical_cases.jsonl").open("w", encoding="utf-8") as target:
        for norm, family, code, text, first, frequency, conflict, covered in db.execute(
            "SELECT * FROM cases WHERE conflict=0 ORDER BY norm"
        ):
            record = {
                "id": norm,
                "description": text,
                "gold_code": code,
                "source": "historical_all_data",
                "normalized_fingerprint": norm,
                "group_id": family,
                "source_row": first,
                "frequency": frequency,
                "catalog_2026_covered": bool(covered),
                "label_status": "historical_reference_unverified",
            }
            target.write(json.dumps(record, ensure_ascii=False) + "\n")
    db.close()
    digest = hashlib.sha256()
    with args.source.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    manifest = {
        "source_file": args.source.name,
        "source_sha256": digest.hexdigest(),
        "counts": counts,
        "retained_fields": ["commodity_num", "commodity_name", "commodity_spec"],
        "excluded_fields": "remarks, enterprise identifiers, amounts, tax rates and quantities",
        "label_status": "historical_reference_unverified",
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
