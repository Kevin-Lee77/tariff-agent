"""Extract only domain field names and API routes, without reading connection settings."""

import argparse
import hashlib
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    controllers = []
    models = []
    for file in sorted(args.source.rglob("*.java")):
        relevant = file.stem in {
            "CSynonymController",
            "CNormativeController",
            "CClassificationController",
            "CCommodityElementController",
            "CRuleController",
            "CTrainingController",
        }
        model = "/domain/model/" in file.as_posix() and file.parent.name in (
            "synonym",
            "normative",
            "classification",
        )
        if not relevant and not model:
            continue
        text = file.read_text(encoding="utf-8")
        item = {
            "name": file.stem,
            "source": file.relative_to(args.source).as_posix(),
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
        }
        if relevant:
            item["routes"] = re.findall(r'@Path\("([^"]+)"\)', text)
            controllers.append(item)
        if model:
            item["fields"] = re.findall(
                r"private\s+(?:String|Integer|int|Double|Boolean|Long|List<[^>]+>)\s+(\w+)", text
            )
            models.append(item)
    result = {
        "controllers": controllers,
        "models": models,
        "scope": "Recovered original domain/API sources; no SSO or database configuration inspected",
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        {
            "controllers": len(controllers),
            "routes": sum(len(c["routes"]) for c in controllers),
            "models": len(models),
        }
    )


if __name__ == "__main__":
    main()
