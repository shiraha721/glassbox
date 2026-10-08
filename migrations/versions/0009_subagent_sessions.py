"""Link the hidden conversations that host sub-agent Runs to the conversation that started them."""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the parent link; existing conversations have none."""
    op.execute("ALTER TABLE sessions ADD COLUMN parent_session_id VARCHAR")
    op.execute("CREATE INDEX ix_sessions_parent ON sessions (parent_session_id)")


def downgrade() -> None:
    """Drop the link; sub-agent conversations stay hidden by their kind."""
    op.execute("DROP INDEX ix_sessions_parent")
    op.execute("ALTER TABLE sessions DROP COLUMN parent_session_id")
