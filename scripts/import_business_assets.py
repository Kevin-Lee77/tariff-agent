"""Import prepared datasets transactionally; safe to repeat without multiplying rows."""

import argparse
import json
from pathlib import Path

from tariff_agent.assets import Assets
from tariff_agent.config import Settings
from tariff_agent.persistence import database_target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    settings = Settings()
    repository = Assets(database_target(settings), auto_create=not settings.production)
    results = []
    for file in sorted(args.directory.glob("*.json")):
        if file.name in ("manifest.json", "import-report.json"):
            continue
        result = repository.import_bundle(json.loads(file.read_text(encoding="utf-8")))
        results.append({"file": file.name, "result": result})
    summary = repository.stats()
    report = {
        "imports": results,
        "counts": {
            k: summary[k]
            for k in ("bill", "bill_element", "negative", "dictionary_version", "dictionary_entry")
        },
        "database_backend": summary["database_backend"],
    }
    (args.directory / "import-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
