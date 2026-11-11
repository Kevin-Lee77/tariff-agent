"""Standalone Flask component evaluation on a private business-DB snapshot and explicit rule oracles."""

import argparse
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

from pydantic import SecretStr

from tariff_agent.assets import Assets
from tariff_agent.benchmark_data import rows
from tariff_agent.config import Settings
from tariff_agent.flask_services import create_service
from tariff_agent.persistence import database_target, engine_for
from tariff_agent.stage_metrics import fraction, quantiles


def prf(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def identifier_metrics(cases, results):
    expected = {row["id"]: re.findall(r"批次：([^;；\n]*)", row["description"]) for row in cases}
    eligible = [row for row in results if expected[row["id"]]]
    hits = sum(
        re.findall(r"批次：([^;；\n]*)", row["predicted_text"]) == expected[row["id"]] for row in eligible
    )
    return fraction(hits, len(eligible))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="governance-component-metrics-v1")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", args.name):
        raise ValueError("Invalid experiment name")
    settings = Settings()
    target = Path("data/private") / args.name
    target.mkdir(exist_ok=False)
    engine = engine_for(database_target(settings))
    assert engine.dialect.name == "sqlite"
    snapshot = target / "snapshot.db"
    with sqlite3.connect(engine.url.database) as source, sqlite3.connect(snapshot) as destination:
        source.backup(destination)
    snapshot.chmod(0o600)
    isolated = settings.model_copy(
        update={
            "database": snapshot,
            "database_url": SecretStr(""),
            "production": False,
            "governance_transport": "embedded",
            "otlp_endpoint": "",
        }
    )
    key = isolated.api_key.get_secret_value()
    source_client = create_service("source", isolated).test_client()
    language_client = create_service("language", isolated).test_client()
    dataset = Path("data/private/benchmark-industry-v1/governance-v1.jsonl")
    manifest = json.loads(dataset.with_name("governance-manifest.json").read_text())
    assert hashlib.sha256(dataset.read_bytes()).hexdigest() == manifest["files"][dataset.name]
    cases = list(rows(dataset))
    source_records = []
    labels = ("blocked", "clear", "information_required", "review_required")
    confusion = {gold: {pred: 0 for pred in labels} for gold in labels}
    span_tp = span_fp = span_fn = 0
    risk_tp = risk_fp = risk_fn = 0
    latency = []
    for case in cases:
        payload = {name: case[name] for name in ("description", "declared_code", "elements")}
        payload["governance_profile"] = "sandbox"
        started = time.monotonic()
        response = source_client.post("/v1/declarations/check", json=payload, headers={"X-API-Key": key})
        response_data = response.get_json()
        assert response.status_code == 200
        duration = time.monotonic() - started
        latency.append(duration)
        predicted = response_data["decision"]
        expected = case["expected_decision"]
        confusion[expected][predicted] += 1
        if expected in ("blocked", "clear"):
            truth = expected == "blocked"
            hit = predicted == "blocked"
            risk_tp += truth and hit
            risk_fp += not truth and hit
            risk_fn += truth and not hit
        expected_spans = {
            (match.start(), match.end(), match[0])
            for match in re.finditer(re.escape("【联调风险】"), case["description"])
        }
        observed = {(row["start"], row["end"], row["text"]) for row in response_data["annotations"]}
        span_tp += len(expected_spans & observed)
        span_fp += len(observed - expected_spans)
        span_fn += len(expected_spans - observed)
        source_records.append(
            {
                "id": case["id"],
                "scenario": case["scenario"],
                "gold_decision": expected,
                "predicted_decision": predicted,
                "span_tp": len(expected_spans & observed),
                "span_fp": len(observed - expected_spans),
                "span_fn": len(expected_spans - observed),
                "seconds": duration,
            }
        )
    (target / "source-results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in source_records))
    macro = []
    for label in labels:
        values = prf(
            confusion[label][label],
            sum(confusion[g][label] for g in labels if g != label),
            sum(confusion[label][p] for p in labels if p != label),
        )
        macro.append(values["f1"] or 0)
    source_report = {
        "n": len(cases),
        "label_source": "frozen synthetic rule oracle, not expert risk judgement",
        "scenario_counts": dict(Counter(row["scenario"] for row in cases)),
        "decision_match": fraction(
            sum(row["gold_decision"] == row["predicted_decision"] for row in source_records), len(cases)
        ),
        "decision_macro_f1": sum(macro) / len(macro),
        "decision_confusion": confusion,
        "risk_detection_in_covered_complete_cases": {
            "n": sum(row["expected_decision"] in ("blocked", "clear") for row in cases),
            **prf(risk_tp, risk_fp, risk_fn),
        },
        "original_annotation_exact_span": prf(span_tp, span_fp, span_fn),
        "latency_seconds": quantiles(latency),
        "word2vec_semantic_accuracy": None,
    }

    # Explicit contract-only additions live in the snapshot; never publish them to business dictionaries.
    assets = Assets(snapshot)
    assets.import_bundle(
        {
            "kind": "component-evaluation",
            "source": "explicit-synthetic-contracts",
            "synthetic": True,
            "dictionaries": [
                {
                    "kind": "synonym",
                    "name": "歧义契约测试",
                    "entries": [
                        {"term": "合成歧义词", "canonical": "合成规范甲"},
                        {"term": "合成歧义词", "canonical": "合成规范乙"},
                    ],
                },
                {
                    "kind": "standard",
                    "name": "字段范围契约测试",
                    "entries": [
                        {
                            "term": "合成冻藏表达",
                            "canonical": "冷冻",
                            "element_code": "STORAGE",
                            "element_name": "保存方法",
                            "scope": "02",
                        }
                    ],
                },
            ],
        }
    )
    config = assets.language_policy(True, "0202300090")
    candidates = {}
    independent = set(config.get("independent_words", []))
    independent.update(row["term"] for row in config["entries"] if row["kind"] in ("independent", "negative"))
    for row in config["entries"]:
        if row["kind"] not in ("synonym", "standard") or row["element_code"] or row["element_name"]:
            continue
        if (
            row["term"] == row["canonical"]
            or not 2 <= len(row["term"]) <= 15
            or not 2 <= len(row["canonical"]) <= 15
        ):
            continue
        if row["term"].startswith(("不", "非", "无", "未")) or any(
            word in row["term"] for word in independent
        ):
            continue
        candidates.setdefault(row["term"], set()).add(row["canonical"])
    pairs = [(alias, next(iter(mains))) for alias, mains in candidates.items() if len(mains) == 1]
    pairs.sort(key=lambda pair: hashlib.sha256(json.dumps(pair, ensure_ascii=False).encode()).hexdigest())
    pairs = pairs[:40]
    assert pairs
    language_cases = []

    def add(category, text, expected, spans=(), warnings=(), code="0202300090"):
        language_cases.append(
            {
                "id": "language-" + str(len(language_cases)),
                "category": category,
                "description": text,
                "declared_code": code,
                "expected_text": expected,
                "expected_protected_texts": list(spans),
                "expected_warning_types": sorted(warnings),
            }
        )

    for index, (alias, canonical) in enumerate(pairs):
        add("synonym_canonical", f"商品名称：{alias}；批次：{index}", f"商品名称：{canonical}；批次：{index}")
        text = f"不含{alias}；批次：{index}"
        add("negative_protection", text, text, [f"不含{alias}"])
        text = f"未添加{alias}，商品名称：{alias}；批次：{index}"
        add(
            "negative_scope_and_positive",
            text,
            f"未添加{alias}，商品名称：{canonical}；批次：{index}",
            [f"未添加{alias}"],
        )
    for word in ("不锈钢", "无缝钢管", "无纺布", "非金属矿"):
        add("independent_word", word + "；冻制剔骨牛肉", word + "；冷冻去骨牛肉", [word])
    add("mutex_positive", "冷冻冷藏牛肉", "冷冻冷藏牛肉", warnings=["mutually_exclusive_terms"])
    add("mutex_negative", "冷冻牛肉，不含冷藏肉", "冷冻牛肉，不含冷藏肉", ["不含冷藏肉"])
    add("ambiguous_alias", "合成歧义词", "合成歧义词", warnings=["ambiguous_alias"])
    add("ambiguous_negative", "不含合成歧义词", "不含合成歧义词", ["不含合成歧义词"])
    add("scoped_standard", "保存方法：合成冻藏表达", "保存方法：冷冻")
    add("wrong_field_scope", "用途：合成冻藏表达", "用途：合成冻藏表达")
    add("wrong_chapter_scope", "保存方法：合成冻藏表达", "保存方法：合成冻藏表达", code="3901100001")
    for word in ("其他货物", "东西", "一批货", "详见附件", "不清楚"):
        add("nonstandard_warning", word, word, warnings=["non_standard_value"])
    (target / "language-cases.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in language_cases)
    )
    results = []
    warning_tp = warning_fp = warning_fn = 0
    protected_total = protected_ok = 0
    language_latency = []
    for case in language_cases:
        started = time.monotonic()
        response = language_client.post(
            "/v1/declarations/normalize",
            json={
                "description": case["description"],
                "declared_code": case["declared_code"],
                "governance_profile": "sandbox",
            },
            headers={"X-API-Key": key},
        )
        assert response.status_code == 200
        predicted = response.get_json()
        duration = time.monotonic() - started
        language_latency.append(duration)
        observed = {row["reason"] for row in predicted["warnings"]}
        expected = set(case["expected_warning_types"])
        warning_tp += len(observed & expected)
        warning_fp += len(observed - expected)
        warning_fn += len(expected - observed)
        count = len(case["expected_protected_texts"])
        kept = sum(text in predicted["normalized_text"] for text in case["expected_protected_texts"])
        protected_total += count
        protected_ok += kept
        results.append(
            {
                "id": case["id"],
                "category": case["category"],
                "exact_text_match": predicted["normalized_text"] == case["expected_text"],
                "protected_total": count,
                "protected_kept": kept,
                "warning_types_expected": sorted(expected),
                "warning_types_observed": sorted(observed),
                "predicted_text": predicted["normalized_text"],
                "seconds": duration,
            }
        )
    (target / "language-results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results)
    )
    categories = {}
    for category in sorted({row["category"] for row in results}):
        subset = [row for row in results if row["category"] == category]
        categories[category] = {
            "n": len(subset),
            "exact_text_match": fraction(sum(row["exact_text_match"] for row in subset), len(subset)),
            "protected_text_preserved": fraction(
                sum(row["protected_kept"] for row in subset), sum(row["protected_total"] for row in subset)
            ),
        }
    language_report = {
        "n": len(results),
        "label_source": "dictionary-derived canonical targets and explicit synthetic scope contracts; not expert semantic labels",
        "sampled_alias_pairs": len(pairs),
        "categories": categories,
        "exact_text_match": fraction(sum(row["exact_text_match"] for row in results), len(results)),
        "protected_text_preserved": fraction(protected_ok, protected_total),
        "warning_type_micro": prf(warning_tp, warning_fp, warning_fn),
        "latency_seconds": quantiles(language_latency),
        "unknown_clustering_semantic_accuracy": None,
        "identifier_value_preserved": identifier_metrics(language_cases, results),
    }
    report = {
        "status": "completed",
        "scope": "standalone Flask components on isolated snapshot",
        "business_database_unchanged": True,
        "no_generation_model_calls": True,
        "source": source_report,
        "language": language_report,
        "files": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in target.glob("*.jsonl")
        },
    }
    previous = Path("data/private/governance-component-metrics-v1")
    if target != previous and (previous / "language-cases.jsonl").exists():
        previous_cases = list(rows(previous / "language-cases.jsonl"))
        previous_results = list(rows(previous / "language-results.jsonl"))
        previous_report = json.loads((previous / "summary.json").read_text())
        report["same_fixed_language_cases_as_v1"] = (
            report["files"]["language-cases.jsonl"] == previous_report["files"]["language-cases.jsonl"]
        )
        report["baseline_language_exact_match"] = previous_report["language"]["exact_text_match"]
        report["baseline_identifier_value_preserved"] = identifier_metrics(previous_cases, previous_results)
    report["code_sha256"] = hashlib.sha256(
        b"".join(path.read_bytes() for path in sorted(Path("src/tariff_agent").glob("*.py")))
    ).hexdigest()
    output = Path("docs/evaluation") / (args.name + ".json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (target / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
