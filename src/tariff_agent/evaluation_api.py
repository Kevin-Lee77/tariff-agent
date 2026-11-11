import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse


def active_experiment(experiment, manifest):
    if manifest is None:
        return True
    if experiment.get("dataset_sha256") in set(manifest["files"].values()):
        return True
    selected = experiment.get("selected_ids", [])
    n = experiment.get("n_completed", 0)
    return (
        experiment.get("pilot") is True
        and experiment.get("split") == "dev"
        and experiment.get("parent_dataset_sha256") == manifest["files"].get("dev.jsonl")
        and isinstance(n, int)
        and 0 < n <= manifest.get("counts", {}).get("dev", 0)
        and len(selected) == n
        and len(set(selected)) == n
    )


def router(settings, user):
    routes = APIRouter(prefix="/v1/evaluations", dependencies=[Depends(user)])

    @routes.get("/status")
    def status():
        dataset = settings.benchmark_directory / "manifest.json"
        manifest = json.loads(dataset.read_text(encoding="utf-8")) if dataset.exists() else None
        indexes = {}
        from qdrant_client import QdrantClient

        from .history import History, catalog_settings

        for name, config, kind in [
            ("history", settings, "historical_case"),
            ("catalog", catalog_settings(settings), "tariff_rule"),
        ]:
            client = QdrantClient(
                url=settings.qdrant_url, api_key=settings.qdrant_api_key.get_secret_value() or None, timeout=3
            )
            try:
                indexes[name] = History(config, client, kind).status() | {"connected": True}
            except Exception as exc:
                indexes[name] = {"backend": "qdrant", "connected": False, "error_type": type(exc).__name__}
            finally:
                client.close()
        experiments = []
        for path in sorted(
            settings.evaluation_directory.glob("*/summary.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:20]:
            try:
                experiment = json.loads(path.read_text(encoding="utf-8"))
                if active_experiment(experiment, manifest):
                    experiments.append(experiment)
            except (ValueError, OSError):
                continue
        banks = []
        for path in sorted((settings.evaluation_directory / "badcases").glob("*.json")):
            try:
                bank = json.loads(path.read_text(encoding="utf-8"))
                if manifest:
                    import re

                    seed = bank["seed_experiment"]
                    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", seed):
                        continue
                    metadata = json.loads(
                        (settings.evaluation_directory / seed / "experiment.json").read_text()
                    )
                    if metadata.get("dataset_sha256") != manifest["files"]["dev.jsonl"]:
                        continue
                banks.append({k: v for k, v in bank.items() if k != "cases"} | {"name": path.stem})
            except (ValueError, OSError, KeyError):
                continue
        return {
            "dataset": manifest,
            "indexes": indexes,
            "experiments": experiments,
            "component_evaluations": [
                json.loads(path.read_text())
                for path in [
                    settings.evaluation_directory.parent / name / "summary.json"
                    for name in ("governance-component-metrics-v1", "governance-component-metrics-v2")
                ]
                if path.is_file()
            ],
            "history_enabled": settings.history_enabled,
            "catalog_enabled": settings.qdrant_catalog_enabled,
            "history_dense_enabled": settings.history_dense_enabled,
            "warehouse": json.loads(
                (settings.history_directory.parent / "history-large-v2" / "manifest.json").read_text()
            )
            if (settings.history_directory.parent / "history-large-v2" / "manifest.json").exists()
            else None,
            "badcase_banks": banks,
            "badcase_regressions": [
                json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((settings.evaluation_directory / "regressions").glob("*.json"))
            ][-20:],
        }

    @routes.get("/badcases/{name}")
    def badcases(name: str, limit: int = 20):
        import re

        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", name) or not 1 <= limit <= 100:
            raise HTTPException(400, "Invalid bank name or limit")
        path = settings.evaluation_directory / "badcases" / (name + ".json")
        if not path.is_file():
            raise HTTPException(404, "Badcase bank unavailable")
        report = json.loads(path.read_text(encoding="utf-8"))
        report["cases"] = report["cases"][:limit]
        return report

    @routes.get("/experiments/{name}/summary")
    def report(name: str):
        import re

        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", name):
            raise HTTPException(400, "Invalid experiment name")
        path = settings.evaluation_directory / name / "summary.json"
        if not path.is_file():
            raise HTTPException(404, "Experiment report unavailable")
        return FileResponse(path, media_type="application/json", filename=name + ".json")

    return routes
