import argparse
import hashlib
import json
from pathlib import Path

from .config import Settings
from .knowledge import Knowledge
from .store import configured_store


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("doctor")
    imp = sub.add_parser("import-corpus")
    imp.add_argument("path", type=Path)
    export = sub.add_parser("export-feedback")
    export.add_argument("output", type=Path)
    sub.add_parser("build-index")
    sub.add_parser("init-config")
    account = sub.add_parser("create-user")
    account.add_argument("username")
    account.add_argument("--role", choices=["admin", "operator", "reviewer"], default="operator")
    history = sub.add_parser("build-history")
    history.add_argument("--source", type=Path, required=True)
    history.add_argument("--exclude", type=Path, action="append", required=True)
    history.add_argument("--limit", type=int, default=0)
    history.add_argument("--dense", action="store_true")
    args = p.parse_args()
    s = Settings()
    if args.command == "init-config":
        import os
        import secrets

        text = Path(".env.example").read_text(encoding="utf-8")
        for name in ("API_KEY", "REVIEWER_KEY"):
            import re

            text = re.sub(
                rf"^TARIFF_{name}=.*$", f"TARIFF_{name}={secrets.token_urlsafe(32)}", text, flags=re.M
            )
        fd = os.open(".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        print("Created private .env; set TARIFF_DEEPSEEK_API_KEY there. Keys are not printed.")
    elif args.command == "init-db":
        configured_store(s)
        print("Database schema ready")
    elif args.command == "create-user":
        import getpass

        from .auth import Identity

        value = getpass.getpass("Password (min 12 characters): ")
        if value != getpass.getpass("Confirm password: "):
            raise SystemExit("Passwords do not match")
        Identity(configured_store(s), s).create_user(args.username, value, args.role)
        print("User created; password hidden")
    elif args.command == "import-corpus":
        if s.corpus.exists():
            raise SystemExit("Target exists; backup and select a new versioned corpus path first")
        print("entries:", Knowledge.import_file(args.path, s.corpus))
    elif args.command == "doctor":
        import httpx

        checks = {
            "database_parent": s.database.parent.exists(),
            "corpus_present": s.corpus.exists(),
            "standard_rules": s.rules.exists(),
            "word2vec": s.word2vec.exists(),
            "deepseek_key_present": bool(s.deepseek_api_key.get_secret_value()),
            "generation_model": s.deepseek_model,
            "embedding_model": s.embedding_model,
        }
        try:
            r = httpx.get(s.embedding_url.rstrip("/") + "/models", timeout=5)
            r.raise_for_status()
            checks["vector_gateway"] = any(x["id"] == s.embedding_model for x in r.json()["data"])
        except Exception:
            checks["vector_gateway"] = False
        print(json.dumps(checks, indent=2))
    elif args.command in ("build-index", "build-history"):
        from .clients import Models

        models = Models(s)
        try:
            k = Knowledge(s.corpus, s.allow_test_corpus)
            if args.command == "build-index":
                print("Indexed headings:", k.build_dense(models.embed, s.index_directory, s.embedding_model))
            else:
                if args.limit < 0:
                    raise SystemExit("limit must be zero (all) or positive")
                from .history import History

                print(
                    json.dumps(
                        History(s).build(
                            args.source,
                            args.exclude,
                            models.embed if args.dense else None,
                            k.formal_codes,
                            args.limit,
                        )
                    )
                )
        finally:
            models.close()
    elif args.command == "export-feedback":
        rows = configured_store(s).approved_samples()
        clean, seen = [], set()
        for row in rows:
            value = row["input"]
            fingerprint = hashlib.sha256(
                json.dumps(
                    {"description": value["description"], "elements": value.get("elements", [])},
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            if fingerprint not in seen:
                seen.add(fingerprint)
                clean.append(row | {"fingerprint": fingerprint})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as out:
            for row in clean:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
        print("expert-approved unique samples:", len(clean))


if __name__ == "__main__":
    main()
