"""Add chapter, long-tail and text-length views to completed immutable evaluation outputs."""

import argparse
import json
from collections import Counter, defaultdict

from tariff_agent.benchmark_data import rows
from tariff_agent.config import Settings
from tariff_agent.evaluation import score


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment")
    args = parser.parse_args()
    settings = Settings()
    folder = settings.evaluation_directory / args.experiment
    if folder.parent.resolve() != settings.evaluation_directory.resolve():
        raise ValueError("Invalid experiment")
    summary = json.loads((folder / "summary.json").read_text())
    if summary["suite"] not in ("retrieval", "agent"):
        raise ValueError("This analysis needs historical label data")
    reference = {r["id"]: r for r in rows(settings.benchmark_directory / (summary["split"] + ".jsonl"))}
    frequencies = Counter(r["gold_code"] for r in rows(settings.benchmark_directory / "history.jsonl"))
    groups = defaultdict(list)
    for result in rows(folder / "results.jsonl"):
        original = reference[result["id"]]
        count = frequencies[original["gold_code"]]
        length = len(original["description"])
        groups["chapter:" + original["gold_code"][:2]].append(result)
        groups[
            "frequency:" + ("unseen" if not count else "tail_1_to_5" if count <= 5 else "head_gt_5")
        ].append(result)
        groups[
            "length:"
            + ("short_lt_100" if length < 100 else "medium_100_to_500" if length <= 500 else "long_gt_500")
        ].append(result)
    result = {
        "name": summary["name"],
        "label_status": summary["label_status"],
        "groups": {name: score(values) for name, values in groups.items()},
    }
    (folder / "slices.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print({"experiment": summary["name"], "slice_count": len(groups)})


if __name__ == "__main__":
    main()
