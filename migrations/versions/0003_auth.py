"""Auth: project join keys and per-agent tokens.

Revision ID: 0003_auth
Revises: 0002_reconciliation
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_auth"
down_revision = "0002_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("join_key_hash", sa.Text(), nullable=True))
    op.create_table(
        "agent_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_agent_tokens_agent", "agent_tokens", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_tokens_agent", "agent_tokens")
    op.drop_table("agent_tokens")
    op.drop_column("projects", "join_key_hash")