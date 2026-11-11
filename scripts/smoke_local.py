"""Real local API/worker smoke checks; synthetic inputs, no paid model requests."""

import json
import time
from pathlib import Path

import httpx

from tariff_agent.config import Settings


def main():
    s = Settings()
    headers = {"X-API-Key": s.api_key.get_secret_value()}
    output = {}
    with httpx.Client(base_url="http://127.0.0.1:18000", timeout=30) as client:
        output["live"] = client.get("/health/live").status_code
        ready = client.get("/health/ready", headers=headers)
        output["readiness_status"] = ready.status_code
        output["readiness"] = ready.json()
        requests = {
            "terms": {"synthetic": True, "known": {"鸡翅尖": ["鸡翼尖"]}, "pending": ["鸡翅尖"]},
            "risk": {
                "synthetic": True,
                "code": "0202300000",
                "code_origin": "declared",
                "elements": [{"code": "SYNTHETIC_KIND", "name": "种类", "value": "牛肉"}],
                "rules": [
                    {
                        "knowledge_id": "SYNTHETIC-SMOKE",
                        "fit_area": "02",
                        "element_code": "SYNTHETIC_KIND",
                        "vocabulary": "牛肉",
                        "threshold": 0.85,
                    }
                ],
            },
        }
        for kind, body in requests.items():
            response = client.post(
                f"/v1/governance/{kind}/jobs",
                json=body,
                headers=headers | {"Idempotency-Key": f"synthetic-smoke-v2-{kind}"},
            )
            response.raise_for_status()
            key = response.json()["id"]
            for _ in range(60):
                task = client.get("/v1/tasks/" + key, headers=headers).json()
                if task["status"] not in ("queued", "running"):
                    break
                time.sleep(0.5)
            assert task["status"] == "completed", (kind, task["status"], task.get("error"))
            assert task["result"]["synthetic"] is True
            output[kind] = {
                "task_id": key,
                "status": task["status"],
                "synthetic": task["result"]["synthetic"],
            }
        response = client.post(
            "/v1/governance/standard/check",
            json={"chapter": "02", "elements": [{"name": "保存方法", "value": "冷冻"}]},
            headers=headers,
        )
        response.raise_for_status()
        output["standard_check"] = response.json()["status"]
    Path("smoke-results.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
