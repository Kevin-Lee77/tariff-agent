"""Official LangGraph savers, guarded by the same task ownership fence as business state."""

import os
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver


class GuardedSaver(BaseCheckpointSaver):
    def __init__(self, saver, store, job, fence=None):
        super().__init__(serde=saver.serde)
        self.saver, self.store, self.job = saver, store, job
        self.fence = fence or (lambda: self.store.lease_guard(self.job["id"], self.job["token"]))

    def get_tuple(self, config):
        return self.saver.get_tuple(config)

    def list(self, config, *, filter=None, before=None, limit=None):
        yield from self.saver.list(config, filter=filter, before=before, limit=limit)

    def put(self, config, checkpoint, metadata, new_versions):
        with self.fence():
            return self.saver.put(config, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        with self.fence():
            return self.saver.put_writes(config, writes, task_id, task_path)

    def get_next_version(self, current, channel):
        return self.saver.get_next_version(current, channel)


@contextmanager
def checkpoint_store(store, settings, job, fence=None):
    if not settings.durable_execution_enabled:
        yield None
        return
    if store.engine.dialect.name == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver

        target = Path(store.engine.url.database)
        path = target.with_name(target.stem + ".checkpoints.db")
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            pass
        path.chmod(0o600)
        with SqliteSaver.from_conn_string(str(path)) as saver:
            saver.setup()
            yield GuardedSaver(saver, store, job, fence)
    else:
        from langgraph.checkpoint.postgres import PostgresSaver

        connection = store.engine.url.render_as_string(hide_password=False).replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
        with PostgresSaver.from_conn_string(connection) as saver:
            saver.setup()
            yield GuardedSaver(saver, store, job, fence)
