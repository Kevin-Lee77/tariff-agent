"""Idempotently activate local service settings without displaying existing credentials."""

import os
from pathlib import Path


def main():
    env = Path(".env")
    text = env.read_text(encoding="utf-8")
    updates = {
        "TARIFF_BUSINESS_ASSETS_ENABLED": "true",
        "TARIFF_GOVERNANCE_TRANSPORT": "http",
        "TARIFF_TAX_SOURCE_URL": "http://127.0.0.1:18021",
        "TARIFF_TAX_LANGUAGE_URL": "http://127.0.0.1:18022",
        "TARIFF_RISK_RULES": "./data/private/risk_rules-imported.json",
        "TARIFF_MAX_MODEL_CALLS": "40",
    }
    lines = []
    seen = set()
    for line in text.splitlines():
        key = line.split("=", 1)[0]
        if key in updates:
            lines.append(key + "=" + updates[key])
            seen.add(key)
        else:
            lines.append(line)
    lines.extend(key + "=" + value for key, value in updates.items() if key not in seen)
    backup = Path("data/private/env-before-declaration-flow")
    if not backup.exists():
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as out:
            out.write(text)
    temporary = env.with_suffix(".next")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")
    temporary.replace(env)
    print("Configured declaration services; credentials preserved")


if __name__ == "__main__":
    main()
