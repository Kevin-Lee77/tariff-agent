"""Install a pinned official release binary inside this project only."""

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import httpx


def main():
    root = Path(".runtime/qdrant")
    root.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=180) as client:
        release = client.get("https://api.github.com/repos/qdrant/qdrant/releases/latest")
        release.raise_for_status()
        data = release.json()
        asset = next(a for a in data["assets"] if a["name"] == "qdrant-x86_64-unknown-linux-musl.tar.gz")
        download = client.get(
            asset["url"], headers={"Accept": "application/octet-stream"}, params={"download": "1"}
        )
        download.raise_for_status()
        digest = hashlib.sha256(download.content).hexdigest()
        if asset.get("digest") and asset["digest"] != "sha256:" + digest:
            raise RuntimeError("Release checksum mismatch")
        with tarfile.open(fileobj=io.BytesIO(download.content), mode="r:gz") as archive:
            member = next(m for m in archive.getmembers() if Path(m.name).name == "qdrant" and m.isfile())
            if member.size > 300 * 1024 * 1024:
                raise RuntimeError("Unexpected binary size")
            target = root / "qdrant"
            target.write_bytes(archive.extractfile(member).read())
            target.chmod(0o750)
        manifest = {
            "version": data["tag_name"],
            "asset": asset["name"],
            "url": asset["browser_download_url"],
            "sha256": digest,
            "upstream_digest_verified": bool(asset.get("digest")),
        }
        (root / "release.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    env = Path(".env")
    text = env.read_text(encoding="utf-8")
    import secrets

    updates = {
        "TARIFF_QDRANT_URL": "http://127.0.0.1:18033",
        "TARIFF_QDRANT_API_KEY": secrets.token_urlsafe(40),
        "TARIFF_HISTORY_DIRECTORY": "./data/private/history-index",
        "TARIFF_BENCHMARK_DIRECTORY": "./data/private/benchmark-industry-v1",
    }
    existing = {line.split("=", 1)[0] for line in text.splitlines()}
    fd = os.open(env, os.O_WRONLY | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as out:
        for key, value in updates.items():
            if key not in existing:
                out.write("\n" + key + "=" + value)
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
