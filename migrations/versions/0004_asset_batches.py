"""Durable, cancellable business file batches and row diagnostics."""

import sqlalchemy as sa
from alembic import op

revision = "0004_asset_batches"
down_revision = "0003_agent_memory"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "asset_batch_files",
        sa.Column("job_id", sa.Text, sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("output", sa.LargeBinary),
        sa.Column("output_name", sa.Text),
    )
    op.create_table(
        "asset_batch_rows",
        sa.Column("job_id", sa.Text, sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("row_number", sa.Integer, primary_key=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("data", sa.Text, nullable=False),
        sa.Column("error", sa.Text, nullable=False),
    )


def downgrade():
    raise RuntimeError("Business batch history requires a reviewed backup and rollback")
