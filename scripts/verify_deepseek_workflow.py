"""One real DeepSeek workflow check with explicit BM25-only diagnostic profile.

Separate database. Does not change deployed retrieval configuration or claim RAG accuracy.
"""

import json
from pathlib import Path

from tariff_agent.config import Settings
from tariff_agent.domain import CaseInput
from tariff_agent.store import configured_store
from tariff_agent.worker import process_one


def main():
    s = Settings(
        database=Path("data/private/deepseek-verification.db"),
        database_url="",
        production=False,
        dense_enabled=False,
        rerank_enabled=False,
        history_enabled=False,
    )
    store = configured_store(s)
    body = CaseInput(
        description="冷冻去骨牛肉，供人食用，未经其他加工。",
        elements=[
            {"name": "材质", "value": "牛肉"},
            {"name": "用途", "value": "供人食用"},
            {"name": "保存方法", "value": "冷冻"},
            {"name": "加工方法", "value": "去骨"},
        ],
    )
    job = store.submit("case", body.model_dump(), "deepseek-workflow-diagnostic-v1")
    process_one(s)
    result = store.get(job["id"])
    output = {
        "profile": "diagnostic_bm25_only",
        "synthetic_input": True,
        "status": result["status"],
        "error_type": result.get("error"),
        "stages": [
            e["stage"] for e in store.events(job["id"]) if e["stage"] not in ("model_request", "model_usage")
        ],
        "model_requests": sum(e["stage"] == "model_request" for e in store.events(job["id"])),
        "recommendation": (result.get("result") or {}).get("votes", {}).get("top_code"),
        "scope": "Real generation integration only; not mixed-retrieval or accuracy validation",
    }
    Path("deepseek-verification.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
