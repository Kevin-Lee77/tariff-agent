"""Copy completed Qdrant token caches into their dedicated directory, preserving old files."""

import json
import re
import shutil
from pathlib import Path

from tariff_agent.config import Settings


def main():
    settings = Settings()
    source = settings.history_directory.resolve()
    target = Path("data/private/history-index").resolve()
    private = Path("data/private").resolve()
    if not source.is_relative_to(private) or not target.is_relative_to(private):
        raise ValueError("Unexpected cache location")
    manifest = json.loads((source / "active.json").read_text())
    if manifest.get("backend") != "qdrant":
        raise ValueError("Only Qdrant cache migration is supported")
    if source != target:
        if target.exists():
            raise FileExistsError("Target already exists; review its generation before replacing")
        shutil.copytree(source, target)
        for path in target.rglob("active.json"):
            data = json.loads(path.read_text())
            for key in ("token_cache", "vocabulary"):
                data[key] = str(target / Path(data[key]).relative_to(source))
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        env = Path(".env")
        text = re.sub(
            r"^TARIFF_HISTORY_DIRECTORY=.*$",
            "TARIFF_HISTORY_DIRECTORY=./data/private/history-index",
            env.read_text(),
            flags=re.M,
        )
        temporary = Path(".env.next")
        temporary.write_text(text, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(env)
    print({"cache_directory": str(target), "backend": "qdrant"})


if __name__ == "__main__":
    main()
