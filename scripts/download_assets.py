"""Resumable public model snapshots; no CUDA use, no credentials printed."""

import json
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

root = Path(__file__).resolve().parents[1]
models = ["Qwen/Qwen3-Embedding-8B", "Qwen/Qwen3-Reranker-0.6B"]
manifest = []
for repo in models:
    info = HfApi().model_info(repo, timeout=30)
    print(f"Downloading {repo} pinned to {info.sha}", flush=True)
    destination = root / "models" / repo.split("/")[-1]
    snapshot_download(
        repo_id=repo,
        revision=info.sha,
        local_dir=destination,
        max_workers=3,
        ignore_patterns=["*.bin", "*.h5", "*.ot"],
    )
    index = destination / "model.safetensors.index.json"
    shards = (
        set(json.loads(index.read_text())["weight_map"].values()) if index.exists() else {"model.safetensors"}
    )
    missing = [x for x in shards if not (destination / x).exists()]
    if missing:
        raise RuntimeError("Missing model shards")
    manifest.append(
        {
            "repo": repo,
            "revision": info.sha,
            "directory": str(destination),
            "weight_bytes": sum((destination / x).stat().st_size for x in shards),
        }
    )
    (root / "models" / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Completed {repo}", flush=True)
