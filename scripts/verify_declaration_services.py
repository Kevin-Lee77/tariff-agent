"""Smoke test deployed HTTP services with explicit sandbox data; never calls an LLM."""

import json
import time
import uuid
from pathlib import Path

import httpx


def main():
    credentials = json.loads(Path("data/private/bootstrap-admin.json").read_text())
    with httpx.Client(base_url="http://127.0.0.1:18000", timeout=10) as client:
        login = client.post("/auth/login", json=credentials)
        login.raise_for_status()
        headers = {"X-CSRF-Token": login.json()["csrf"]}

        def submit(path, payload):
            response = client.post(
                path, json=payload, headers=headers | {"Idempotency-Key": uuid.uuid4().hex}
            )
            response.raise_for_status()
            identifier = response.json()["id"]
            for _ in range(80):
                row = client.get("/v1/tasks/" + identifier).json()
                if row["status"] not in ("queued", "running"):
                    return row
                time.sleep(0.25)
            raise RuntimeError("Smoke task did not settle")

        common = {"declared_code": "0202300090", "governance_profile": "sandbox"}
        blocked = submit("/v1/cases", common | {"description": "联调：变质牛肉"})
        assert blocked["status"] == "risk_blocked"
        trace = client.get("/v1/tasks/" + blocked["id"] + "/events").json()
        assert [e["stage"] for e in trace] == ["risk_precheck", "finished"]
        feedback = client.get("/v1/cases/" + blocked["id"] + "/enterprise-feedback")
        assert (
            feedback.status_code == 200 and feedback.json()["risk_report"]["annotations"][0]["text"] == "变质"
        )
        language = submit(
            "/v1/governance/declarations/normalize", common | {"description": "冻制剔骨牛肉，不含添加剂"}
        )
        assert language["result"]["normalized_text"] == "冷冻去骨牛肉，不含添加剂"
        clear = submit("/v1/governance/declarations/check", common | {"description": "冷冻牛肉"})
        assert clear["result"]["decision"] == "clear"
        result = {
            "risk_blocked_task": blocked["id"],
            "language_task": language["id"],
            "clear_risk_task": clear["id"],
            "model_requests": 0,
            "flask_transport": "http",
            "sandbox": True,
            "checks": "passed",
        }
        Path(".runtime/declaration-smoke.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))


if __name__ == "__main__":
    main()
