"""Activity types for git reconciliation (edit_unclaimed, edit_conflict).

Revision ID: 0002_reconciliation
Revises: 0001_baseline
Create Date: 2026-10-09
"""
from alembic import op

revision = "0002_reconciliation"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE can't be used inside the transaction that adds it,
    # so run it outside one.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE activitytype ADD VALUE IF NOT EXISTS 'edit_unclaimed'")
        op.execute("ALTER TYPE activitytype ADD VALUE IF NOT EXISTS 'edit_conflict'")


def downgrade() -> None:
    # Postgres can't drop enum values; recreate the type without them.
    op.execute("DELETE FROM activity_events WHERE type IN ('edit_unclaimed', 'edit_conflict')")
    op.execute("ALTER TYPE activitytype RENAME TO activitytype_old")
    op.execute("CREATE TYPE activitytype AS ENUM ('agent_registered', 'claim_created', 'claim_released', "
               "'claim_expired', 'claim_invalidated', 'claim_rejected', 'message_sent')")
    op.execute("ALTER TABLE activity_events ALTER COLUMN type TYPE activitytype USING type::text::activitytype")
    op.execute("DROP TYPE activitytype_old")