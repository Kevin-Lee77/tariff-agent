"""Explicit evaluation gates; missing metrics fail closed and retrieval is not an Agent evaluation."""

import argparse
import json
import math
from pathlib import Path


def assess(summary, policy):
    failures = []
    if summary.get("suite") != policy["suite"]:
        failures.append("suite_mismatch")
    if summary.get("status") != "completed":
        failures.append("experiment_not_completed")
    if summary.get("n_completed", 0) < policy.get("min_cases", 1):
        failures.append("insufficient_cases")
    metrics = summary.get("metrics", {})
    for direction, comparator in [
        ("minimum", lambda value, bound: value >= bound),
        ("maximum", lambda value, bound: value <= bound),
    ]:
        for metric, bound in policy.get(direction, {}).items():
            value = metrics
            for part in metric.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if not isinstance(bound, (int, float)) or not math.isfinite(bound):
                raise ValueError("Gate thresholds must be finite numbers")
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                failures.append("missing_metric:" + metric)
            elif not comparator(value, bound):
                failures.append("threshold_failed:" + metric)
    return {
        "passed": not failures,
        "failures": failures,
        "experiment": summary.get("name"),
        "suite": summary.get("suite"),
        "n": summary.get("n_completed"),
        "scope": "Configured measured checks only; legal/business correctness requires expert acceptance",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args()
    result = assess(json.loads(args.summary.read_text()), json.loads(args.policy.read_text()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 2)


if __name__ == "__main__":
    main()
