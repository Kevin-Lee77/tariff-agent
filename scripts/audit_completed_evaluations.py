"""Read immutable existing reports and derive the recorded retrieval funnel, no model calls."""

import json
from collections import Counter
from pathlib import Path

from tariff_agent.benchmark_data import rows
from tariff_agent.stage_metrics import retrieval_funnel


def main():
    root = Path("data/private/evaluation-runs")
    reports = {}
    for name in (
        "compact-v2-dev-baseline",
        "compact-v2-dev-hybrid",
        "compact-v2-heldout-baseline",
        "compact-v2-heldout-hybrid",
        "governance-1000-v1",
    ):
        directory = root / name
        summary = json.loads((directory / "summary.json").read_text())
        records = list(rows(directory / "results.jsonl"))
        assert summary["status"] == "completed" and len(records) == summary["n_completed"]
        value = {
            "name": name,
            "n": len(records),
            "metrics": summary["metrics"],
            "dataset_sha256": summary["dataset_sha256"],
            "code_sha256": summary["code_sha256"],
            "label_status": summary["label_status"],
            "scope": summary["scope"],
        }
        if summary["suite"] == "retrieval" and "hybrid" in name:
            value["funnel"] = retrieval_funnel(records)
        if summary["suite"] == "governance":
            value["risk_decision_match_rate"] = summary.get("risk_decision_match_rate")
            labelled = [row for row in records if row.get("expected_decision") is not None]
            value["expected_decisions"] = dict(Counter(row["expected_decision"] for row in labelled))
            value["expected_decision_labels_persisted_in_results"] = len(labelled)
            value["observed_statuses"] = dict(Counter(row.get("status") for row in records))
            value["normalize_reached_cases"] = sum(
                "normalize" in row.get("trace", {}).get("actual", []) for row in records
            )
            value["explicit_language_status_checks"] = sum(
                "expected_language_status_matched" in row for row in records
            )
            value["language_status_matches"] = sum(
                row.get("expected_language_status_matched") is True for row in records
            )
            value["semantic_terminology_accuracy"] = None
        reports[name] = value
        if summary["suite"] == "retrieval" and "hybrid" in name:
            transitions = {}
            for k in (10, 20, 40):
                counts = Counter()
                for row in records:
                    trace = row.get("retrieval_stages", {})
                    before = any(c[:4] == row["gold_code"][:4] for c in trace.get("fused_recall", [])[:k])
                    after = any(c[:4] == row["gold_code"][:4] for c in trace.get("reranked", [])[:k])
                    counts[
                        "retained"
                        if before and after
                        else "lost"
                        if before
                        else "gained"
                        if after
                        else "both_missed"
                    ] += 1
                transitions[str(k)] = dict(counts)
            value["fusion_to_rerank_paired_counts"] = transitions
    result = {
        "audit_scope": "existing frozen historical reports, not a rerun of the latest Agent workflow",
        "reports": reports,
        "agent_full_dataset_completed": False,
    }
    output = Path("docs/evaluation/stage-metrics-audit.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
