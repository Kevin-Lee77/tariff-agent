"""Real-provider component integration; force one evidence-gap probe without claiming accuracy."""

import argparse
import json
from collections import Counter
from pathlib import Path

from pydantic import SecretStr

from tariff_agent.agent_evaluation import inspect_run
from tariff_agent.clients import Models
from tariff_agent.config import Settings
from tariff_agent.domain import CaseInput
from tariff_agent.knowledge import Knowledge
from tariff_agent.store import configured_store
from tariff_agent.workflow import Workflow


class EvidenceProbe(Models):
    probe_injected = False

    def structured(self, stage, payload, schema, teacher=False):
        output = super().structured(stage, payload, schema, teacher)
        if stage == "l2_note_confirmation" and not self.probe_injected:
            self.probe_injected = True
            return output.model_copy(
                update={
                    "citations": [],
                    "conflicts": [
                        "integration_probe_please_lookup_current_candidate_notes_before_final_judgement"
                    ],
                }
            )
        return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-model", action="store_true")
    args = parser.parse_args()
    if not args.real_model:
        raise SystemExit("Explicit --real-model required")
    settings = Settings().model_copy(
        update={
            "database": Path("data/private/context-agent-integration.db"),
            "database_url": SecretStr(""),
            "production": False,
            "strict_readiness": False,
            "context_max_input_tokens": 6000,
        }
    )
    store = configured_store(settings)
    case = CaseInput(
        description="商品名称：冷冻去骨牛肉；来源：家牛；部位：牛后腿肌肉，不含内脏和骨头；用途与功能：供人食用；形态：肉块；加工方式：去骨后冷冻，未经腌制、调味或熟制；饲养方式：草饲，非谷饲；包装：20千克纸箱。"
    )
    job = store.submit("case", case.model_dump(), "context-agentic-component-v1")
    knowledge = Knowledge(settings.corpus, settings.allow_test_corpus)
    model = EvidenceProbe(settings)
    try:
        if job["status"] in ("queued", "failed"):
            if job["status"] == "failed":
                store.retry(job["id"], settings.max_attempts)
            lease = store.claim(settings.lease_seconds, settings.max_attempts)
            model.bind(store, job["id"], lease["token"])
            try:
                result = Workflow(knowledge, model, store, settings).run(
                    lease, scope="classification_component"
                )
                store.finish(job["id"], lease["token"], result["status"], result)
            except Exception as exc:
                if store.owns(job["id"], lease["token"]):
                    store.finish(job["id"], lease["token"], "failed", error=type(exc).__name__)
                raise
        current = store.get(job["id"])
        assert current["status"] == "awaiting_review", current["status"]
        events = store.events(job["id"], limit=5000)
        inspected = inspect_run(current["result"], events, knowledge, "classification_component")
        assert inspected["trace"]["score"] and inspected["context_protected_facts_valid"]
        assert inspected["exploration_scope_valid"] and inspected["context_omitted_records"] > 0
        assert any(
            row["stage"] == "model_request" and row["data"]["stage"] == "l2_exploration_plan"
            for row in events
        )
        current_events = [
            row for row in events if row["data"].get("run_id") == current["result"]["provenance"]["run_id"]
        ]
        report = {
            "status": "passed",
            "scope": "classification_component_integration_not_accuracy_benchmark",
            "synthetic_description": True,
            "first_l2_evidence_gap_injected": True,
            "runtime": inspected,
            "successful_run_stage_counts": dict(Counter(row["stage"] for row in current_events)),
            "all_attempt_stage_counts": dict(Counter(row["stage"] for row in events)),
            "exploration_stop_reason": current["result"].get("exploration", {}).get("stop_reason"),
            "formal_business_approval": False,
        }
        Path(".runtime/context-agentic-integration.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        print(json.dumps(report, ensure_ascii=False))
    finally:
        model.close()


if __name__ == "__main__":
    main()
