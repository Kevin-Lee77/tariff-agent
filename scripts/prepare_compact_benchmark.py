"""Reduce the execution budget without moving old held-out families back into retrieval."""

import argparse
import hashlib
import json
from pathlib import Path

from tariff_agent.benchmark_data import rows, stratified


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("previous", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--dev", type=int, default=100)
    parser.add_argument("--heldout", type=int, default=200)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    previous = json.loads((args.previous / "manifest.json").read_text(encoding="utf-8"))
    files, groups = {}, {}
    for name, size in [("dev", args.dev), ("heldout", args.heldout)]:
        source = args.previous / (name + ".jsonl")
        if hashlib.sha256(source.read_bytes()).hexdigest() != previous["files"][source.name]:
            raise ValueError("Parent benchmark checksum mismatch")
        chosen = stratified(list(rows(source)), size)
        groups[name] = {r["group_id"] for r in chosen}
        path = args.output / (name + ".jsonl")
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in chosen), encoding="utf-8")
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    overlap = len(groups["dev"] & groups["heldout"])
    if overlap:
        raise ValueError("Development and blind families overlap")
    manifest = {
        "version": "compact-v2",
        "counts": {"dev": args.dev, "heldout": args.heldout},
        "group_overlap": overlap,
        "label_status": previous["label_status"],
        "files": files,
        "parent_manifest_sha256": hashlib.sha256((args.previous / "manifest.json").read_bytes()).hexdigest(),
        "exclusion_pools": [str((args.previous / (s + "_pool.jsonl")).resolve()) for s in ("dev", "heldout")],
        "policy": "Dev for tuning and badcase regression; heldout is final acceptance only; all parent pool families remain excluded.",
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
