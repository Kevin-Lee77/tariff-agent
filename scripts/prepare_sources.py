"""Read-only source extraction; writes only this project's data/private directory.

Use the Codex bundled Python locally, or install openpyxl for standalone imports.
No spreadsheets or original case records are modified. No model calls occur here.
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import openpyxl


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--catalog-root", type=Path, required=True)
    p.add_argument("--cases", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "tariff.json"
    if target.exists():
        raise SystemExit("Output exists; use a new versioned output directory")
    entries, provenance = {}, []
    for path in sorted((args.catalog_root / "2.label_files_manual").glob("*.xlsx")):
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        provenance.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        for sheet in wb:
            for row_no, row in enumerate(sheet.iter_rows(values_only=True), 1):
                raw, text = row[:2] if len(row) >= 2 else (None, None)
                code = str(raw).strip() if raw is not None else ""
                if re.fullmatch(r"\d{2}(?:\d{2}){0,4}", code) and text:
                    entries[code] = {
                        "code": code,
                        "parent": code[:-2] if len(code) > 2 else None,
                        "text": str(text),
                        "source_id": f"manual:{path.stem}:{sheet.title}:{row_no}",
                    }
        wb.close()
    official = args.catalog_root / "Fw_税则&注释(2)" / "2026十位税号税则.xlsx"
    wb = openpyxl.load_workbook(official, read_only=True, data_only=True)
    official_codes = set()
    for row_no, row in enumerate(wb["商品编码"].iter_rows(values_only=True), 1):
        code = str(row[0]).strip() if row[0] is not None else ""
        if re.fullmatch(r"\d{10}", code) and row[1]:
            official_codes.add(code)
            entries[code] = {
                "code": code,
                "parent": code[:-2],
                "text": str(row[1]),
                "source_id": f"catalog-2026:商品编码:{row_no}",
            }
    wb.close()
    provenance.append({"file": official.name, "sha256": hashlib.sha256(official.read_bytes()).hexdigest()})
    derived = []
    # Prefix nodes are explicit index artifacts, never fabricated legal descriptions.
    for code in list(entries):
        for length in range(2, len(code), 2):
            parent = code[:length]
            if parent not in entries:
                entries[parent] = {
                    "code": parent,
                    "parent": parent[:-2] if length > 2 else None,
                    "text": f"编码索引前缀 {parent}（派生节点，非税则条文）",
                    "source_id": "derived-index:" + parent,
                }
                derived.append(parent)
    corpus = {
        "jurisdiction": "CN",
        "version": "local-catalog-2026-v1",
        "source": "2026十位税号税则 + label_files_manual; notes not yet ingested; label year not verified",
        "test_only": False,
        "entries": [entries[k] for k in sorted(entries)],
    }
    target.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    data = json.loads(args.cases.read_text(encoding="utf-8-sig"))
    seen, counts = set(), Counter()
    dev, test = [], []
    with (args.output / "historical_cases.jsonl").open("x", encoding="utf-8") as out:
        for row in data:
            problem = str(row.get("problem", ""))
            # Keep only product description/specification; remove fiscal and declaration identifiers.
            description = re.split(r"申报备注[:：]|产销国[:：]|实际价格税率[:：]|应价格税率[:：]", problem)[
                0
            ].strip()
            gold = str(row.get("solution", "")).strip()
            if not description or not re.fullmatch(r"\d{10}", gold):
                counts["invalid"] += 1
                continue
            fingerprint = hashlib.sha256(description.encode()).hexdigest()
            if fingerprint in seen:
                counts["duplicate_description"] += 1
                continue
            seen.add(fingerprint)
            counts["retained"] += 1
            if gold not in official_codes:
                counts["gold_not_in_2026_catalog"] += 1
            record = {
                "id": str(row.get("id", "")),
                "description": description,
                "gold_code": gold,
                "fingerprint": fingerprint,
                "source": "historical_user_dataset",
                "catalog_2026_covered": gold in official_codes,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            # Deterministic split by text hash. No gold labels enter retrieval corpus.
            if int(fingerprint[:8], 16) % 10 == 0 and len(test) < 100:
                test.append(record)
            elif len(dev) < 100:
                dev.append(record)
    for name, rows in [("dev_cases.jsonl", dev), ("heldout_cases.jsonl", test)]:
        (args.output / name).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )
    manifest = {
        "provenance": provenance,
        "counts": dict(counts),
        "entries": len(entries),
        "official_10digit_codes": len(official_codes),
        "derived_prefixes": len(derived),
        "notes_status": "not_ingested",
        "historical_year": "unknown",
        "sensitive_fields": "fiscal amounts, rates, declaration remarks and identifiers removed",
        "case_source_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
    }
    (args.output / "source_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
