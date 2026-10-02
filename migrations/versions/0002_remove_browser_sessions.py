"""Remove the unused login session table from existing local workspaces."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop login state without changing conversations or execution records."""
    op.drop_table("browser_sessions")


def downgrade() -> None:
    """Restore the empty table expected by the previous application version."""
    op.execute(
        "CREATE TABLE browser_sessions "
        "(id TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires_at TEXT NOT NULL)"
    )
