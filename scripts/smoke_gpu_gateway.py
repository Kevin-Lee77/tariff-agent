import json
from pathlib import Path

import numpy as np

from tariff_agent.clients import Models
from tariff_agent.config import Settings
from tariff_agent.domain import Attributes
from tariff_agent.model_transport import gateway_health


def main():
    settings = Settings().model_copy(update={"small_model_fallback_enabled": False})
    client = Models(settings)
    try:
        ready = gateway_health(settings)
        vectors = client.embed(["冷冻去骨牛肉，未调味，供食用", "聚乙烯塑料包装袋"])
        query = client.embed_query(["冻牛肉"])
        ranked = client.rerank("冷冻去骨牛肉", ["塑料包装袋", "冷冻去骨牛肉，供食用"])
        attributes = client.structured(
            "extract_attributes", {"description": "冷冻去骨牛肉，未调味、未经烹煮，供人食用。"}, Attributes
        )
        report = {
            "health": ready,
            "dimensions": list(vectors.shape),
            "norms": np.linalg.norm(vectors, axis=1).tolist(),
            "query_document_scores": (query @ vectors.T).tolist(),
            "rerank_order": ranked,
            "local_4b_schema_valid": True,
            "local_4b_attributes": attributes.model_dump(),
            "deepseek_fallback_allowed": False,
        }
        Path(".runtime/gpu-gateway-smoke.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))
    finally:
        client.close()


if __name__ == "__main__":
    main()
