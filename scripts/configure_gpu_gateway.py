"""Apply non-public deployment settings without printing credentials or changing DeepSeek credentials."""

import argparse
import os
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--history-dense", action="store_true")
    parser.add_argument("--key-file", type=Path, default=Path("data/private/gpu-gateway.key"))
    parser.add_argument("--ca-file", type=Path, default=Path("data/private/gpu-gateway.crt"))
    args = parser.parse_args()
    key = args.key_file.read_text().strip()
    if not args.url.startswith("https://") or len(key) < 24 or not args.ca_file.is_file():
        raise ValueError("Require HTTPS, a private key file and the pinned service certificate")
    path = Path(".env")
    content = path.read_text(encoding="utf-8")
    backup = Path("data/private/env-before-3090")
    if not backup.exists():
        with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as target:
            target.write(content)
    values = {
        "MODEL_GATEWAY_KEY": key,
        "MODEL_GATEWAY_CA": str(args.ca_file.resolve()),
        "EMBEDDING_URL": args.url.rstrip("/") + "/v1",
        "RERANK_URL": args.url.rstrip("/") + "/rerank",
        "SMALL_MODEL_URL": args.url.rstrip("/") + "/v1",
        "SMALL_MODEL_ENABLED": "true",
        "HISTORY_DENSE_ENABLED": str(args.history_dense).lower(),
        "BENCHMARK_DIRECTORY": "data/private/benchmark-compact-v2",
    }
    for name, value in values.items():
        name = "TARIFF_" + name
        line = name + "=" + value
        content = (
            re.sub(r"^" + name + r"=.*$", lambda _: line, content, flags=re.M)
            if re.search(r"^" + name + "=", content, re.M)
            else content.rstrip() + "\n" + line + "\n"
        )
    temporary = path.with_suffix(".next")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as target:
        target.write(content)
    temporary.replace(path)
    print("GPU gateway configured; credentials hidden; previous configuration retained privately.")


if __name__ == "__main__":
    main()
