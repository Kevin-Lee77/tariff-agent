"""Extract legacy local samples and generate reproducible, explicitly synthetic fixtures."""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(out, name, bundle):
    (out / name).write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("legacy_root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--generated-bills", type=int, default=3000)
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    base = args.legacy_root
    folder = base / "税智源" / "海关数据+word2vec" / "海关数据+word2vec" / "补充数据"
    aligned = []
    totals = {}
    for file in sorted(folder.glob("bill*.csv")):
        records = []
        with file.open(encoding="utf-8-sig", newline="") as source:
            for number, row in enumerate(csv.DictReader(source), 1):
                codes = (row.get("elementCodeCollection") or "").split("||")
                values = (row.get("elementValueCollection") or "").split("||")
                balanced = len(codes) == len(values)
                elements = [
                    {
                        "code": codes[i] if i < len(codes) else "",
                        "name": "商品名称" if i < len(codes) and codes[i] == "0000" else "",
                        "value": values[i] if i < len(values) else "",
                    }
                    for i in range(max(len(codes), len(values)))
                ]
                description = "；".join(
                    (e["name"] or e["code"] or "未映射要素") + "：" + e["value"] for e in elements
                )[:6000]
                record = row | {
                    "source_key": str(number),
                    "elements": elements,
                    "description": description,
                    "alignment_status": "aligned" if balanced else "requires_mapping",
                    "source_file": file.name,
                    "source_row": number,
                }
                records.append(record)
                if (
                    balanced
                    and re.fullmatch(r"\d{6,10}", row["codeTs"])
                    and any(e["code"] == "0000" and e["value"] for e in elements)
                ):
                    aligned.append(record)
        write(
            out,
            file.stem + ".json",
            {
                "kind": "bill",
                "source": file.name,
                "synthetic": False,
                "bills": records,
                "summary": {"aligned": sum(r["alignment_status"] == "aligned" for r in records)},
            },
        )
        totals[file.name] = len(records)
    for file in sorted(folder.glob("negative*.csv")):
        records = []
        with file.open(encoding="utf-8-sig", newline="") as source:
            for number, row in enumerate(csv.DictReader(source), 1):
                threshold = float(row.get("threshold") or 95)
                threshold = threshold / 100 if threshold > 1 else threshold
                area = (row.get("fitArea") or "").strip()
                records.append(
                    {
                        "knowledge_id": row.get("knowledgeCode") or row["knowledgeId"],
                        "fit_area": area,
                        "element_code": row["elementCode"],
                        "vocabulary": row["vocabulary"],
                        "threshold": threshold,
                        "review_comment": row.get("reviewComment") or "",
                        "status": "unreviewed"
                        if re.fullmatch(r"\d{2,10}", area) and 0 <= threshold <= 1
                        else "unsupported_scope",
                        "source_row": number,
                        "legacy": row,
                    }
                )
        write(
            out,
            file.stem + ".json",
            {
                "kind": "negative",
                "source": file.name,
                "synthetic": False,
                "negative": records,
                "dictionaries": [
                    {
                        "kind": "risk",
                        "name": file.stem,
                        "entries": [],
                        "config": {
                            "priority": 2024 if "2024" in file.name else 2023,
                            "matching": "word2vec",
                            "coverage": [],
                            "element_names": {"品名": "0000", "商品名称": "0000"},
                        },
                    }
                ],
            },
        )
        totals[file.name] = len(records)
    language = base / "税智语"
    standard = load(next((language / "大模型脚本").rglob("valid_rules.json")))
    dictionaries = []
    for chapter, scopes in standard.items():
        entries = [
            {
                "term": word,
                "scope": chapter,
                "element_name": "" if field == "ALL" else field,
                "payload": {"legacy_scope": field, "requires_element_review": field == "ALL"},
            }
            for field, words in scopes.items()
            for word in dict.fromkeys(words)
        ]
        dictionaries.append({"kind": "standard", "name": "历史规范词-" + chapter, "entries": entries})
    for file in [
        language / "20250605135600589_0213-params.txt",
        next((language / "大模型脚本").rglob("20251127145934990_0216-params.txt")),
    ]:
        data = load(file)
        entries = []
        frequencies = {}
        for group in data.get("param5", {}).values():
            if isinstance(group, dict):
                frequencies.update(group)
        for main, aliases in data.get("param4", {}).items():
            entries.extend(
                {
                    "term": word,
                    "canonical": main,
                    "scope": "02",
                    "frequency": frequencies.get(word, 0),
                    "payload": {"source_file": file.name, "source_group": main, "proposal_only": True},
                }
                for word in dict.fromkeys([main] + aliases)
            )
        dictionaries.append({"kind": "synonym", "name": "历史同义词候选-" + file.stem, "entries": entries})
        pending = [
            {"term": word, "scope": "02", "frequency": frequencies.get(word, 0)}
            for word in data.get("param2", [])
        ]
        dictionaries.append({"kind": "pending", "name": "历史未知词-" + file.stem, "entries": pending})
    corpus = load(next((language / "大模型脚本").rglob("313cf371-3b0f-4f9b-9d2f-03932941ee97.txt")))
    counts = Counter(
        (a.get("elementName", ""), a["analyzeContent"])
        for r in corpus
        for a in r.get("analyze", [])
        if a.get("analyzeContent")
    )
    dictionaries.append(
        {
            "kind": "observed",
            "name": "历史申报观测词-500条语料",
            "entries": [
                {
                    "term": word,
                    "scope": "02",
                    "element_name": name,
                    "frequency": count,
                    "payload": {"quality": "observed_not_normative"},
                }
                for (name, word), count in counts.items()
            ],
        }
    )
    write(
        out,
        "language-legacy.json",
        {
            "kind": "language",
            "source": "existing-rules-and-script-snapshots",
            "synthetic": False,
            "dictionaries": dictionaries,
            "summary": {"observed_declarations": len(corpus)},
        },
    )
    # Derive varied fixtures from actual aligned structures. No real enterprise identifiers are copied.
    chapters = sorted({r["codeTs"][:2] for r in aligned})
    negatives = [
        {
            "knowledge_id": "SYNTHETIC-" + chapter,
            "fit_area": chapter,
            "element_code": "0000",
            "vocabulary": "【联调风险】",
            "threshold": 1.0,
            "review_comment": "合成风险标记：请企业核实并修订申报",
            "status": "sandbox",
        }
        for chapter in chapters
    ]
    negatives.append(
        {
            "knowledge_id": "SANDBOX-02-001",
            "fit_area": "02",
            "element_code": "0000",
            "vocabulary": "变质",
            "threshold": 1.0,
            "review_comment": "联调风险示例，请核实商品状态",
            "status": "sandbox",
        }
    )
    synthetic_bills = []
    scenarios = ("clear", "risk_hit", "negation", "synonym", "missing_element", "mutex")
    grouped = {chapter: [r for r in aligned if r["codeTs"].startswith(chapter)] for chapter in chapters}
    for i in range(args.generated_bills):
        chapter = chapters[i % len(chapters)]
        samples = grouped[chapter]
        source = samples[(i // len(chapters)) % len(samples)]
        scenario = scenarios[(i // len(chapters)) % len(scenarios)]
        elements = [dict(e) for e in source["elements"]]
        product = next(e for e in elements if e["code"] == "0000")
        if scenario == "risk_hit":
            product["value"] += "【联调风险】"
        if scenario == "negation":
            product["value"] += "，不含测试添加剂"
        if scenario == "synonym" and chapter == "02":
            product["value"] = "冻制剔骨牛肉"
        if scenario == "mutex" and chapter == "02":
            product["value"] = "冷藏冷冻牛肉"
        if scenario == "missing_element":
            elements = [e for e in elements if e["code"] != "0000"]
        synthetic_bills.append(
            {
                "taskId": "SYNTHETIC-" + str(i),
                "billCode": "SYNTHETIC-" + str(i),
                "entryId": "",
                "orgCode": "SANDBOX",
                "codeTs": source["codeTs"],
                "elementCodeCollection": "||".join(e["code"] for e in elements),
                "elementValueCollection": "||".join(e["value"] for e in elements),
                "description": "；".join((e["name"] or e["code"]) + "：" + e["value"] for e in elements)[
                    :6000
                ],
                "elements": elements,
                "alignment_status": "aligned",
                "scenario": scenario,
                "derived_from": {"file": source["source_file"], "row": source["source_row"]},
                "synthetic": True,
            }
        )
    seed_dictionaries = [
        {
            "kind": "risk",
            "name": "合成风险词典-多章",
            "entries": [],
            "config": {
                "matching": "exact",
                "coverage": [{"prefix": c, "elements": ["0000"]} for c in chapters],
                "element_names": {"品名": "0000", "商品名称": "0000"},
                "priority": 1,
            },
        }
    ]
    for kind in (
        "synonym",
        "standard",
        "nonstandard",
        "negative",
        "independent",
        "mutex",
        "classification_mutex",
        "score",
        "element",
    ):
        entries = []
        if kind == "synonym":
            entries = [{"term": "冻制", "canonical": "冷冻"}, {"term": "剔骨", "canonical": "去骨"}]
            for version in dictionaries:
                if version["kind"] == "synonym":
                    entries.extend(
                        dict(
                            row,
                            payload=row.get("payload", {}) | {"synthetic": True, "not_expert_verified": True},
                        )
                        for row in version["entries"]
                    )
        elif kind == "standard":
            entries = [
                dict(row, payload=row.get("payload", {}) | {"synthetic": True})
                for row in dictionaries[0]["entries"]
            ]
        elif kind == "nonstandard":
            entries = [
                {"term": w, "scope": c, "payload": {"reason": "合成模糊申报表达，请补充属性"}}
                for c in chapters
                for w in ["其他货物", "东西", "一批货", "详见附件", "不清楚"]
            ]
        elif kind == "negative":
            entries = [{"term": w} for w in ["不含", "未添加", "非专用", "不适用", "无添加"]]
        elif kind == "independent":
            entries = [{"term": w} for w in ["不锈钢", "无缝钢管", "无纺布", "非金属矿"]]
        elif kind in ("mutex", "classification_mutex"):
            entries = [
                {
                    "term": a,
                    "scope": c,
                    "payload": {"group1": [a], "group2": [b], "reason": "合成互斥检查，需核实具体商品"},
                }
                for c in chapters
                for a, b in [("冷冻", "冷藏"), ("含糖", "不含糖"), ("已调制", "未经调制")]
            ]
        elif kind == "score":
            entries = [
                {
                    "term": w,
                    "scope": c,
                    "payload": {"score": 1, "reason": "仅演示规则命中计分，不是税号分类概率"},
                }
                for c in chapters
                for w in ["用途", "材质", "加工方式"]
            ]
        elif kind == "element":
            entries = [{"term": "商品名称", "canonical": "0000", "element_code": "0000"}]
        seed_dictionaries.append({"kind": kind, "name": "合成" + kind + "词典", "entries": entries})
    write(
        out,
        "synthetic-derived.json",
        {
            "kind": "synthetic",
            "source": "deterministic-derived-v1",
            "synthetic": True,
            "bills": synthetic_bills,
            "negative": negatives,
            "dictionaries": seed_dictionaries,
            "summary": {
                "chapters": chapters,
                "scenarios": list(scenarios),
                "seed": "deterministic-v1",
                "synthetic": True,
            },
        },
    )
    manifest = {
        "original_counts": totals,
        "generated_bills": len(synthetic_bills),
        "generated_rules": len(negatives),
        "chapters": len(chapters),
        "dictionary_entries": sum(len(v["entries"]) for v in dictionaries + seed_dictionaries),
        "files": [
            {"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(out.glob("*.json"))
            if p.name != "manifest.json"
        ],
    }
    write(out, "manifest.json", manifest)
    print(json.dumps(manifest | {"files": len(manifest["files"])}, ensure_ascii=True))


if __name__ == "__main__":
    main()
