"""Development-only failure triage. Historical labels require review before training."""

import json
from collections import Counter
from pathlib import Path

from .benchmark_data import rows


def category(row):
    if row.get("status") == "failed":
        return "execution_failure"
    heading = row["gold_code"][:4]

    def hit(codes):
        return any(code[:4] == heading for code in codes)

    stages = row.get("retrieval_stages", {})
    if "retrieved" in row and not hit(row["retrieved"]):
        if hit(stages.get("fused_recall", [])):
            return "rerank_dropped_heading"
        if hit(stages.get("catalog_recall", [])) or hit(stages.get("history_heading_recall", [])):
            return "fusion_dropped_heading"
        return "retrieval_miss"
    for stage in ("shortlist", "ranked", "resolved"):
        if stage in row and not hit(row[stage]):
            return stage + "_lost_heading"
    if row.get("status") == "awaiting_information":
        return "clarification_required"
    if row.get("predicted_codes") and row["gold_code"] != row["predicted_codes"][0]:
        return "subheading_mismatch"
    if "retrieved" in row and not hit(row["retrieved"][:10]):
        return "low_rank_heading"
    return None


def mine(benchmark, experiment, output):
    benchmark, experiment, output = Path(benchmark), Path(experiment), Path(output)
    metadata = json.loads((experiment / "experiment.json").read_text(encoding="utf-8"))
    if metadata["split"] != "dev":
        raise ValueError("Do not mine a tuning bank from the blind acceptance set")
    sources = {r["id"]: r for r in rows(benchmark / "dev.jsonl")}
    cases = []
    for result in rows(experiment / "results.jsonl"):
        reason = category(result)
        if reason:
            source = sources[result["id"]]
            cases.append(
                {
                    "id": source["id"],
                    "group_id": source["group_id"],
                    "description": source["description"],
                    "reference_code": result["gold_code"],
                    "category": reason,
                    "review_status": "pending_expert_review",
                    "label_status": source["label_status"],
                    "seed_experiment": experiment.name,
                    "retrieved": result.get("retrieved", []),
                    "predicted_codes": result.get("predicted_codes", []),
                    "retrieval_stages": result.get("retrieval_stages", {}),
                    "error_type": result.get("error_type"),
                    "eligible_for_training": False,
                }
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError("Badcase banks are immutable; choose a new version")
    report = {
        "version": 1,
        "seed_experiment": experiment.name,
        "split": "dev",
        "n": len(cases),
        "categories": dict(Counter(r["category"] for r in cases)),
        "policy": "Triage hypotheses, not adjudicated causes. Never auto-publish labels or train on blind failures.",
        "cases": cases,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {k: v for k, v in report.items() if k != "cases"}


def compare(bank, experiment):
    source = json.loads(Path(bank).read_text(encoding="utf-8"))
    experiment = Path(experiment)
    current = {r["id"]: r for r in rows(experiment / "results.jsonl")}
    seed_folder = experiment.parent / source["seed_experiment"]
    seed_meta = json.loads((seed_folder / "experiment.json").read_text())
    candidate_meta = json.loads((experiment / "experiment.json").read_text())
    if seed_meta.get("dataset_sha256") != candidate_meta.get("dataset_sha256"):
        raise ValueError("Badcase regression requires the same frozen dataset")
    seed_ids = {r["id"] for r in rows(seed_folder / "results.jsonl")} | {r["id"] for r in source["cases"]}
    known_bad = {r["id"] for r in source["cases"]}
    statuses = Counter()
    for case in source["cases"]:
        row = current.get(case["id"])
        statuses["missing" if row is None else "unresolved" if category(row) else "resolved"] += 1
    return {
        "seed": source["seed_experiment"],
        "candidate": Path(experiment).name,
        "bank_size": source["n"],
        "counts": dict(statuses),
        "resolved_rate": statuses["resolved"] / source["n"] if source["n"] else None,
        "all_cases_covered": statuses["missing"] == 0,
        "all_seed_cases_covered": seed_ids <= set(current),
        "new_failure_ids": [
            key for key, row in current.items() if key in seed_ids - known_bad and category(row)
        ],
    }
