"""Offline scorer; only compare a fixed run with explicit labels. Never invent metrics."""

import argparse
import json


def score(rows):
    if not rows:
        raise ValueError("Empty evaluation set")
    result = {"n": len(rows)}
    for level in (4, 6, 8, 10):
        eligible = [r for r in rows if len(r["gold_code"]) >= level]
        if not eligible:
            continue
        for k in (1, 3):
            hits = sum(
                any(c[:level] == r["gold_code"][:level] for c in r.get("predicted_codes", [])[:k])
                for r in eligible
            )
            result[f"top{k}_{level}digit"] = {
                "hits": hits,
                "n": len(eligible),
                "accuracy": hits / len(eligible),
            }
    stages = ("retrieved", "shortlist", "ranked", "resolved")
    lost = dict.fromkeys(stages, 0)
    for row in rows:
        for stage in stages:
            if stage in row and not any(c[:4] == row["gold_code"][:4] for c in row[stage]):
                lost[stage] += 1
                break
    result["first_loss_stage"] = lost
    recall_rows = [r for r in rows if "retrieved" in r]
    if recall_rows:
        for k in (10, 20, 40):
            hits = sum(any(c[:4] == r["gold_code"][:4] for c in r["retrieved"][:k]) for r in recall_rows)
            result[f"heading_recall@{k}"] = {
                "hits": hits,
                "n": len(recall_rows),
                "value": hits / len(recall_rows),
            }
        reciprocal = []
        for row in recall_rows:
            ranks = [i + 1 for i, code in enumerate(row["retrieved"]) if code[:4] == row["gold_code"][:4]]
            reciprocal.append(1 / ranks[0] if ranks else 0)
        result["heading_mrr@40"] = sum(reciprocal) / len(reciprocal)
    result["failures"] = sum(r.get("status") == "failed" for r in rows)
    result["no_prediction"] = sum(not r.get("predicted_codes") for r in rows)
    traces = [r for r in rows if "trace" in r]
    if traces:
        result["trajectory_pass_rate"] = sum(r["trace"]["score"] for r in traces) / len(traces)
        gated = [r for r in traces if r.get("risk_gate_respected") is not None]
        result["risk_gate_pass_rate"] = (
            sum(r["risk_gate_respected"] for r in gated) / len(gated) if gated else None
        )
        cited = sum(r.get("citation_count", 0) for r in traces)
        result["quote_verification_rate"] = (
            sum(r.get("verified_citations", 0) for r in traces) / cited if cited else None
        )
        proposals = [r for r in traces if r.get("predicted_codes")]
        result["citation_coverage"] = (
            sum(r.get("citation_presence", False) for r in proposals) / len(proposals) if proposals else None
        )
        result["path_legality_rate"] = (
            sum(r.get("path_valid") is True for r in proposals) / len(proposals) if proposals else None
        )
        result["model_requests"] = sum(r.get("model_requests", 0) for r in traces)
        result["total_tokens"] = sum(r.get("tokens", 0) for r in traces)
        result["runtime_versioned_rate"] = sum(bool(r.get("runtime_versioned")) for r in traces) / len(traces)
        result["memory_scope_violations"] = sum(r.get("memory_scope_valid") is not True for r in traces)
        result["illegal_parent_decisions"] = sum(len(r.get("illegal_parent_decisions", [])) for r in traces)
        result["context_before_proxy_tokens"] = sum(r.get("context_before_proxy_tokens", 0) for r in traces)
        result["context_after_proxy_tokens"] = sum(r.get("context_after_proxy_tokens", 0) for r in traces)
        result["context_omitted_records"] = sum(r.get("context_omitted_records", 0) for r in traces)
        result["context_fact_violations"] = sum(
            r.get("context_protected_facts_valid") is False for r in traces
        )
        result["exploration_scope_violations"] = sum(
            r.get("exploration_scope_valid") is False for r in traces
        )
        manifested = [r for r in traces if r.get("citation_count", 0)]
        result["evidence_fingerprint_rate"] = (
            sum(r.get("evidence_fingerprints_valid") is True for r in manifested) / len(manifested)
            if manifested
            else None
        )
        result["target_resolution_rate"] = (
            sum(r.get("target_resolved") is True for r in proposals) / len(proposals) if proposals else None
        )
    latencies = sorted(r["elapsed_seconds"] for r in rows if "elapsed_seconds" in r)
    if latencies:
        result["latency_seconds"] = {
            "p50": latencies[(len(latencies) - 1) // 2],
            "p95": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))],
        }
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("jsonl")
    args = p.parse_args()
    with open(args.jsonl, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    print(json.dumps(score(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
