"""Measured funnel metrics. Missing stages and failed cases stay in overall denominators."""

from collections import Counter


def fraction(hits, n):
    return {"hits": hits, "n": n, "value": hits / n if n else None}


def quantiles(values):
    values = sorted(values)
    if not values:
        return None
    return {
        "n": len(values),
        "p50": values[(len(values) - 1) // 2],
        "p95": values[min(len(values) - 1, int(len(values) * 0.95))],
    }


def retrieval_funnel(rows):
    result = {}
    for stage in ("catalog_recall", "history_heading_recall", "fused_recall", "reranked"):
        executed = [row for row in rows if stage in row.get("retrieval_stages", {})]
        result[stage] = {
            "recorded_cases": len(executed),
            "candidate_count": quantiles([len(row["retrieval_stages"][stage]) for row in executed]),
            "reference_present_in_pool": fraction(
                sum(
                    any(
                        code[:4] == row["gold_code"][:4]
                        for code in row.get("retrieval_stages", {}).get(stage, [])
                    )
                    for row in rows
                ),
                len(rows),
            ),
        }
        for k in (10, 20, 40):
            hits = sum(
                any(
                    code[:4] == row["gold_code"][:4]
                    for code in row.get("retrieval_stages", {}).get(stage, [])[:k]
                )
                for row in rows
            )
            result[stage][f"recall@{k}"] = fraction(hits, len(rows))
    return {
        "n": len(rows),
        "label_scope": "four-digit historical reference heading, not final classification accuracy",
        "stages": result,
        "failure_count": sum(row.get("status") == "failed" for row in rows),
    }


def agent_funnel(rows):
    n = len(rows)
    stages = {}
    for stage, limit in (("retrieved", 40), ("shortlist", 10), ("ranked", 3), ("resolved", None)):
        executed = [row for row in rows if stage in row]
        hits = sum(
            any(code[:4] == row["gold_code"][:4] for code in row.get(stage, [])[:limit]) for row in rows
        )
        reached_hits = sum(
            any(code[:4] == row["gold_code"][:4] for code in row.get(stage, [])[:limit]) for row in executed
        )
        stages[stage] = {
            "executed_cases": len(executed),
            "reference_heading_retention_all_cases": fraction(hits, n),
            "reference_heading_retention_reached_cases": fraction(reached_hits, len(executed)),
            "candidate_count": quantiles([len(row.get(stage, [])) for row in executed]),
        }
    extracted = [row for row in rows if row.get("attributes_extracted")]
    stages["attributes"] = {
        "executed_cases": len(extracted),
        "schema_valid_rate": fraction(len(extracted), n),
        "missing_information_cases": sum(bool(row.get("attribute_missing")) for row in extracted),
        "semantic_accuracy": None,
        "reason": "No expert property labels; schema/completion is not semantic accuracy",
    }
    statuses = Counter(row.get("status", "failed") for row in rows)
    stage_latencies = {}
    for stage in ("attributes", "retrieve", "l1", "l2", "subheading", "verify", "teacher"):
        stage_latencies[stage] = quantiles(
            [row["stage_seconds"][stage] for row in rows if stage in row.get("stage_seconds", {})]
        )
    known = sum(row.get("quote_verification_rate") is not None for row in rows)
    return {
        "n": n,
        "scope": "classification_component_pilot_not_business_acceptance",
        "statuses": dict(statuses),
        "stages": stages,
        "stage_latency_seconds": stage_latencies,
        "trajectory_pass_rate": fraction(sum(bool(row.get("trace", {}).get("score")) for row in rows), n),
        "model_requests": sum(row.get("model_requests", 0) for row in rows),
        "total_tokens": sum(row.get("tokens", 0) for row in rows),
        "semantic_citation_accuracy": None,
        "semantic_attribute_accuracy": None,
        "unlabelled_metrics_are_not_assumed_correct": True,
        "unused_per_row_quote_rate_count": known,
    }
