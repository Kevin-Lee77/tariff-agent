"""One bounded real-model integration using Qdrant cases; no expert accuracy claim."""

import argparse
import json
from collections import Counter
from pathlib import Path

from pydantic import SecretStr

from tariff_agent.clients import Models
from tariff_agent.config import Settings
from tariff_agent.domain import CaseInput
from tariff_agent.knowledge import Knowledge
from tariff_agent.store import configured_store
from tariff_agent.workflow import Workflow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hybrid", action="store_true")
    args = parser.parse_args()
    settings = Settings().model_copy(
        update={
            "database": Path(
                "data/private/gpu-agent-integration.db"
                if args.hybrid
                else "data/private/qdrant-agent-integration.db"
            ),
            "database_url": SecretStr(""),
            "production": False,
            "strict_readiness": False,
            "dense_enabled": args.hybrid,
            "rerank_enabled": args.hybrid,
            "history_enabled": True,
            "qdrant_catalog_enabled": True,
        }
    )
    store = configured_store(settings)
    body = CaseInput(
        description="商品名称：冷冻去骨牛肉；来源：家牛；部位：牛后腿肌肉，不含内脏和骨头；用途：供人食用；加工方式：去骨后冷冻，未经腌制、调味或熟制；饲养方式：草饲，非谷饲；包装：20千克纸箱。",
        retrieval_profile="hybrid" if args.hybrid else "bm25_diagnostic",
    )
    job = store.submit(
        "case", body.model_dump(), "gpu-hybrid-real-workflow-v1" if args.hybrid else "qdrant-real-workflow-v2"
    )
    if job.get("result"):
        result = job["result"]
    else:
        lease = store.claim(1800, 3)
        model = Models(settings)
        model.bind(store, job["id"], lease["token"])
        try:
            result = Workflow(Knowledge(settings.corpus), model, store, settings).run(
                lease, scope="classification_component"
            )
            store.finish(job["id"], lease["token"], result["status"], result)
        except Exception as exc:
            store.finish(job["id"], lease["token"], "failed", error=type(exc).__name__)
            raise
        finally:
            model.close()
    events = store.events(job["id"])
    report = {
        "scope": "classification_component_integration_not_benchmark",
        "synthetic_description": True,
        "status": result["status"],
        "historical_references": len(result.get("historical_cases", [])),
        "history_backend": "qdrant",
        "catalog_candidates": len(result.get("retrieved", [])),
        "recommendation": result.get("votes", {}).get("top_code"),
        "model_requests": sum(e["stage"] == "model_request" for e in events),
        "requests_by_provider": dict(
            Counter(e["data"].get("provider", "deepseek") for e in events if e["stage"] == "model_request")
        ),
        "retrieval_profile": body.retrieval_profile,
        "stages": [e["stage"] for e in events if e["stage"] not in ("model_request", "model_usage")],
        "formal_business_approval": False,
    }
    Path(
        ".runtime/gpu-agent-integration.json" if args.hybrid else ".runtime/qdrant-agent-integration.json"
    ).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
