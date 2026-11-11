"""Initial PostgreSQL schema. This revision is a frozen schema snapshot."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "jobs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("original_payload", sa.Text),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("result", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("idem", sa.Text, unique=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("token", sa.Text),
        sa.Column("lease", sa.Float),
        sa.Column("created", sa.Float, nullable=False),
        sa.Column("updated", sa.Float, nullable=False),
    )
    op.create_index("jobs_claim", "jobs", ["status", "created"])
    op.create_table(
        "events",
        sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Text, nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("data", sa.Text, nullable=False),
        sa.Column("created", sa.Float, nullable=False),
    )
    op.create_index("events_job_seq", "events", ["job_id", "seq"])
    op.create_table(
        "feedback",
        sa.Column("job_id", sa.Text, primary_key=True),
        sa.Column("data", sa.Text, nullable=False),
        sa.Column("created", sa.Float, nullable=False),
    )
    op.create_table(
        "terms",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("data", sa.Text, nullable=False),
        sa.Column("updated", sa.Float, nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("username", sa.Text, primary_key=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("active", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created", sa.Float, nullable=False),
    )
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.Text, primary_key=True),
        sa.Column("username", sa.Text, nullable=False),
        sa.Column("csrf_hash", sa.Text, nullable=False),
        sa.Column("expires", sa.Float, nullable=False),
        sa.Column("created", sa.Float, nullable=False),
    )
    op.create_table(
        "audit",
        sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("resource", sa.Text, nullable=False),
        sa.Column("detail", sa.Text, nullable=False),
        sa.Column("created", sa.Float, nullable=False),
    )
    op.create_table(
        "worker_health",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("last_seen", sa.Float, nullable=False),
    )
    op.create_table(
        "login_limits",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("failures", sa.Integer, nullable=False),
        sa.Column("until", sa.Float, nullable=False),
    )


def downgrade():
    raise RuntimeError("Destructive rollback requires a separate reviewed migration and database backup")
