import argparse
import json
from pathlib import Path

from tariff_agent.badcases import compare, mine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["mine", "compare"])
    parser.add_argument("experiment", type=Path)
    parser.add_argument("--benchmark", type=Path, default=Path("data/private/benchmark-compact-v2"))
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = (
        mine(args.benchmark, args.experiment, args.bank)
        if args.mode == "mine"
        else compare(args.bank, args.experiment)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as target:
            json.dump(report, target, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
