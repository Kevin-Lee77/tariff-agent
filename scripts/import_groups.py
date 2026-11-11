"""Preserve odd-length source grouping prefixes as structure, never final HS codes."""

import argparse
import json
import re
from pathlib import Path

import openpyxl


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--labels", type=Path, required=True)
    p.add_argument("--corpus", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise SystemExit("Choose a new versioned output path")
    data = json.loads(a.corpus.read_text(encoding="utf-8"))
    formal = {e["code"] for e in data["entries"]}
    groups = {}
    for path in sorted(a.labels.glob("*.xlsx")):
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sheet in wb:
            for row_no, row in enumerate(sheet.iter_rows(values_only=True), 1):
                code = str(row[0]).strip() if row and row[0] is not None else ""
                if (
                    re.fullmatch(r"\d{5}(?:\d{2}){0,2}", code)
                    and code[:-1] in formal
                    and len(row) > 1
                    and row[1]
                ):
                    groups[code] = {
                        "code": code,
                        "parent": code[:-1],
                        "text": str(row[1]),
                        "source_id": f"structural-group:{path.stem}:{sheet.title}:{row_no}",
                    }
        wb.close()
    data["groups"] = [groups[k] for k in sorted(groups)]
    data["version"] += "+structural-groups-v1"
    data["source"] = data["source"].replace("notes not yet ingested;", "")
    a.output.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print("Structural grouping prefixes:", len(groups))


if __name__ == "__main__":
    main()
