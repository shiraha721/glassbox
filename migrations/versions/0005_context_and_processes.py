"""Record explicit context reductions and owned native command sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Extend session metadata in place and preserve existing Runs and checkpoints."""
    op.create_table(
        "compactions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.execute("ALTER TABLE sessions ADD COLUMN compaction_id TEXT")
    op.execute("ALTER TABLE sessions ADD COLUMN compacting_id TEXT")
    op.create_table(
        "processes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "run_id", sa.String(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    """Remove the added metadata without rebuilding or cascading existing history tables."""
    op.drop_table("processes")
    op.execute("ALTER TABLE sessions DROP COLUMN compacting_id")
    op.execute("ALTER TABLE sessions DROP COLUMN compaction_id")
    op.drop_table("compactions")
