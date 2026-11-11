"""Shared portable schema. Deployment uses PostgreSQL, local tests use SQLite."""

from functools import lru_cache
from pathlib import Path

from sqlalchemy import Column, Float, ForeignKey, Index, Integer, MetaData, Table, Text, create_engine, event

metadata = MetaData()
jobs = Table(
    "jobs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("payload", Text, nullable=False),
    Column("original_payload", Text),
    Column("status", Text, nullable=False),
    Column("result", Text),
    Column("error", Text),
    Column("idem", Text, unique=True),
    Column("attempts", Integer, nullable=False, default=0),
    Column("token", Text),
    Column("lease", Float),
    Column("created", Float, nullable=False),
    Column("updated", Float, nullable=False),
)
Index("jobs_claim", jobs.c.status, jobs.c.created)
events = Table(
    "events",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("job_id", Text, nullable=False),
    Column("stage", Text, nullable=False),
    Column("data", Text, nullable=False),
    Column("created", Float, nullable=False),
)
Index("events_job_seq", events.c.job_id, events.c.seq)
feedback = Table(
    "feedback",
    metadata,
    Column("job_id", Text, primary_key=True),
    Column("data", Text, nullable=False),
    Column("created", Float, nullable=False),
)
terms = Table(
    "terms",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("data", Text, nullable=False),
    Column("updated", Float, nullable=False),
)
users = Table(
    "users",
    metadata,
    Column("username", Text, primary_key=True),
    Column("password_hash", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("active", Integer, nullable=False, default=1),
    Column("created", Float, nullable=False),
)
sessions = Table(
    "sessions",
    metadata,
    Column("token_hash", Text, primary_key=True),
    Column("username", Text, nullable=False),
    Column("csrf_hash", Text, nullable=False),
    Column("expires", Float, nullable=False),
    Column("created", Float, nullable=False),
)
audit = Table(
    "audit",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("actor", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("resource", Text, nullable=False),
    Column("detail", Text, nullable=False),
    Column("created", Float, nullable=False),
)
worker_health = Table(
    "worker_health",
    metadata,
    Column("id", Text, primary_key=True),
    Column("last_seen", Float, nullable=False),
)
login_limits = Table(
    "login_limits",
    metadata,
    Column("key", Text, primary_key=True),
    Column("failures", Integer, nullable=False),
    Column("until", Float, nullable=False),
)

# Business assets are separate from task traces and from each other.
data_imports = Table(
    "data_imports",
    metadata,
    Column("id", Text, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("fingerprint", Text, nullable=False, unique=True),
    Column("synthetic", Integer, nullable=False),
    Column("summary", Text, nullable=False),
    Column("created", Float, nullable=False),
)
bill = Table(
    "bill",
    metadata,
    Column("id", Text, primary_key=True),
    Column("import_id", Text, ForeignKey("data_imports.id"), nullable=False),
    Column("source_key", Text, nullable=False),
    Column("task_id", Text),
    Column("bill_code", Text),
    Column("entry_id", Text),
    Column("org_code", Text),
    Column("code_ts", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("alignment_status", Text, nullable=False),
    Column("raw", Text, nullable=False),
    Column("synthetic", Integer, nullable=False),
)
Index("bill_scope", bill.c.synthetic, bill.c.code_ts)
bill_element = Table(
    "bill_element",
    metadata,
    Column("bill_id", Text, ForeignKey("bill.id"), primary_key=True),
    Column("position", Integer, primary_key=True),
    Column("code", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("value", Text, nullable=False),
    Column("alignment_status", Text, nullable=False),
)
negative_rules = Table(
    "negative",
    metadata,
    Column("id", Text, primary_key=True),
    Column("import_id", Text, ForeignKey("data_imports.id"), nullable=False),
    Column("knowledge_id", Text, nullable=False),
    Column("fit_area", Text, nullable=False),
    Column("element_code", Text, nullable=False),
    Column("vocabulary", Text, nullable=False),
    Column("threshold", Float, nullable=False),
    Column("review_comment", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("raw", Text, nullable=False),
    Column("synthetic", Integer, nullable=False),
)
Index("negative_scope", negative_rules.c.synthetic, negative_rules.c.fit_area, negative_rules.c.element_code)
dictionary_version = Table(
    "dictionary_version",
    metadata,
    Column("id", Text, primary_key=True),
    Column("import_id", Text, ForeignKey("data_imports.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("synthetic", Integer, nullable=False),
    Column("active", Integer, nullable=False),
    Column("parent_id", Text, ForeignKey("dictionary_version.id")),
    Column("config", Text, nullable=False),
    Column("created", Float, nullable=False),
)
dictionary_entry = Table(
    "dictionary_entry",
    metadata,
    Column("id", Text, primary_key=True),
    Column("dictionary_id", Text, ForeignKey("dictionary_version.id"), nullable=False),
    Column("term", Text, nullable=False),
    Column("canonical", Text, nullable=False),
    Column("element_code", Text, nullable=False),
    Column("element_name", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("frequency", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("payload", Text, nullable=False),
)
Index(
    "dictionary_lookup", dictionary_entry.c.dictionary_id, dictionary_entry.c.scope, dictionary_entry.c.term
)
governance_report = Table(
    "governance_report",
    metadata,
    Column("id", Text, primary_key=True),
    Column("bill_id", Text, ForeignKey("bill.id")),
    Column("kind", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("decision", Text, nullable=False),
    Column("synthetic", Integer, nullable=False),
    Column("data", Text, nullable=False),
    Column("created", Float, nullable=False),
)
governance_annotation = Table(
    "governance_annotation",
    metadata,
    Column("id", Text, primary_key=True),
    Column("report_id", Text, ForeignKey("governance_report.id"), nullable=False),
    Column("knowledge_id", Text, nullable=False),
    Column("data", Text, nullable=False),
)


agent_runs = Table(
    "agent_runs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("job_id", Text, ForeignKey("jobs.id"), nullable=False),
    Column("domain_version", Text, nullable=False),
    Column("execution_version", Text, nullable=False),
    Column("input_revision", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("stage", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("trace_id", Text),
    Column("created", Float, nullable=False),
    Column("updated", Float, nullable=False),
)
Index("agent_runs_job", agent_runs.c.job_id, agent_runs.c.updated)

expert_memories = Table(
    "expert_memories",
    metadata,
    Column("id", Text, primary_key=True),
    Column("source_job", Text, ForeignKey("jobs.id"), nullable=False, unique=True),
    Column("domain_version", Text, nullable=False),
    Column("namespace", Text, nullable=False),
    Column("code", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("data", Text, nullable=False),
    Column("publisher", Text, nullable=False),
    Column("created", Float, nullable=False),
    Column("expires", Float, nullable=False),
    Column("revoked", Float),
    Column("revocation_reason", Text),
)
Index(
    "expert_memory_scope",
    expert_memories.c.namespace,
    expert_memories.c.domain_version,
    expert_memories.c.status,
)

agent_spans = Table(
    "agent_spans",
    metadata,
    Column("span_id", Text, primary_key=True),
    Column("trace_id", Text, nullable=False),
    Column("parent_id", Text),
    Column("job_id", Text, ForeignKey("jobs.id"), nullable=False),
    Column("run_id", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("started", Float, nullable=False),
    Column("ended", Float, nullable=False),
    Column("attributes", Text, nullable=False),
)
Index("agent_spans_job", agent_spans.c.job_id, agent_spans.c.started)


@lru_cache(maxsize=16)
def engine_for(target):
    value = str(target)
    if "://" not in value:
        path = Path(value).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        value = "sqlite:///" + path.as_posix()
    if not value.startswith(("sqlite:///", "postgresql+psycopg://")):
        raise ValueError("Unsupported database backend")
    options = (
        {"connect_args": {"check_same_thread": False, "timeout": 30}} if value.startswith("sqlite") else {}
    )
    engine = create_engine(value, pool_pre_ping=True, hide_parameters=True, **options)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def configure(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def database_target(settings):
    return settings.database_url.get_secret_value() or str(settings.database)


@lru_cache(maxsize=32)
def ensure_schema(target):
    metadata.create_all(engine_for(target))
