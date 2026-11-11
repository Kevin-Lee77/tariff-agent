"""Frozen business asset schema: bill/negative/dictionaries/reports."""

import sqlalchemy as sa
from alembic import op

revision = "0002_business_assets"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def column(name, kind=sa.Text, **kw):
    return sa.Column(name, kind, **kw)


def upgrade():
    op.create_table(
        "data_imports",
        column("id", primary_key=True),
        column("kind", nullable=False),
        column("source", nullable=False),
        column("fingerprint", nullable=False, unique=True),
        column("synthetic", sa.Integer, nullable=False),
        column("summary", nullable=False),
        column("created", sa.Float, nullable=False),
    )
    op.create_table(
        "bill",
        column("id", primary_key=True),
        column("import_id", nullable=False),
        column("source_key", nullable=False),
        column("task_id"),
        column("bill_code"),
        column("entry_id"),
        column("org_code"),
        column("code_ts", nullable=False),
        column("description", nullable=False),
        column("alignment_status", nullable=False),
        column("raw", nullable=False),
        column("synthetic", sa.Integer, nullable=False),
    )
    op.create_index("bill_scope", "bill", ["synthetic", "code_ts"])
    op.create_table(
        "bill_element",
        column("bill_id", primary_key=True),
        column("position", sa.Integer, primary_key=True),
        column("code", nullable=False),
        column("name", nullable=False),
        column("value", nullable=False),
        column("alignment_status", nullable=False),
    )
    op.create_table(
        "negative",
        column("id", primary_key=True),
        column("import_id", nullable=False),
        column("knowledge_id", nullable=False),
        column("fit_area", nullable=False),
        column("element_code", nullable=False),
        column("vocabulary", nullable=False),
        column("threshold", sa.Float, nullable=False),
        column("review_comment", nullable=False),
        column("status", nullable=False),
        column("raw", nullable=False),
        column("synthetic", sa.Integer, nullable=False),
    )
    op.create_index("negative_scope", "negative", ["synthetic", "fit_area", "element_code"])
    op.create_table(
        "dictionary_version",
        column("id", primary_key=True),
        column("import_id", nullable=False),
        column("name", nullable=False),
        column("kind", nullable=False),
        column("status", nullable=False),
        column("synthetic", sa.Integer, nullable=False),
        column("active", sa.Integer, nullable=False),
        column("parent_id"),
        column("config", nullable=False),
        column("created", sa.Float, nullable=False),
    )
    op.create_table(
        "dictionary_entry",
        column("id", primary_key=True),
        column("dictionary_id", nullable=False),
        column("term", nullable=False),
        column("canonical", nullable=False),
        column("element_code", nullable=False),
        column("element_name", nullable=False),
        column("scope", nullable=False),
        column("frequency", sa.Integer, nullable=False),
        column("status", nullable=False),
        column("payload", nullable=False),
    )
    op.create_index("dictionary_lookup", "dictionary_entry", ["dictionary_id", "scope", "term"])
    op.create_table(
        "governance_report",
        column("id", primary_key=True),
        column("bill_id"),
        column("kind", nullable=False),
        column("version", nullable=False),
        column("decision", nullable=False),
        column("synthetic", sa.Integer, nullable=False),
        column("data", nullable=False),
        column("created", sa.Float, nullable=False),
    )
    op.create_table(
        "governance_annotation",
        column("id", primary_key=True),
        column("report_id", nullable=False),
        column("knowledge_id", nullable=False),
        column("data", nullable=False),
    )
    for table, field, target in [
        ("bill", "import_id", "data_imports"),
        ("bill_element", "bill_id", "bill"),
        ("negative", "import_id", "data_imports"),
        ("dictionary_version", "import_id", "data_imports"),
        ("dictionary_version", "parent_id", "dictionary_version"),
        ("dictionary_entry", "dictionary_id", "dictionary_version"),
        ("governance_report", "bill_id", "bill"),
        ("governance_annotation", "report_id", "governance_report"),
    ]:
        with op.batch_alter_table(table) as batch:
            batch.create_foreign_key("fk_" + table + "_" + field, target, [field], ["id"])


def downgrade():
    raise RuntimeError("Business records require backup and a reviewed rollback")
