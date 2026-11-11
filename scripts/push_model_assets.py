"""Transfer verified model snapshots over authenticated TLS to an explicitly configured receiver."""

import hashlib
import json
import os
import ssl
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = os.environ.get("TARIFF_ASSET_UPLOAD_URL", "").rstrip("/")
if not ENDPOINT.startswith("https://"):
    raise SystemExit("Set TARIFF_ASSET_UPLOAD_URL to the HTTPS receiver; no default remote endpoint")
key = (ROOT / "data/private/gpu-gateway.key").read_text().strip()
context = ssl.create_default_context(cafile=str(ROOT / "data/private/gpu-gateway.crt"))
items = []
for model in ("Qwen3-Embedding-8B", "Qwen3-Reranker-0.6B"):
    folder = ROOT / "models" / model
    for path in folder.rglob("*"):
        if (
            path.is_file()
            and ".cache" not in path.parts
            and path.suffix in (".json", ".safetensors", ".txt", ".jinja", ".md")
        ):
            items.append((path, model + "/" + path.relative_to(folder).as_posix()))


def upload(item):
    source, relative = item
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)

    def chunks():
        with source.open("rb") as stream:
            yield from iter(lambda: stream.read(1024 * 1024), b"")

    with httpx.Client(verify=context, timeout=httpx.Timeout(600, connect=10)) as client:
        response = client.put(
            ENDPOINT + "/" + relative,
            content=chunks(),
            headers={
                "Authorization": "Bearer " + key,
                "Content-Length": str(source.stat().st_size),
                "X-Content-SHA256": digest.hexdigest(),
            },
        )
        response.raise_for_status()
    print(
        json.dumps({"file": relative, "bytes": source.stat().st_size, "sha256": digest.hexdigest()}),
        flush=True,
    )


with ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(upload, items))
