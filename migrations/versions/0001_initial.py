"""Local studio records. Keep this schema independent of future application changes."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = [
        "CREATE TABLE settings (id TEXT PRIMARY KEY, data JSON NOT NULL)",
        "CREATE TABLE presets (id TEXT PRIMARY KEY, data JSON NOT NULL)",
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL, revision INTEGER NOT NULL, active_run_id TEXT, messages JSON NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE previews (id TEXT PRIMARY KEY, data JSON NOT NULL)",
        "CREATE TABLE runs (id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, preview_id TEXT NOT NULL UNIQUE, idempotency_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL, snapshot JSON NOT NULL, status TEXT NOT NULL, stop_reason TEXT, messages JSON NOT NULL, partial_output TEXT NOT NULL, latest_event_seq INTEGER NOT NULL, approval JSON, error TEXT, usage JSON NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        "CREATE TABLE events (run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, seq INTEGER NOT NULL, type TEXT NOT NULL, timestamp TEXT NOT NULL, payload JSON NOT NULL, PRIMARY KEY (run_id, seq))",
        "CREATE TABLE calls (id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, kind TEXT NOT NULL, status TEXT NOT NULL, data JSON NOT NULL, result JSON)",
        "CREATE TABLE browser_sessions (id TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires_at TEXT NOT NULL)",
        "CREATE INDEX ix_runs_session ON runs(session_id, created_at)",
        "CREATE INDEX ix_calls_run ON calls(run_id)",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    for name in [
        "browser_sessions",
        "calls",
        "events",
        "runs",
        "previews",
        "sessions",
        "presets",
        "settings",
    ]:
        op.drop_table(name)
