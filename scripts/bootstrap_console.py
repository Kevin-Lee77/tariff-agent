"""Create initial admin without exposing credentials in argv, stdout or source control."""

import json
import os
import secrets
from pathlib import Path

from tariff_agent.auth import Identity
from tariff_agent.config import Settings
from tariff_agent.store import configured_store


def main():
    target = Path("data/private/bootstrap-admin.json")
    if target.exists():
        print("Bootstrap credential file already exists; no user/password changed")
        return
    s = Settings()
    password = secrets.token_urlsafe(24)
    store = configured_store(s)
    identity = Identity(store, s)
    identity.create_user("admin", password, "admin")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"username": "admin", "password": password}, stream)
    store.audit("bootstrap", "initial_admin_created", "admin")
    print("Initial admin created; private credentials saved to data/private/bootstrap-admin.json")


if __name__ == "__main__":
    main()
