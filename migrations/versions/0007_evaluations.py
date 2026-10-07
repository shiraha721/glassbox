"""Store prompt evaluations and their comparison batches beside normal conversations."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add eval definitions, their run batches, and a session kind that hides eval sessions."""
    # Eval items run as ordinary Runs in their own sessions; this flag keeps those out of the
    # conversation list without a separate execution path. Existing sessions default to "chat".
    op.execute("ALTER TABLE sessions ADD COLUMN kind TEXT NOT NULL DEFAULT 'chat'")
    op.create_table(
        "evals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(collation="NOCASE"), nullable=False, unique=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_table(
        "eval_batches",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "eval_id",
            sa.String(),
            sa.ForeignKey("evals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_eval_batches_eval_id", "eval_batches", ["eval_id"])


def downgrade() -> None:
    """Remove evaluations and the session kind, keeping conversations and Run evidence intact."""
    op.drop_index("ix_eval_batches_eval_id", table_name="eval_batches")
    op.drop_table("eval_batches")
    op.drop_table("evals")
    op.execute("ALTER TABLE sessions DROP COLUMN kind")
