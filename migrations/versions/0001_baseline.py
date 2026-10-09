"""Baseline: the schema as it was before migrations existed.

Databases created earlier by Base.metadata.create_all() already match this;
app/migrate.py detects them and stamps them at this revision instead of
trying to create the tables again.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

CLAIM_STATUS = ("active", "released", "expired", "invalidated")
ACTIVITY_TYPES = ("agent_registered", "claim_created", "claim_released", "claim_expired",
                  "claim_invalidated", "claim_rejected", "message_sent")


def upgrade() -> None:
    claimstatus = postgresql.ENUM(*CLAIM_STATUS, name="claimstatus", create_type=False)
    activitytype = postgresql.ENUM(*ACTIVITY_TYPES, name="activitytype", create_type=False)
    claimstatus.create(op.get_bind(), checkfirst=True)
    activitytype.create(op.get_bind(), checkfirst=True)
    now = sa.text("now()")

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("repo_root", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("role", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=now),
    )
    op.create_index("uq_project_agent_name", "agents", ["project_id", "name"], unique=True)
    op.create_table(
        "claims",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("symbol_name", sa.Text()),
        sa.Column("line_start", sa.Integer(), nullable=False),
        sa.Column("line_end", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("status", claimstatus, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), server_default=now),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("release_note", sa.Text()),
    )
    op.create_index("ix_claims_project_file_status", "claims", ["project_id", "file_path", "status"])
    op.create_index("ix_claims_agent", "claims", ["agent_id"])
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="CASCADE")),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now),
    )
    op.create_index("ix_messages_project_created", "messages", ["project_id", "created_at"])
    op.create_index("ix_messages_to_agent", "messages", ["to_agent_id", "created_at"])
    op.create_table(
        "activity_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", activitytype, nullable=False),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now),
    )
    op.create_index("ix_activity_project_created", "activity_events", ["project_id", "created_at"])
    op.create_table(
        "file_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now),
    )
    op.create_index("uq_snapshot_project_file", "file_snapshots", ["project_id", "file_path"], unique=True)


def downgrade() -> None:
    for table in ("file_snapshots", "activity_events", "messages", "claims", "agents", "projects"):
        op.drop_table(table)
    op.execute("DROP TYPE IF EXISTS activitytype")
    op.execute("DROP TYPE IF EXISTS claimstatus")