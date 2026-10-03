"""Bind conversations to named local workspace folders without altering Run snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add project folders and nullable membership for existing default-workspace sessions."""
    op.create_table(
        "projects",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("workspace_root", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.String(), nullable=False),
    )
    # Rebuilding sessions with foreign_keys enabled can cascade-delete existing Runs.
    op.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT REFERENCES projects(id)")


def downgrade() -> None:
    """Remove project selection while preserving conversations and frozen Run evidence."""
    op.execute("ALTER TABLE sessions DROP COLUMN project_id")
    op.drop_table("projects")
