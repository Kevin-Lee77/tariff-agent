"""Frozen agent run, reviewed memory and local OpenTelemetry schema."""

import sqlalchemy as sa
from alembic import op

revision = "0003_agent_memory"
down_revision = "0002_business_assets"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("job_id", sa.Text, sa.ForeignKey("jobs.id"), nullable=False),
        *(
            sa.Column(name, sa.Text, nullable=False)
            for name in (
                "domain_version",
                "execution_version",
                "input_revision",
                "scope",
                "status",
                "stage",
                "state",
            )
        ),
        sa.Column("trace_id", sa.Text),
        sa.Column("created", sa.Float, nullable=False),
        sa.Column("updated", sa.Float, nullable=False),
    )
    op.create_index("agent_runs_job", "agent_runs", ["job_id", "updated"])
    op.create_table(
        "expert_memories",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("source_job", sa.Text, sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        *(
            sa.Column(name, sa.Text, nullable=False)
            for name in ("domain_version", "namespace", "code", "status", "data", "publisher")
        ),
        sa.Column("created", sa.Float, nullable=False),
        sa.Column("expires", sa.Float, nullable=False),
        sa.Column("revoked", sa.Float),
        sa.Column("revocation_reason", sa.Text),
    )
    op.create_index("expert_memory_scope", "expert_memories", ["namespace", "domain_version", "status"])
    op.create_table(
        "agent_spans",
        sa.Column("span_id", sa.Text, primary_key=True),
        sa.Column("trace_id", sa.Text, nullable=False),
        sa.Column("parent_id", sa.Text),
        sa.Column("job_id", sa.Text, sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("started", sa.Float, nullable=False),
        sa.Column("ended", sa.Float, nullable=False),
        sa.Column("attributes", sa.Text, nullable=False),
    )
    op.create_index("agent_spans_job", "agent_spans", ["job_id", "started"])


def downgrade():
    raise RuntimeError("Agent memory requires a backed-up, reviewed rollback")
