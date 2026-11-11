"""Import local legacy CSV as UNREVIEWED policy. Never infer approval or coverage."""

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rules = []
    rejected = 0
    with args.csv.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            area = (row.get("fitArea") or "").strip()
            vocabulary = (row.get("vocabulary") or "").strip()
            try:
                threshold = float(row.get("threshold") or 95)
                if threshold > 1:
                    threshold /= 100
                if not re.fullmatch(r"\d{2,10}", area) or not vocabulary or not 0 <= threshold <= 1:
                    raise ValueError("unsupported rule")
            except ValueError:
                rejected += 1
                continue
            rules.append(
                {
                    "knowledge_id": row.get("knowledgeCode") or row["knowledgeId"],
                    "fit_area": area,
                    "element_code": row["elementCode"],
                    "vocabulary": vocabulary,
                    "threshold": threshold,
                    "review_comment": row.get("reviewComment") or "请核实申报内容并补充业务复核意见",
                }
            )
    result = {
        "version": "legacy-csv-" + hashlib.sha256(args.csv.read_bytes()).hexdigest()[:16],
        "source_file": args.csv.name,
        "synthetic": False,
        "approved": False,
        "matching": "word2vec",
        "coverage": [],
        "element_names": {"品名": "0000", "商品名称": "0000"},
        "rules": rules,
        "rejected_rows": rejected,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"imported": len(rules), "rejected": rejected, "approved": False}))


if __name__ == "__main__":
    main()
