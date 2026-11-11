"""Validate deployed, database-backed tools against synthetic bill records; no model requests."""

import json
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select

from tariff_agent.assets import Assets
from tariff_agent.config import Settings
from tariff_agent.persistence import bill, database_target, governance_report


def main():
    settings = Settings()
    assets = Assets(database_target(settings), auto_create=False)
    with httpx.Client(base_url="http://127.0.0.1:18000", timeout=15) as client:
        credentials = json.loads(Path("data/private/bootstrap-admin.json").read_text())
        response = client.post("/auth/login", json=credentials)
        response.raise_for_status()
        headers = {"X-CSRF-Token": response.json()["csrf"]}

        def execute(key):
            response = client.post(
                "/v1/assets/bill/" + key + "/execute",
                json={"classify": False},
                headers=headers | {"Idempotency-Key": uuid.uuid4().hex},
            )
            response.raise_for_status()
            task = response.json()["id"]
            for _ in range(100):
                row = client.get("/v1/tasks/" + task).json()
                if row["status"] not in ("queued", "running"):
                    return row
                time.sleep(0.25)
            raise RuntimeError("Bill check timeout")

        selected = assets.list_assets("bill", query="【联调风险】", synthetic=True, limit=1)["items"][0]
        blocked = execute(selected["id"])
        assert blocked["result"]["decision"] == "blocked", blocked.get("error")
        with assets.connect() as db:
            stored = db.execute(
                select(governance_report).where(governance_report.c.id == blocked["result"]["report_id"])
            ).one()
            assert stored.bill_id == selected["id"] and stored.decision == "blocked"
            unaligned = db.scalar(
                select(bill.c.id).where(bill.c.alignment_status == "requires_mapping").limit(1)
            )
        waiting = execute(unaligned)
        assert waiting["result"]["decision"] == "information_required"
        summary = client.get("/v1/assets/summary").json()
        assert summary["bill"] == 23089 and summary["negative"] == 5615
        result = {
            "status": "passed",
            "bill_count": summary["bill"],
            "bill_element_count": summary["bill_element"],
            "negative_count": summary["negative"],
            "dictionary_versions": summary["dictionary_version"],
            "dictionary_entries": summary["dictionary_entry"],
            "database_backend": summary["database_backend"],
            "blocked_bill_task": blocked["id"],
            "unaligned_bill_task": waiting["id"],
            "persisted_report": stored.id,
            "paid_model_requests": 0,
        }
        Path(".runtime/business-assets-verification.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result))


if __name__ == "__main__":
    main()
