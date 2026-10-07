"""Store standing tool approvals ("approve for this conversation") on each conversation."""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add an empty grant list to every conversation."""
    op.execute("ALTER TABLE sessions ADD COLUMN grants JSON NOT NULL DEFAULT '[]'")


def downgrade() -> None:
    """Drop standing approvals; later calls ask again, nothing else changes."""
    op.execute("ALTER TABLE sessions DROP COLUMN grants")
