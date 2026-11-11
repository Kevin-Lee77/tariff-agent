"""Bounded real-model component smoke with an injected failure and native checkpoint recovery."""

import argparse
import json
from collections import Counter
from pathlib import Path

from pydantic import SecretStr

from tariff_agent.agent_evaluation import inspect_run
from tariff_agent.agent_memory import AgentMemory
from tariff_agent.clients import Models
from tariff_agent.config import Settings
from tariff_agent.domain import CaseInput
from tariff_agent.knowledge import Knowledge
from tariff_agent.store import configured_store
from tariff_agent.workflow import Workflow


class InjectedFailure(RuntimeError):
    pass


class FaultOnce(Models):
    fail_once = True

    def structured(self, stage, payload, schema, teacher=False):
        if stage == "l2_note_confirmation" and self.fail_once:
            self.fail_once = False
            raise InjectedFailure("Intentional failure before the L2 provider request")
        return super().structured(stage, payload, schema, teacher)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-model", action="store_true")
    args = parser.parse_args()
    if not args.real_model:
        raise SystemExit("Use --real-model to authorize this bounded integration smoke")
    settings = Settings().model_copy(
        update={
            "database": Path("data/private/durable-agent-integration.db"),
            "database_url": SecretStr(""),
            "production": False,
            "strict_readiness": False,
        }
    )
    store = configured_store(settings)
    case = CaseInput(
        description="商品名称：冷冻去骨牛肉；来源：家牛；部位：牛后腿肌肉，不含内脏和骨头；用途：供人食用；加工方式：去骨后冷冻，未经腌制、调味或熟制；饲养方式：草饲，非谷饲；包装：20千克纸箱。",
        retrieval_profile="hybrid",
    )
    job = store.submit("case", case.model_dump(), "durable-hybrid-component-v1")
    knowledge = Knowledge(settings.corpus, settings.allow_test_corpus)
    model = FaultOnce(settings)
    try:
        if job["status"] != "awaiting_review":
            for attempt in range(2):
                if store.get(job["id"])["status"] == "failed":
                    store.retry(job["id"], 3)
                lease = store.claim(settings.lease_seconds, 3)
                if lease is None or lease["id"] != job["id"]:
                    raise RuntimeError("Integration task lease unavailable")
                model.bind(store, job["id"], lease["token"])
                try:
                    result = Workflow(knowledge, model, store, settings).run(
                        lease, scope="classification_component"
                    )
                    store.finish(job["id"], lease["token"], result["status"], result)
                except InjectedFailure:
                    store.finish(job["id"], lease["token"], "failed", error="InjectedFailure")
                    continue
                except Exception as exc:
                    if store.owns(job["id"], lease["token"]):
                        store.finish(job["id"], lease["token"], "failed", error=type(exc).__name__)
                    raise
                break
        current = store.get(job["id"])
        assert current["status"] == "awaiting_review"
        events = store.events(job["id"], limit=5000)
        counts = Counter(e["stage"] for e in events)
        assert counts["attributes"] == counts["retrieve"] == counts["l1"] == 1
        assert counts["checkpoint_resume"] >= 1
        inspected = inspect_run(current["result"], events, knowledge, "classification_component")
        assert inspected["trace"]["score"] and inspected["memory_scope_valid"]
        report = {
            "status": "passed",
            "scope": "classification_component_integration_not_accuracy_benchmark",
            "synthetic_description": True,
            "injected_failure_before": "l2",
            "earlier_successful_stages_not_repeated": True,
            "stage_counts": dict(counts),
            "runtime": inspected,
            "memory_runs": len(AgentMemory(store, settings).case_memory(job["id"])["runs"]),
            "formal_business_approval": False,
        }
        Path(".runtime/durable-agent-integration.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))
    finally:
        model.close()


if __name__ == "__main__":
    main()
