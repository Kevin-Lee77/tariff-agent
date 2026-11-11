"""Frozen, chapter-stratified development pilot; private case DBs, bounded concurrency/calls."""

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import SecretStr

from tariff_agent.agent_evaluation import inspect_run
from tariff_agent.agent_telemetry import experiment_item
from tariff_agent.benchmark_data import rows
from tariff_agent.clients import Models
from tariff_agent.config import Settings
from tariff_agent.domain import CaseInput
from tariff_agent.evaluation import score
from tariff_agent.knowledge import Knowledge
from tariff_agent.model_transport import gateway_health
from tariff_agent.runtime_context import digest
from tariff_agent.stage_metrics import agent_funnel
from tariff_agent.store import configured_store
from tariff_agent.workflow import Workflow


def choose(records, n, seed="agent-pilot-20261109"):
    groups = {}
    for row in records:
        groups.setdefault(row["gold_code"][:2], []).append(row)
    for chapter in groups:
        groups[chapter].sort(key=lambda row: digest([seed, row["id"]]))
    chapters = sorted(groups, key=lambda chapter: digest([seed, chapter]))
    selected = []
    while len(selected) < n:
        for chapter in chapters:
            if groups[chapter]:
                selected.append(groups[chapter].pop(0))
                if len(selected) == n:
                    return selected
        if not any(groups.values()):
            break
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--name", default="agent-pilot-dev20-v1")
    parser.add_argument("--allow-model-calls", action="store_true")
    args = parser.parse_args()
    if not args.allow_model_calls or not 1 <= args.n <= 20 or not args.name.replace("-", "").isalnum():
        raise SystemExit("Require explicit bounded --allow-model-calls, n1..20 and safe name")
    settings = Settings()
    parent = settings.benchmark_directory
    source = parent / "dev.jsonl"
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    parent_manifest = json.loads((parent / "manifest.json").read_text())
    assert parent_manifest["files"]["dev.jsonl"] == source_hash
    target = settings.evaluation_directory / args.name
    target.mkdir(parents=True, exist_ok=False)
    selected = choose(list(rows(source)), args.n)
    assert len(selected) == args.n
    selected_ids = [row["id"] for row in selected]
    configuration = {
        "suite": "agent",
        "split": "dev",
        "scope": "classification_component",
        "n": args.n,
        "parent_dataset_sha256": source_hash,
        "selected_ids": selected_ids,
        "selection": "fixed hash by chapter then round-robin; no result filtering",
        "label_status": "Historical declared labels, not expert-adjudicated truth",
        "concurrency": args.workers,
        "max_model_calls_per_case": settings.max_model_calls,
        "source_sha256": hashlib.sha256(
            b"".join(path.read_bytes() for path in sorted(Path("src/tariff_agent").glob("*.py")))
        ).hexdigest(),
        "models": gateway_health(settings),
        "features": {
            "context": settings.context_compression_enabled,
            "exploration": settings.agentic_retrieval_enabled,
            "max_tool_rounds": settings.agentic_retrieval_max_rounds,
        },
        "status": "running",
        "formal_business_approval": False,
    }
    (target / "experiment.json").write_text(json.dumps(configuration, ensure_ascii=False, indent=2))
    (target / "selected.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected)
    )
    knowledge = Knowledge(settings.corpus, settings.allow_test_corpus)

    def execute(item):
        execution = settings.model_copy(
            update={
                "database": target / "cases" / f"{item['id']}.db",
                "database_url": SecretStr(""),
                "production": False,
                "strict_readiness": False,
                "otlp_endpoint": "",
            }
        )
        execution.database.parent.mkdir(exist_ok=True)
        store = configured_store(execution)
        case = CaseInput(description=item["description"], target_digits=10, retrieval_profile="hybrid")
        job = store.submit("case", case.model_dump(), "pilot:" + item["id"])
        lease = store.claim(execution.lease_seconds, execution.max_attempts)
        model = Models(execution)
        model.bind(store, job["id"], lease["token"])
        started = time.monotonic()
        result = {}
        error = None
        try:
            with experiment_item(
                digest(configuration)[:32], args.name, source_hash, item["id"], item["gold_code"]
            ):
                result = Workflow(knowledge, model, store, execution).run(
                    lease, scope="classification_component"
                )
                store.finish(job["id"], lease["token"], result["status"], result)
        except Exception as exc:
            error = type(exc).__name__
            if store.owns(job["id"], lease["token"]):
                store.finish(job["id"], lease["token"], "failed", error=error)
        finally:
            model.close()
        events = store.events(job["id"], limit=5000)
        partial = {}
        for event in events:
            if "output" in event["data"]:
                partial.update(event["data"]["output"])
        if not result:
            result = partial | {"case": case.model_dump(), "status": "failed"}
        inspected = inspect_run(result, events, knowledge, "classification_component")
        record = {
            "id": item["id"],
            "gold_code": item["gold_code"],
            "chapter": item["gold_code"][:2],
            "status": result["status"],
            "error_type": error,
            "elapsed_seconds": round(time.monotonic() - started, 4),
            **inspected,
            "attributes_extracted": "attributes" in result,
            "attribute_missing": result.get("attributes", {}).get("missing", []),
            "questions": result.get("report", {}).get("questions", []),
            "stage_seconds": {
                event["stage"]: event["data"]["elapsed_seconds"]
                for event in events
                if "elapsed_seconds" in event["data"]
            },
        }
        for stage in ("retrieved", "shortlist", "ranked", "resolved"):
            if stage in result:
                record[stage] = result[stage]
        (target / "cases" / f"{item['id']}.result.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2)
        )
        return record

    completed = []
    begun = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool, (target / "results.jsonl").open("x") as output:
        futures = [pool.submit(execute, item) for item in selected]
        for future in as_completed(futures):
            record = future.result()
            completed.append(record)
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            output.flush()
            (target / "progress.json").write_text(
                json.dumps(
                    {
                        "completed": len(completed),
                        "n": args.n,
                        "statuses": agent_funnel(completed)["statuses"],
                    }
                )
            )
            print(
                json.dumps(
                    {
                        "completed": len(completed),
                        "n": args.n,
                        "status": record["status"],
                        "model_requests": record["model_requests"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    summary = configuration | {
        "name": args.name,
        "status": "completed",
        "n_completed": len(completed),
        "wall_seconds": round(time.monotonic() - begun, 3),
        "metrics": score(completed),
        "stage_metrics": agent_funnel(completed),
    }
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(
        json.dumps({"status": "completed", "n": len(completed), "summary": str(target / "summary.json")}),
        flush=True,
    )


if __name__ == "__main__":
    main()
