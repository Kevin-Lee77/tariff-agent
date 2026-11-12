"""Optional real PostgreSQL integration; binaries remain isolated in a temporary directory."""

import os
import shutil
import signal
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tariff_agent.store import Store


@pytest.fixture
def postgres_url():
    pgserver = pytest.importorskip("pgserver")
    pytest.importorskip("psycopg")
    # Do NOT call pgserver.get_server as root: that helper changes ancestor permissions.
    root = Path(tempfile.mkdtemp(prefix="tariff-agent-pg-"))
    runtime = root / "runtime"
    shutil.copytree(Path(pgserver.__file__).parent / "pginstall", runtime)
    shutil.copytree(Path(pgserver.__file__).parent.parent / "pgserver.libs", root / "libs")
    environment = os.environ | {"LD_LIBRARY_PATH": str(root / "libs") + ":" + str(runtime / "lib")}

    def demote():
        if os.geteuid() == 0:
            import pwd

            account = pwd.getpwnam("nobody")
            os.setgroups([])
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)

    if os.geteuid() == 0:
        import pwd

        account = pwd.getpwnam("nobody")
        for path in [root] + list(root.rglob("*")):
            os.chown(path, account.pw_uid, account.pw_gid)
    init = subprocess.run(
        [
            str(runtime / "bin/initdb"),
            "-D",
            str(root / "data"),
            "-U",
            "postgres",
            "-A",
            "trust",
            "--no-locale",
            "-E",
            "UTF8",
        ],
        preexec_fn=demote,
        cwd=root,
        capture_output=True,
        env=environment,
    )
    assert init.returncode == 0, init.stderr.decode(errors="replace")[-2000:]
    server = subprocess.Popen(
        [
            str(runtime / "bin/postgres"),
            "-D",
            str(root / "data"),
            "-k",
            str(root),
            "-p",
            "15432",
            "-c",
            "listen_addresses=",
            "-c",
            "fsync=off",
        ],
        preexec_fn=demote,
        cwd=root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if (root / ".s.PGSQL.15432").exists():
                break
            if server.poll() is not None:
                raise RuntimeError("Isolated PostgreSQL exited before readiness")
            time.sleep(0.1)
        yield f"postgresql+psycopg://postgres@/postgres?host={root}&port=15432"
    finally:
        server.send_signal(signal.SIGINT)
        server.wait(timeout=15)


def test_postgresql_concurrent_claim_and_transactional_review(postgres_url):
    store = Store(postgres_url)
    for i in range(8):
        store.submit("case", {"description": f"测试商品{i}"}, f"postgres-{i}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: Store(postgres_url, auto_create=False).claim(100, 3), range(8)))
    assert len({row["id"] for row in claims if row}) == 8
    first = claims[0]
    store.finish(
        first["id"],
        first["token"],
        "awaiting_review",
        {"votes": {"top_code": "990101"}, "risk_check": {"decision": "clear"}},
    )
    from tariff_agent.domain import Feedback

    store.review(
        first["id"],
        Feedback(approved=True, final_code="990101", reason="数据库集成测试", reviewer="test"),
        {"990101"},
    )
    assert len(store.approved_samples()) == 1
    assert store.summary()["database_backend"] == "postgresql"


def test_alembic_bootstraps_an_empty_postgresql_database(postgres_url, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    monkeypatch.setenv("TARIFF_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    store = Store(postgres_url, auto_create=False)
    assert {
        "jobs",
        "sessions",
        "audit",
        "worker_health",
        "bill",
        "bill_element",
        "negative",
        "dictionary_entry",
        "governance_report",
        "asset_batch_files",
        "asset_batch_rows",
    } <= set(inspect(store.engine).get_table_names())
    task = store.submit("terms", {"pending": []}, "migration-test")
    assert task["status"] == "queued"


def test_postgresql_atomic_file_batch_and_concurrent_dictionary_publication(postgres_url, settings):
    from pydantic import SecretStr

    from tariff_agent.asset_batches import AssetBatches
    from tariff_agent.asset_operations import DictionaryEditor
    from tariff_agent.store import Conflict
    from tariff_agent.worker import process_one

    settings.database_url = SecretStr(postgres_url)
    batches = AssetBatches(postgres_url)
    job = batches.create(
        "term,canonical\n冻制,冷冻\n".encode(),
        "words.csv",
        {
            "operation": "import",
            "asset_kind": "dictionary",
            "dictionary_kind": "synonym",
            "name": "测试并发发布",
            "synthetic": False,
        },
        "test",
        "postgres-file-test",
    )
    process_one(settings)
    result = batches.get(job["id"])
    assert result["result"]["applied"]
    editor = DictionaryEditor(postgres_url)
    base = editor.detail(result["result"]["dictionary_id"])
    action = [{"action": "confirm", "entry_ids": [base["entries"][0]["id"]]}]
    versions = [editor.edit(base["id"], base["revision"], action, "test", "并发审核测试") for _ in range(2)]

    def publish(version):
        try:
            DictionaryEditor(postgres_url).publish(version["id"], "test", "只允许一个分支生效")
            return True
        except Conflict:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        published = list(pool.map(publish, versions))
    assert sorted(published) == [False, True]


def test_postgresql_native_agent_checkpoint_recovery(postgres_url, settings):
    from pydantic import SecretStr
    from test_agent_memory_runtime import FailOnce, submit

    from tariff_agent.agent_memory import AgentMemory
    from tariff_agent.worker import process_one

    settings.database_url = SecretStr(postgres_url)
    store = Store(postgres_url)
    job = submit(store, idem="postgres-native-state")
    model = FailOnce()
    process_one(settings, model)
    assert store.get(job["id"])["status"] == "failed"
    store.retry(job["id"], 3)
    process_one(settings, model)
    assert store.get(job["id"])["status"] == "awaiting_review"
    assert model.calls["extract_attributes"] == 1
    assert len(AgentMemory(store, settings).case_memory(job["id"])["runs"]) == 1


def test_postgresql_native_expert_resume_and_feedback_commit(postgres_url, settings):
    from pydantic import SecretStr
    from test_agent_memory_runtime import submit
    from test_engineering import FakeModels

    from tariff_agent.domain import Feedback
    from tariff_agent.worker import process_one

    settings.database_url = SecretStr(postgres_url)
    store = Store(postgres_url)
    job = submit(store)
    process_one(settings, FakeModels())
    current = store.get(job["id"])
    assert current["result"]["human_review"]["status"] == "waiting_for_expert"
    store.review(
        job["id"],
        Feedback(approved=True, final_code="990101", reason="Postgres专家恢复确认", reviewer="test"),
        {"990101"},
    )
    current = store.get(job["id"])
    assert current["status"] == "approved" and current["result"]["human_review"]["status"] == "completed"
    assert len(store.approved_samples()) == 1
