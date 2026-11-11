import json
from pathlib import Path

import httpx


def main():
    with httpx.Client(base_url="http://127.0.0.1:18000", timeout=15) as client:
        assert client.get("/v1/evaluations/status").status_code == 401
        login = client.post(
            "/auth/login", json=json.loads(Path("data/private/bootstrap-admin.json").read_text())
        )
        login.raise_for_status()
        response = client.get("/v1/evaluations/status")
        response.raise_for_status()
        data = response.json()
        assert data["dataset"]["counts"]["heldout"] == 200 and data["dataset"]["counts"]["dev"] == 100
        assert data["indexes"]["history"]["points"] == 300000 and data["indexes"]["catalog"]["points"] == 1231
        assert data["indexes"]["history"]["connected"] and not data["indexes"]["history"]["dense_ready"]
        assert (
            data["indexes"]["history"]["dense_search_ready"]
            and data["indexes"]["history"]["dense_count"] == 2048
        )
        assert (
            data["indexes"]["catalog"]["dense_ready"]
            and data["warehouse"]["warehouse_counts"]["raw_rows"] == 3775241
        )
        expected = {"compact-v2-dev-baseline", "compact-v2-heldout-baseline"}
        assert expected <= {r["name"] for r in data["experiments"]}
        report = client.get("/v1/evaluations/experiments/compact-v2-dev-baseline/summary")
        assert report.status_code == 200 and "attachment" in report.headers["content-disposition"]
        ready = client.get("/health/ready")
        assert ready.status_code == 200 and ready.json()["checks"]["vector_gateway"]
        bank = client.get("/v1/evaluations/badcases/compact-v2-baseline-dev")
        assert bank.status_code == 200 and bank.json()["n"] == 51
        assert all(not row["eligible_for_training"] for row in bank.json()["cases"])
        result = {
            "status": "passed",
            "history_cases": 300000,
            "catalog_headings": 1231,
            "dev": 100,
            "heldout": 200,
            "required_experiments": sorted(expected),
            "backend": "qdrant",
            "history_dense_count": 2048,
            "history_enabled": data["history_enabled"],
            "catalog_enabled": data["catalog_enabled"],
        }
        Path(".runtime/evaluation-dashboard-verification.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result))


if __name__ == "__main__":
    main()
