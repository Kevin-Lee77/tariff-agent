"""Budgeted dense coverage, with truthful partial-index readiness and resumable batches."""

import argparse
import json

from tariff_agent.clients import Models
from tariff_agent.config import Settings
from tariff_agent.history import History


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=2048)
    args = parser.parse_args()
    settings = Settings()
    models, index = Models(settings), History(settings)
    try:
        index.attach_dense(models.embed, limit=args.limit)
        print(json.dumps(index.status(), ensure_ascii=False))
    finally:
        index.close()
        models.close()


if __name__ == "__main__":
    main()
