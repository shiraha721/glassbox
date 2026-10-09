"""The application schema. Revisions 0001-0010 of the first development versions were merged here.

The statements reproduce the schema those revisions produced, column for column, so a database
they created needs only its version stamp changed (`scripts/upgrade_data.py`) to continue."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = {
    "settings": "id TEXT PRIMARY KEY, data JSON NOT NULL",
    "presets": "id TEXT PRIMARY KEY, data JSON NOT NULL",
    "conversation_folders": (
        "id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, "
        "revision INTEGER NOT NULL, created_at TEXT NOT NULL"
    ),
    "projects": (
        "id VARCHAR NOT NULL, name VARCHAR NOT NULL, workspace_root VARCHAR NOT NULL, "
        "created_at VARCHAR NOT NULL, PRIMARY KEY (id), UNIQUE (workspace_root)"
    ),
    # Conversations. kind is "chat", or "eval"/"subagent" for the hidden conversations that
    # host evaluation and sub-agent Runs.
    "sessions": (
        "id TEXT PRIMARY KEY, title TEXT NOT NULL, revision INTEGER NOT NULL, "
        "active_run_id TEXT, messages JSON NOT NULL, created_at TEXT NOT NULL, "
        "folder_id TEXT REFERENCES conversation_folders(id) ON DELETE SET NULL, "
        "archived BOOLEAN NOT NULL DEFAULT 0, title_locked BOOLEAN NOT NULL DEFAULT 0, "
        "copied_from_session_id TEXT, project_id TEXT REFERENCES projects(id), "
        "compaction_id TEXT, compacting_id TEXT, kind TEXT NOT NULL DEFAULT 'chat', "
        "grants JSON NOT NULL DEFAULT '[]', parent_session_id VARCHAR"
    ),
    "previews": "id TEXT PRIMARY KEY, data JSON NOT NULL",
    "runs": (
        "id TEXT PRIMARY KEY, "
        "session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, "
        "preview_id TEXT NOT NULL UNIQUE, idempotency_key TEXT NOT NULL UNIQUE, "
        "request_hash TEXT NOT NULL, snapshot JSON NOT NULL, status TEXT NOT NULL, "
        "stop_reason TEXT, messages JSON NOT NULL, partial_output TEXT NOT NULL, "
        "latest_event_seq INTEGER NOT NULL, approval JSON, error TEXT, usage JSON NOT NULL, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, source_run_id TEXT"
    ),
    "events": (
        "run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, seq INTEGER NOT NULL, "
        "type TEXT NOT NULL, timestamp TEXT NOT NULL, payload JSON NOT NULL, "
        "PRIMARY KEY (run_id, seq)"
    ),
    "calls": (
        "id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, "
        "kind TEXT NOT NULL, status TEXT NOT NULL, data JSON NOT NULL, result JSON"
    ),
    "compactions": (
        "id VARCHAR NOT NULL, session_id VARCHAR NOT NULL, status VARCHAR NOT NULL, "
        "created_at VARCHAR NOT NULL, data JSON NOT NULL, PRIMARY KEY (id), "
        "FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE"
    ),
    "processes": (
        "id VARCHAR NOT NULL, run_id VARCHAR NOT NULL, status VARCHAR NOT NULL, "
        "created_at VARCHAR NOT NULL, data JSON NOT NULL, PRIMARY KEY (id), "
        "FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE"
    ),
    "evals": (
        'id VARCHAR NOT NULL, name VARCHAR COLLATE "NOCASE" NOT NULL, revision INTEGER NOT NULL, '
        "created_at VARCHAR NOT NULL, data JSON NOT NULL, PRIMARY KEY (id), UNIQUE (name)"
    ),
    "eval_batches": (
        "id VARCHAR NOT NULL, eval_id VARCHAR NOT NULL, status VARCHAR NOT NULL, "
        "created_at VARCHAR NOT NULL, data JSON NOT NULL, PRIMARY KEY (id), "
        "FOREIGN KEY(eval_id) REFERENCES evals (id) ON DELETE CASCADE"
    ),
}

INDEXES = {
    "ix_sessions_parent": "sessions (parent_session_id)",
    "ix_runs_session": "runs (session_id, created_at)",
    "ix_calls_run": "calls (run_id)",
    "ix_compactions_session": "compactions (session_id)",
    "ix_processes_run": "processes (run_id)",
    "ix_eval_batches_eval_id": "eval_batches (eval_id)",
}


def upgrade() -> None:
    """Create every table and lookup index."""
    for name, columns in TABLES.items():
        op.execute(f"CREATE TABLE {name} ({columns})")
    for name, target in INDEXES.items():
        op.execute(f"CREATE INDEX {name} ON {target}")


def downgrade() -> None:
    """Drop every table, dependents first; their indexes go with them."""
    for name in reversed(TABLES):
        op.drop_table(name)
