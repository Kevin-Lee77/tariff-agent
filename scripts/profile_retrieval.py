"""Report stage latency without logging private declarations or credentials."""

import json
import time

from tariff_agent.clients import Models
from tariff_agent.config import Settings
from tariff_agent.history import History
from tariff_agent.knowledge import Knowledge
from tariff_agent.retrieval import recall


def main():
    timings = []

    class Timed(Models):
        def embed_query(self, texts):
            start = time.monotonic()
            result = super().embed_query(texts)
            timings.append({"stage": "query_embedding", "seconds": time.monotonic() - start})
            return result

        def rerank(self, query, documents):
            start = time.monotonic()
            result = super().rerank(query, documents)
            timings.append(
                {"stage": "rerank", "documents": len(documents), "seconds": time.monotonic() - start}
            )
            return result

    original = History.search

    def measured(index, *args, **kwargs):
        start = time.monotonic()
        result = original(index, *args, **kwargs)
        timings.append({"stage": index.kind, "seconds": time.monotonic() - start})
        return result

    History.search = measured
    settings = Settings()
    model = Timed(settings)
    try:
        knowledge = Knowledge(settings.corpus)
        for i in range(2):
            start = time.monotonic()
            recall(settings, knowledge, model, "冷冻去骨牛肉，未经调味，供人食用", include_references=False)
            timings.append({"stage": "total", "round": i, "seconds": time.monotonic() - start})
        print(json.dumps(timings, indent=2))
    finally:
        model.close()


if __name__ == "__main__":
    main()
