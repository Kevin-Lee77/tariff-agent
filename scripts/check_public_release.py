"""Check tracked publication files without printing sensitive values; no network or data loading."""

import re
import subprocess
from pathlib import Path


def check(root):
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    ).stdout.decode().split("\0")
    forbidden_parts = {"data", "private", "models", ".runtime", "node_modules", ".venv", "resume"}
    forbidden_suffixes = {".db", ".sqlite", ".jsonl", ".parquet", ".pkl", ".safetensors", ".pem", ".key", ".crt", ".xlsx", ".csv", ".pdf", ".zip"}
    patterns = {
        "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "provider-token": re.compile(r"\b(?:sk-[A-Za-z0-9]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16})\b"),
        "private-ip": re.compile(r"(?<![\d.])(?:10\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\d.])"),
        "personal-path": re.compile(r"[A-Za-z]:[/\\](?:Users|A_Lzw)[/\\]|/(?:home|root)/(?:lzw|project)/", re.I),
    }
    problems = []
    count = 0
    for name in filter(None, tracked):
        path = Path(name)
        count += 1
        if set(path.parts) & forbidden_parts or path.suffix.lower() in forbidden_suffixes or (path.name.startswith(".env") and path.name != ".env.example"):
            problems.append((name, "excluded-material"))
        target = root / path
        if not target.is_file() or path.suffix.lower() in {".png", ".jpg", ".webp"}:
            continue
        text = target.read_text(encoding="utf-8", errors="replace")
        for label, pattern in patterns.items():
            candidate = text
            if label == "private-ip" and path.name.endswith(".lock"):
                candidate = "\n".join(line for line in text.splitlines() if not re.fullmatch(r"[A-Za-z0-9_-]+==[A-Za-z0-9.+_-]+", line))
            if pattern.search(candidate):
                problems.append((name, label))
    if not count:
        raise SystemExit("No tracked files: stage the intended release before scanning")
    if problems:
        for name, label in problems:
            print(f"{name}: {label}")
        raise SystemExit("Publication check failed; values intentionally hidden")
    print(f"Publication checks passed: {count} tracked files; no excluded material or detected credentials")


if __name__ == "__main__":
    check(Path(__file__).resolve().parents[1])
