import argparse
import json
from pathlib import Path

from tariff_agent.benchmark_data import prepare
from tariff_agent.config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/private/historical_cases.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/private/benchmark-industry-v1"))
    parser.add_argument("--dev", type=int, default=3000)
    parser.add_argument("--heldout", type=int, default=10000)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(args.source, Settings().corpus, args.output, args.dev, args.heldout), ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
