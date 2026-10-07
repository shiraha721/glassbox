"""Index process and compaction ownership lookups (0001 already indexes runs and calls)."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

INDEXES = {
    "ix_processes_run": ("processes", "run_id"),
    "ix_compactions_session": ("compactions", "session_id"),
}


def upgrade() -> None:
    """Avoid full table scans when a conversation's processes or summaries are read."""
    for name, (table, column) in INDEXES.items():
        op.create_index(name, table, [column])


def downgrade() -> None:
    """Drop the lookup indexes; no data changes."""
    for name, (table, _column) in INDEXES.items():
        op.drop_index(name, table_name=table)
