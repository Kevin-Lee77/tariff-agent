"""Archive only the earlier Qdrant token-cache layout, never unrelated database files."""

import json
from pathlib import Path

from tariff_agent.config import Settings


def main():
    private = Path("data/private").resolve()
    old = private / "chroma"
    destination = private / "index-cache-before-layout-migration"
    current = Settings().history_directory.resolve()
    if not old.exists():
        return
    if (
        current == old
        or not old.resolve().is_relative_to(private)
        or not destination.resolve().is_relative_to(private)
    ):
        raise ValueError("Unexpected active/source cache location")
    if (old / "chroma.sqlite3").exists():
        raise ValueError("Unrelated database files require separate migration")
    original = json.loads((old / "active.json").read_text())
    active = json.loads((current / "active.json").read_text())
    if original.get("backend") != "qdrant" or original["generation"] != active["generation"]:
        raise ValueError("The current index does not match the archived cache")
    if destination.exists():
        raise FileExistsError("Archive destination already exists")
    old.rename(destination)
    print("Archived the earlier Qdrant cache layout; active indexes preserved")


if __name__ == "__main__":
    main()
