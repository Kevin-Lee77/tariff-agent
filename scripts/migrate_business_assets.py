"""Back up the current local database, preserve core records, then apply Alembic."""

import os
import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from tariff_agent.config import Settings
from tariff_agent.persistence import database_target, engine_for


def main():
    settings = Settings()
    engine = engine_for(database_target(settings))
    tables = set(inspect(engine).get_table_names())
    if engine.dialect.name != "sqlite":
        raise RuntimeError(
            "This helper is for the current SQLite deployment; PostgreSQL uses managed backup + Alembic"
        )
    path = settings.database.resolve()
    backup = path.with_name("app-before-business-assets.db")
    if not backup.exists():
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with sqlite3.connect(path) as source, sqlite3.connect(backup) as destination:
            source.backup(destination)

    def counts():
        with engine.connect() as db:
            return {
                name: db.scalar(text("SELECT COUNT(*) FROM " + name))
                for name in ("jobs", "users", "feedback")
            }

    before = counts()
    with engine.connect() as db:
        if db.scalar(text("SELECT COUNT(*) FROM jobs WHERE status='running'")):
            raise RuntimeError("Finish active work before migration")
    config = Config("alembic.ini")
    if "alembic_version" not in tables:
        if (
            not {
                "jobs",
                "events",
                "feedback",
                "terms",
                "users",
                "sessions",
                "audit",
                "worker_health",
                "login_limits",
            }
            <= tables
        ):
            raise RuntimeError("Unexpected legacy schema; refusing to stamp")
        command.stamp(config, "0001_initial")
    command.upgrade(config, "head")
    if counts() != before:
        raise RuntimeError("Core record count changed during migration")
    print({"migration": "0002_business_assets", "core_records_preserved": before, "backup": backup.name})


if __name__ == "__main__":
    main()
