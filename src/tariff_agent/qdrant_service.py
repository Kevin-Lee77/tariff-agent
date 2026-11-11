"""Native single-node service launcher. Production HA uses the deployment configuration."""

import json
import os
from pathlib import Path

from .config import Settings


def main():
    settings = Settings()
    key = settings.qdrant_api_key.get_secret_value()
    if len(key) < 24:
        raise RuntimeError("Configure Qdrant authentication before startup")
    storage = Path("data/private/qdrant").resolve()
    snapshots = Path("data/private/qdrant-snapshots").resolve()
    storage.mkdir(parents=True, exist_ok=True)
    snapshots.mkdir(parents=True, exist_ok=True)
    config = {
        "log_level": "WARN",
        "telemetry_disabled": True,
        "cluster": {"enabled": False},
        "storage": {"storage_path": str(storage), "snapshots_path": str(snapshots)},
        "service": {"host": "127.0.0.1", "http_port": 18033, "grpc_port": 18034},
    }
    path = Path(".runtime/qdrant/config.yaml").resolve()
    path.write_text(json.dumps(config), encoding="utf-8")
    environment = dict(os.environ)
    environment["QDRANT__SERVICE__API_KEY"] = key
    binary = Path(".runtime/qdrant/qdrant").resolve()
    os.execve(binary, [str(binary), "--config-path", str(path)], environment)


if __name__ == "__main__":
    main()
