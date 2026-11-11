import argparse
import json
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

parser = argparse.ArgumentParser(description="Download a public model into a configurable project root")
parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument("--revision", default=None)
args = parser.parse_args()
root = args.root.resolve()
repo = "Qwen/Qwen3-4B"
revision = args.revision or HfApi().model_info(repo, timeout=30).sha
snapshot_download(
    repo,
    revision=revision,
    local_dir=root / "models/Qwen3-4B",
    max_workers=4,
    ignore_patterns=["*.bin", "*.h5", "*.ot", "*.gguf"],
)
manifest = root / "data/private/small-model-manifest.json"
manifest.parent.mkdir(parents=True, exist_ok=True)
manifest.write_text(
    json.dumps({"repo": repo, "revision": revision, "inference_quantization": "NF4 4bit"}), encoding="utf-8"
)
print(json.dumps({"downloaded": repo, "revision": revision}))
