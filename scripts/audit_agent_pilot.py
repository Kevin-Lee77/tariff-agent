"""Summarize completed pilot results and private stage/model events; no new inference."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from tariff_agent.benchmark_data import rows
from tariff_agent.stage_metrics import fraction


def audit(directory):
    summary = json.loads((directory / "summary.json").read_text())
    records = list(rows(directory / "results.jsonl"))
    assert summary["status"] == "completed" and len(records) == summary["n_completed"] == summary["n"]
    assert {row["id"] for row in records} == set(summary["selected_ids"])
    if not (directory / "summary.original.json").exists():
        (directory / "summary.original.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    summary = summary | {
        "pilot": True,
        "dataset_sha256": hashlib.sha256((directory / "selected.jsonl").read_bytes()).hexdigest(),
        "variant": "hybrid_history_attribute_query",
        "paid_generation_used": True,
        "report_metadata_note": "Explicit development subset; original summary retained; metrics unchanged",
    }
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    providers = Counter()
    requests = Counter()
    usage = defaultdict(Counter)
    attribute_teacher = 0
    exploration_cases = 0
    planning_cases = 0
    unique_citations = 0
    citation_occurrences = 0
    badcases = []
    before = after = omitted = 0
    for record in records:
        with sqlite3.connect(directory / "cases" / (record["id"] + ".db")) as db:
            events = [
                (stage, json.loads(data))
                for stage, data in db.execute("select stage,data from events order by seq")
            ]
            final_json = db.execute("select result from jobs").fetchone()[0]
        final = json.loads(final_json or "{}")
        citations = final.get("citations", [])
        citation_occurrences += len(citations)
        unique_citations += len({(row["source_id"], row["quote"]) for row in citations})
        exploration_cases += record.get("exploration_actions", 0) > 0
        planning_cases += any(
            stage == "model_request" and data.get("stage") == "l2_exploration_plan" for stage, data in events
        )
        teacher = False
        for stage, data in events:
            if stage == "model_request":
                providers[data.get("provider", "unknown")] += 1
                name = data["stage"]
                phase = (
                    "subheading"
                    if name.startswith("subheading_under_parent")
                    else "verify"
                    if name.startswith("final_vote_")
                    else name
                )
                requests[phase] += 1
                teacher |= name == "extract_attributes" and data.get("provider") == "deepseek"
            elif stage == "model_usage":
                usage[data.get("model", "unknown")].update(
                    {k: v for k, v in data.get("usage", {}).items() if isinstance(v, (int, float))}
                )
            elif stage == "context_prepared":
                before += data["input_before"]
                after += data["input_after"]
                omitted += data["omitted_records"]
        attribute_teacher += teacher
        reference = record["gold_code"]
        if record["status"] == "failed":
            category = "execution_failure"
        elif record["status"] == "awaiting_information":
            category = "clarification_required_unadjudicated"
        else:
            category = "historical_label_agreement"
            for key in ("retrieved", "shortlist", "ranked"):
                if not any(code[:4] == reference[:4] for code in record.get(key, [])):
                    category = key + "_reference_heading_lost"
                    break
            else:
                if (record.get("predicted_codes") or [None])[0] != reference:
                    category = "subheading_or_final_historical_label_mismatch"
        if category != "historical_label_agreement":
            badcases.append(
                {
                    "id": record["id"],
                    "category": category,
                    "status": record["status"],
                    "reference_code": reference,
                    "predicted_codes": record.get("predicted_codes", []),
                    "questions": record.get("questions", []),
                    "eligible_for_training": False,
                    "review_status": "pending_expert_review",
                }
            )
    result = {
        "name": summary["name"],
        "n": len(records),
        "complete": True,
        "scope": summary["scope"],
        "selection": summary["selection"],
        "label_status": summary["label_status"],
        "metrics": summary["metrics"],
        "stage_metrics": summary["stage_metrics"],
        "providers_request_counts": dict(providers),
        "phase_request_counts": dict(requests),
        "provider_reported_usage": {key: dict(value) for key, value in usage.items()},
        "attribute_deepseek_participation": fraction(attribute_teacher, len(records)),
        "exploration": {
            "planner_cases": planning_cases,
            "cases_with_tool_actions": exploration_cases,
            "actions": sum(record.get("exploration_actions", 0) for record in records),
        },
        "citations": {
            "occurrences": citation_occurrences,
            "unique_within_each_case": unique_citations,
            "semantic_entailment_accuracy": None,
            "validation_is_code_enforced": True,
        },
        "context_proxy_totals": {
            "before": before,
            "after": after,
            "omitted_records": omitted,
            "reduction": (before - after) / before if before else None,
            "billing_savings_measured": False,
        },
        "badcase_categories": dict(Counter(row["category"] for row in badcases)),
        "badcases": badcases,
        "holdout_agent_dataset_completed": False,
        "full_100_case_dev_agent_dataset_completed": False,
        "semantic_attribute_accuracy": None,
        "semantic_citation_accuracy": None,
        "clarification_correctness": None,
    }
    result["stage_top1_reference_agreement"] = {}
    for key, digits in (("shortlist", 4), ("ranked", 4), ("resolved", 10), ("predicted_codes", 10)):
        reached = [row for row in records if row.get(key)]
        hits = sum(
            bool(row.get(key)) and row[key][0][:digits] == row["gold_code"][:digits] for row in records
        )
        result["stage_top1_reference_agreement"][key] = {
            "all_cases": fraction(hits, len(records)),
            "nonempty_stage_cases": fraction(hits, len(reached)),
        }
    output = Path("docs/evaluation/agent-pilot-dev20-audit.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.directory), ensure_ascii=False))


if __name__ == "__main__":
    main()
