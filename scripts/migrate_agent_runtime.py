"""Back up current SQLite state and apply the frozen runtime migration without replacing business records."""

import os
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from tariff_agent.config import Settings
from tariff_agent.persistence import database_target, engine_for


def main():
    settings = Settings()
    engine = engine_for(database_target(settings))
    if engine.dialect.name != "sqlite":
        raise RuntimeError("For PostgreSQL use managed backup then alembic upgrade head")
    path = Path(engine.url.database).resolve()
    names = [
        "jobs",
        "users",
        "feedback",
        "bill",
        "bill_element",
        "negative",
        "dictionary_version",
        "dictionary_entry",
    ]

    def counts():
        with engine.connect() as db:
            return {name: db.scalar(text("SELECT COUNT(*) FROM " + name)) for name in names}

    with engine.connect() as db:
        if db.scalar(text("SELECT COUNT(*) FROM jobs WHERE status='running'")):
            raise RuntimeError("Drain active workers before applying schema migration")
    before = counts()
    backup = path.with_name(path.stem + "-before-agent-runtime.db")
    if not backup.exists():
        os.close(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
            source.backup(target)
    if "alembic_version" not in inspect(engine).get_table_names():
        raise RuntimeError("Existing business schema must be verified and stamped before migration")
    command.upgrade(Config("alembic.ini"), "head")
    if counts() != before:
        raise RuntimeError("Business record counts changed during migration")
    print({"migration": "0003_agent_memory", "core_records_preserved": before, "backup": backup.name})


if __name__ == "__main__":
    main()
