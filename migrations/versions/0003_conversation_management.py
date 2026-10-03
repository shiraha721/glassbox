"""Add conversation organization and copy provenance without rebuilding history tables."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Extend existing SQLite tables in place so cascade deletes cannot discard Runs."""
    op.execute(
        "CREATE TABLE conversation_folders (id TEXT PRIMARY KEY, "
        "name TEXT NOT NULL COLLATE NOCASE UNIQUE, revision INTEGER NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    op.execute(
        "ALTER TABLE sessions ADD COLUMN folder_id TEXT "
        "REFERENCES conversation_folders(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE sessions ADD COLUMN archived BOOLEAN NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE sessions ADD COLUMN title_locked BOOLEAN NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE sessions ADD COLUMN copied_from_session_id TEXT")
    op.execute("ALTER TABLE runs ADD COLUMN source_run_id TEXT")
    op.execute("UPDATE sessions SET title_locked = 1 WHERE title != 'New investigation'")


def downgrade() -> None:
    """Remove metadata only; SQLite 3.35+ supports dropping columns in place."""
    op.execute("ALTER TABLE runs DROP COLUMN source_run_id")
    for column in ("copied_from_session_id", "title_locked", "archived", "folder_id"):
        op.execute(f"ALTER TABLE sessions DROP COLUMN {column}")
    op.drop_table("conversation_folders")
