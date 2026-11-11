"""Activate only complete, split-isolated Qdrant indexes; never print credentials."""

import json
import re
from pathlib import Path

from tariff_agent.config import Settings
from tariff_agent.history import History, catalog_settings


def main():
    settings = Settings()
    manifest = json.loads((settings.benchmark_directory / "manifest.json").read_text())
    history = History(settings)
    catalog = History(catalog_settings(settings), kind="tariff_rule")
    try:
        if history.manifest.get("source_sha256") != manifest["files"]["history.jsonl"]:
            raise ValueError("History index source differs from frozen training partition")
        counts = [history.status(), catalog.status()]
        if not all(r["active"] and r["points"] > 0 for r in counts):
            raise ValueError("Incomplete indexes")
        if history.manifest["indexed"] != manifest["counts"]["history"]:
            raise ValueError("Partial history index")
        text = Path(".env").read_text(encoding="utf-8")
        for key in ("TARIFF_HISTORY_ENABLED", "TARIFF_QDRANT_CATALOG_ENABLED"):
            if re.search(r"^" + key + r"=", text, re.M):
                text = re.sub(r"^" + key + r"=.*$", key + "=true", text, flags=re.M)
            else:
                text += "\n" + key + "=true\n"
        temporary = Path(".env.next")
        temporary.write_text(text, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(".env")
        print(json.dumps({"activated": True, "indexes": counts}))
    finally:
        history.close()
        catalog.close()


if __name__ == "__main__":
    main()
