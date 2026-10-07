"""SQLite persistence with short transactions and one local writer."""

import asyncio
import json
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    delete,
    event,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from rtagent.domain import (
    SIMULATED,
    AppError,
    Preset,
    RunEvent,
    SessionCopy,
    SessionUpdate,
    StudioConfig,
    digest,
    net_changes,
    now,
    uid,
)
from rtagent.domain.hooks import EventHook
from rtagent.domain.lifecycle import ACTIVE_PROCESSES, TERMINAL
from rtagent.domain.prompts import builtin_presets, is_previous_builtin
from rtagent.infrastructure.tracing import redact

metadata = MetaData()
settings_table = Table(
    "settings",
    metadata,
    Column("id", String, primary_key=True),
    Column("data", JSON, nullable=False),
)
presets = Table(
    "presets",
    metadata,
    Column("id", String, primary_key=True),
    Column("data", JSON, nullable=False),
)
folders = Table(
    "conversation_folders",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String(collation="NOCASE"), nullable=False, unique=True),
    Column("revision", Integer, nullable=False),
    Column("created_at", String, nullable=False),
)
projects = Table(
    "projects",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("workspace_root", String, nullable=False, unique=True),
    Column("created_at", String, nullable=False),
)
sessions = Table(
    "sessions",
    metadata,
    Column("id", String, primary_key=True),
    Column("title", String, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("active_run_id", String),
    Column("compaction_id", String),
    Column("compacting_id", String),
    Column("messages", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    Column("folder_id", String, ForeignKey("conversation_folders.id", ondelete="SET NULL")),
    Column("project_id", String, ForeignKey("projects.id")),
    Column("archived", Boolean, nullable=False, default=False),
    Column("title_locked", Boolean, nullable=False, default=False),
    Column("copied_from_session_id", String),
    # "chat" conversations are listed in the sidebar; "eval" sessions host eval Runs and are
    # hidden from the conversation list but keep full Run evidence for inspection.
    Column("kind", String, nullable=False, server_default="chat"),
    # Standing approvals the user granted in this conversation (Grant contracts).
    Column("grants", JSON, nullable=False, server_default="[]"),
)
evals = Table(
    "evals",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String(collation="NOCASE"), nullable=False, unique=True),
    Column("revision", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("data", JSON, nullable=False),
)
eval_batches = Table(
    "eval_batches",
    metadata,
    Column("id", String, primary_key=True),
    Column("eval_id", String, ForeignKey("evals.id", ondelete="CASCADE"), nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("data", JSON, nullable=False),
)
previews = Table(
    "previews",
    metadata,
    Column("id", String, primary_key=True),
    Column("data", JSON, nullable=False),
)
runs = Table(
    "runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("session_id", String, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("preview_id", String, unique=True, nullable=False),
    Column("idempotency_key", String, unique=True, nullable=False),
    Column("request_hash", String, nullable=False),
    Column("snapshot", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("stop_reason", String),
    Column("messages", JSON, nullable=False),
    Column("partial_output", String, nullable=False),
    Column("latest_event_seq", Integer, nullable=False),
    Column("approval", JSON),
    Column("error", String),
    Column("usage", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("source_run_id", String),
)
events = Table(
    "events",
    metadata,
    Column("run_id", String, ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("type", String, nullable=False),
    Column("timestamp", String, nullable=False),
    Column("payload", JSON, nullable=False),
)
calls = Table(
    "calls",
    metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("kind", String, nullable=False),
    Column("status", String, nullable=False),
    Column("data", JSON, nullable=False),
    Column("result", JSON),
)

compactions = Table(
    "compactions",
    metadata,
    Column("id", String, primary_key=True),
    Column("session_id", String, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("data", JSON, nullable=False),
)
processes = Table(
    "processes",
    metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("data", JSON, nullable=False),
)


# Run columns the browser projection needs; the frozen snapshot is deliberately excluded.
RUN_PROJECTION = {
    "id",
    "session_id",
    "status",
    "stop_reason",
    "messages",
    "partial_output",
    "latest_event_seq",
    "approval",
    "error",
    "created_at",
    "updated_at",
    "usage",
    "source_run_id",
}


def with_provider_profiles(stored: dict[str, Any]) -> dict[str, Any]:
    """Upgrade settings saved before provider profiles: the one provider becomes a profile."""
    if "providers" in stored:
        return stored
    old = stored.get("provider", {})
    adapter = "anthropic" if old.get("adapter") == "anthropic" else "openai"
    profile = {
        "id": adapter,
        "name": "Anthropic" if adapter == "anthropic" else "OpenAI-compatible",
        "adapter": adapter,
        "base_url": old.get("base_url") or f"https://api.{adapter}.com/v1",
        "credential_ref": old.get("credential_ref", ""),
        "models": [old["model"]] if old.get("model") else [],
        "temperature": old.get("temperature"),
        "max_output_tokens": old.get("max_output_tokens", 8192),
        "context_window": old.get("context_window"),
    }
    simulated = old.get("adapter") == "fake"
    provider = {**old, "profile_id": SIMULATED if simulated else adapter}
    return {**stored, "providers": [profile], "provider": provider}


def call_order(identifier: str) -> tuple[int, int, int]:
    """Order receipt IDs as executed: model step N precedes the tool calls it requested.

    IDs are ``<run>:model:<step>`` and ``<run>:tool:<step>:<index>``; plain text order would
    list every model request before every tool call."""
    _, kind, step, *index = identifier.split(":")
    return int(step), int(kind == "tool"), int(index[0]) if index else 0


def _session_view(row: Any) -> dict[str, Any]:
    """Exclude internal flags (auto-title, eval kind) from the public conversation contract."""
    value = {key: item for key, item in dict(row).items() if key not in {"title_locked", "kind"}}
    return {**value, "message_count": len(value["messages"])}


class Repository:
    """Persist business data with short SQLite transactions and serialized writers."""

    def __init__(self, database_url: str) -> None:
        """Configure the async engine and SQLite integrity settings."""
        self.url = database_url
        self.on_event: EventHook | None = None
        self.engine = create_async_engine(database_url)
        self.lock = asyncio.Lock()

        @event.listens_for(self.engine.sync_engine, "connect")
        def configure_sqlite(connection: Any, _record: Any) -> None:
            """Apply SQLite connection-level integrity and local concurrency settings."""
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    async def initialize(self) -> None:
        """Apply Alembic migrations and seed the initial preset and settings once."""

        def migrate() -> None:
            root = Path(__file__).resolve().parents[3]
            config = Config(str(root / "alembic.ini"))
            config.set_main_option("sqlalchemy.url", self.url.replace("+aiosqlite", ""))
            command.upgrade(config, "head")

        await asyncio.to_thread(migrate)
        async with self.lock, self.engine.begin() as connection:
            await connection.execute(
                delete(previews).where(func.json_extract(previews.c.data, "$.expires_at") <= now())
            )
            if not (await connection.execute(select(settings_table))).first():
                await connection.execute(
                    insert(settings_table).values(
                        id="default", data=StudioConfig(revision=1).model_dump()
                    )
                )
            else:
                stored = (await connection.execute(select(settings_table.c.data))).scalar_one()
                config = StudioConfig.model_validate(with_provider_profiles(stored))
                permissions = dict(config.tools)
                additions = {
                    "search_files": permissions.get("list_files", "deny"),
                    "search_text": permissions.get("read_text", "deny"),
                    "read_instructions": permissions.get("read_text", "deny"),
                    "update_plan": "allow",
                    # Patches add deletion, so existing profiles ask before this new capability.
                    "apply_patch": "ask",
                    "start_process": "ask",
                    "poll_process": "allow",
                    "send_process_input": "ask",
                    "stop_process": "allow",
                    "fetch_url": "ask",
                }
                for name, permission in additions.items():
                    permissions.setdefault(name, permission)
                if permissions != config.tools or "providers" not in stored:
                    saved = config.model_copy(
                        update={"tools": permissions, "revision": config.revision + 1}
                    )
                    await connection.execute(update(settings_table).values(data=saved.model_dump()))
            existing = (await connection.execute(select(presets.c.data))).scalars().all()
            by_id = {value["id"]: Preset.model_validate(value) for value in existing}
            for preset in builtin_presets():
                current = by_id.get(preset.id)
                if current is None:
                    await connection.execute(
                        insert(presets).values(id=preset.id, data=preset.model_dump())
                    )
                elif is_previous_builtin(current):
                    # Only migrate recognized starter content, never user edits or Run snapshots.
                    await connection.execute(
                        update(presets)
                        .where(presets.c.id == current.id)
                        .values(
                            data=preset.model_copy(
                                update={"revision": current.revision + 1}
                            ).model_dump()
                        )
                    )

    async def close(self) -> None:
        """Release the database connection pool during application shutdown."""
        await self.engine.dispose()

    async def _one(self, table: Table, identifier: str) -> dict[str, Any]:
        """Fetch one row or raise a stable application-level not-found error."""
        async with self.engine.connect() as connection:
            row = (
                (await connection.execute(select(table).where(table.c.id == identifier)))
                .mappings()
                .first()
            )
        if row is None:
            raise AppError("not_found", "The requested record does not exist.", 404)
        return dict(row)

    async def config(self) -> StudioConfig:
        """Read the current versioned settings aggregate."""
        return StudioConfig.model_validate((await self._one(settings_table, "default"))["data"])

    async def save_config(self, value: StudioConfig) -> StudioConfig:
        """Replace settings only when the submitted revision is current."""
        async with self.lock, self.engine.begin() as connection:
            current = (await connection.execute(select(settings_table.c.data))).scalar_one()
            if current["revision"] != value.revision:
                raise AppError(
                    "revision_conflict", "Configuration changed. Reload before saving.", 409
                )
            saved = value.model_copy(update={"revision": value.revision + 1})
            await connection.execute(update(settings_table).values(data=saved.model_dump()))
        return saved

    async def list_presets(self) -> list[Preset]:
        """Load editable preset aggregates from the local database."""
        async with self.engine.connect() as connection:
            values = (await connection.execute(select(presets.c.data))).scalars().all()
        return [Preset.model_validate(value) for value in values]

    async def preset(self, identifier: str) -> Preset:
        """Read one preset including its embedded blocks and revision."""
        return Preset.model_validate((await self._one(presets, identifier))["data"])

    async def save_preset(self, value: Preset) -> Preset:
        """Create or update a preset with optimistic concurrency protection."""
        async with self.lock, self.engine.begin() as connection:
            current = (
                await connection.execute(select(presets.c.data).where(presets.c.id == value.id))
            ).scalar_one_or_none()
            if (current and current["revision"] != value.revision) or (
                not current and value.revision != 0
            ):
                raise AppError("revision_conflict", "Preset changed. Reload before saving.", 409)
            saved = value.model_copy(update={"revision": value.revision + 1})
            if current:
                await connection.execute(
                    update(presets).where(presets.c.id == value.id).values(data=saved.model_dump())
                )
            else:
                await connection.execute(
                    insert(presets).values(id=value.id, data=saved.model_dump())
                )
        return saved

    async def list_projects(self) -> list[dict[str, Any]]:
        """List saved workspace folders for project selection."""
        async with self.engine.connect() as connection:
            return [
                dict(row)
                for row in (
                    await connection.execute(select(projects).order_by(projects.c.name))
                ).mappings()
            ]

    async def project(self, project_id: str) -> dict[str, Any]:
        """Resolve one registered workspace by its stable project identifier."""
        return await self._one(projects, project_id)

    async def create_project(self, name: str, workspace_root: str) -> dict[str, Any]:
        """Register one folder once without changing existing project or Run roots."""
        async with self.lock, self.engine.begin() as connection:
            if (
                await connection.execute(
                    select(projects.c.id).where(projects.c.workspace_root == workspace_root)
                )
            ).first():
                raise AppError(
                    "project_exists", "This workspace folder is already registered.", 409
                )
            value = {
                "id": uid(),
                "name": name,
                "workspace_root": workspace_root,
                "created_at": now(),
            }
            await connection.execute(insert(projects).values(**value))
        return value

    async def new_session(
        self,
        title: str = "New investigation",
        project_id: str | None = None,
        kind: str = "chat",
    ) -> dict[str, Any]:
        """Create an empty conversation bound to a project or the default workspace.

        `kind="eval"` hosts an eval item's Run and is hidden from the conversation list."""
        value = {
            "id": uid(),
            "title": title[:120],
            "revision": 0,
            "active_run_id": None,
            "messages": [],
            "created_at": now(),
            "folder_id": None,
            "project_id": project_id,
            "archived": False,
            "title_locked": title != "New investigation",
            "copied_from_session_id": None,
            "kind": kind,
            "grants": [],
        }
        async with self.lock, self.engine.begin() as connection:
            if (
                project_id
                and not (
                    await connection.execute(
                        select(projects.c.id).where(projects.c.id == project_id)
                    )
                ).first()
            ):
                raise AppError("not_found", "Project does not exist.", 404)
            await connection.execute(insert(sessions).values(**value))
        return _session_view(value)

    async def session(self, identifier: str) -> dict[str, Any]:
        """Load the authoritative history and active-Run reference."""
        return _session_view(await self._one(sessions, identifier))

    async def list_sessions(self) -> list[dict[str, Any]]:
        """Return conversation summaries, newest first, without their message histories."""
        columns = [
            column
            for column in sessions.c
            if column.name not in {"messages", "title_locked", "kind"}
        ]
        query = (
            select(*columns, func.json_array_length(sessions.c.messages).label("message_count"))
            .where(sessions.c.kind == "chat")
            .order_by(sessions.c.created_at.desc())
        )
        async with self.engine.connect() as connection:
            return [dict(row) for row in (await connection.execute(query)).mappings()]

    async def effective_history(self, session: dict[str, Any]) -> list[dict[str, Any]]:
        """Project an explicit summary plus untouched suffix without rewriting original history."""
        if not session.get("compaction_id"):
            return session["messages"]
        record = await self.compaction(session["compaction_id"])
        data = record["data"]
        count = data["covered_messages"]
        if (
            record["status"] != "succeeded"
            or digest(session["messages"][:count]) != data["source_hash"]
        ):
            raise AppError(
                "context_changed", "The saved summary no longer matches its source history.", 409
            )
        return [
            {
                "role": "user",
                "content": "Summary of earlier conversation (derived, not original messages):\n"
                + data["summary"],
                "source": "context:compaction:" + record["id"],
                "tool_calls": [],
                "tool_call_id": None,
            }
        ] + session["messages"][count:]

    async def compaction(self, identifier: str) -> dict[str, Any]:
        """Read the original summary input, provider calls and current job outcome."""
        return await self._one(compactions, identifier)

    async def _has_process(self, connection: AsyncConnection, session_id: str) -> bool:
        """Prevent deletion/workspace moves while original native processes can still write."""
        return (
            await connection.execute(
                select(processes.c.id)
                .join(runs, processes.c.run_id == runs.c.id)
                .where(
                    runs.c.session_id == session_id,
                    processes.c.status.in_(ACTIVE_PROCESSES),
                )
                .limit(1)
            )
        ).first() is not None

    async def process(self, identifier: str) -> dict[str, Any]:
        """Read one durable process intent; this record never recreates its OS handles."""
        return await self._one(processes, identifier)

    async def save_process(self, identifier: str, run_id: str, status: str, data: dict) -> None:
        """Commit process intent/progress before effects or before returning a lifecycle result."""
        async with self.lock, self.engine.begin() as connection:
            existing = (
                await connection.execute(
                    select(processes.c.status).where(processes.c.id == identifier)
                )
            ).first()
            if existing:
                if existing[0] not in ACTIVE_PROCESSES:
                    return
                await connection.execute(
                    update(processes)
                    .where(processes.c.id == identifier)
                    .values(status=status, data=data)
                )
            else:
                await connection.execute(
                    insert(processes).values(
                        id=identifier, run_id=run_id, status=status, created_at=now(), data=data
                    )
                )

    async def list_processes(self, session_id: str) -> list[dict[str, Any]]:
        """List original process ownership through Runs, excluding copied trace provenance."""
        await self.session(session_id)
        async with self.engine.connect() as connection:
            rows = await connection.execute(
                select(processes)
                .join(runs, processes.c.run_id == runs.c.id)
                .where(runs.c.session_id == session_id)
                .order_by(processes.c.created_at)
            )
            return [dict(row._mapping) for row in rows]

    async def list_compactions(self, session_id: str) -> list[dict[str, Any]]:
        """List owned context-reduction evidence in chronological order."""
        async with self.engine.connect() as connection:
            return [
                dict(row)
                for row in (
                    await connection.execute(
                        select(compactions)
                        .where(compactions.c.session_id == session_id)
                        .order_by(compactions.c.created_at)
                    )
                ).mappings()
            ]

    async def begin_compaction(
        self, session_id: str, expected_revision: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Reserve an idle conversation without changing history or its revision."""
        value = {
            "id": uid(),
            "session_id": session_id,
            "status": "running",
            "created_at": now(),
            "data": data,
        }
        async with self.lock, self.engine.begin() as connection:
            session = (
                (await connection.execute(select(sessions).where(sessions.c.id == session_id)))
                .mappings()
                .one_or_none()
            )
            if session is None:
                raise AppError("not_found", "Conversation does not exist.", 404)
            if session["revision"] != expected_revision:
                raise AppError(
                    "revision_conflict", "Conversation changed. Reload before compacting.", 409
                )
            if session["active_run_id"] or session["compacting_id"] or session["archived"]:
                raise AppError("session_busy", "Compact an idle restored conversation.", 409)
            await connection.execute(insert(compactions).values(**value))
            await connection.execute(
                update(sessions)
                .where(sessions.c.id == session_id)
                .values(compacting_id=value["id"])
            )
        return value

    async def save_compaction(
        self, identifier: str, data: dict[str, Any], status: str = "running"
    ) -> None:
        """Persist reduction evidence and atomically activate only a valid successful summary."""
        async with self.lock, self.engine.begin() as connection:
            record = (
                (
                    await connection.execute(
                        select(compactions).where(compactions.c.id == identifier)
                    )
                )
                .mappings()
                .one()
            )
            session = (
                (
                    await connection.execute(
                        select(sessions).where(sessions.c.id == record["session_id"])
                    )
                )
                .mappings()
                .one()
            )
            if record["status"] != "running":
                return
            if status == "succeeded" and (
                session["revision"] != data["source_revision"]
                or digest(session["messages"][: data["covered_messages"]]) != data["source_hash"]
            ):
                status, data = "failed", {
                    **data,
                    "error": "Conversation changed during compaction; the summary was not activated.",
                }
            await connection.execute(
                update(compactions)
                .where(compactions.c.id == identifier)
                .values(status=status, data=data)
            )
            if status != "running" and session["compacting_id"] == identifier:
                changes = {"compacting_id": None}
                if status == "succeeded":
                    changes.update(compaction_id=identifier, revision=session["revision"] + 1)
                await connection.execute(
                    update(sessions).where(sessions.c.id == record["session_id"]).values(**changes)
                )

    async def reset_compaction(self, session_id: str, expected_revision: int) -> None:
        """Restore full effective history while retaining summary and original evidence."""
        async with self.lock, self.engine.begin() as connection:
            session = (
                (await connection.execute(select(sessions).where(sessions.c.id == session_id)))
                .mappings()
                .one_or_none()
            )
            if session is None:
                raise AppError("not_found", "Conversation does not exist.", 404)
            if session["revision"] != expected_revision:
                raise AppError("revision_conflict", "Conversation changed. Reload first.", 409)
            if session["active_run_id"] or session["compacting_id"]:
                raise AppError("session_busy", "Finish current work before restoring context.", 409)
            await connection.execute(
                update(sessions)
                .where(sessions.c.id == session_id)
                .values(compaction_id=None, revision=session["revision"] + 1)
            )

    async def list_folders(self) -> list[dict[str, Any]]:
        """List flat folders alphabetically, including empty folders."""
        async with self.engine.connect() as connection:
            return [
                dict(row)
                for row in (
                    await connection.execute(select(folders).order_by(folders.c.name))
                ).mappings()
            ]

    async def save_folder(
        self, name: str, folder_id: str | None = None, expected_revision: int = 0
    ) -> dict[str, Any]:
        """Create or rename a folder, rejecting stale edits and duplicate names."""
        async with self.lock, self.engine.begin() as connection:
            current = None
            if folder_id:
                current = (
                    (await connection.execute(select(folders).where(folders.c.id == folder_id)))
                    .mappings()
                    .first()
                )
                if current is None:
                    raise AppError("not_found", "Folder does not exist.", 404)
                if current["revision"] != expected_revision:
                    raise AppError(
                        "revision_conflict", "Folder changed. Reload before saving.", 409
                    )
            duplicate = (
                await connection.execute(select(folders.c.id).where(folders.c.name == name))
            ).scalar_one_or_none()
            if duplicate and duplicate != folder_id:
                raise AppError("folder_name", "A folder with this name already exists.", 409)
            value = {
                "id": folder_id or uid(),
                "name": name,
                "revision": expected_revision + 1,
                "created_at": current["created_at"] if current else now(),
            }
            if current:
                await connection.execute(
                    update(folders).where(folders.c.id == folder_id).values(**value)
                )
            else:
                await connection.execute(insert(folders).values(**value))
        return value

    async def delete_folder(self, folder_id: str) -> None:
        """Ungroup conversations before deleting their folder; never delete their history."""
        async with self.lock, self.engine.begin() as connection:
            if not (
                await connection.execute(select(folders.c.id).where(folders.c.id == folder_id))
            ).first():
                raise AppError("not_found", "Folder does not exist.", 404)
            await connection.execute(
                update(sessions)
                .where(sessions.c.folder_id == folder_id)
                .values(folder_id=None, revision=sessions.c.revision + 1)
            )
            await connection.execute(delete(folders).where(folders.c.id == folder_id))

    async def add_grant(self, session_id: str, grant: dict[str, Any]) -> None:
        """Record a standing approval without a revision change, as it can arrive mid-Run."""
        async with self.lock, self.engine.begin() as connection:
            current = (
                await connection.execute(
                    select(sessions.c.grants).where(sessions.c.id == session_id)
                )
            ).scalar_one()
            if any(
                item["tool"] == grant["tool"] and item.get("prefix") == grant.get("prefix")
                for item in current
            ):
                return
            await connection.execute(
                update(sessions).where(sessions.c.id == session_id).values(grants=[*current, grant])
            )

    async def update_session(self, session_id: str, request: SessionUpdate) -> dict[str, Any]:
        """Apply a versioned metadata edit without touching messages or Run snapshots."""
        async with self.lock, self.engine.begin() as connection:
            current = (
                (await connection.execute(select(sessions).where(sessions.c.id == session_id)))
                .mappings()
                .first()
            )
            if current is None:
                raise AppError("not_found", "Conversation does not exist.", 404)
            if current["revision"] != request.expected_revision:
                raise AppError(
                    "revision_conflict", "Conversation changed. Reload before editing.", 409
                )
            changes: dict[str, Any] = {}
            if "project_id" in request.model_fields_set:
                if (
                    current["active_run_id"]
                    or current["compacting_id"]
                    or await self._has_process(connection, session_id)
                ):
                    raise AppError(
                        "session_busy",
                        "Finish or cancel the active Run before changing its workspace.",
                        409,
                    )
                if (
                    request.project_id
                    and not (
                        await connection.execute(
                            select(projects.c.id).where(projects.c.id == request.project_id)
                        )
                    ).first()
                ):
                    raise AppError("not_found", "Project does not exist.", 404)
                changes["project_id"] = request.project_id
            if request.title is not None:
                changes.update(title=request.title, title_locked=True)
            if request.archived is not None:
                if (
                    current["active_run_id"]
                    or current["compacting_id"]
                    or await self._has_process(connection, session_id)
                ):
                    raise AppError(
                        "session_busy", "Finish or cancel the active Run before archiving.", 409
                    )
                changes["archived"] = request.archived
            if "folder_id" in request.model_fields_set:
                if (
                    request.folder_id
                    and not (
                        await connection.execute(
                            select(folders.c.id).where(folders.c.id == request.folder_id)
                        )
                    ).first()
                ):
                    raise AppError("not_found", "Folder does not exist.", 404)
                changes["folder_id"] = request.folder_id
            if request.grants is not None:
                changes["grants"] = [grant.model_dump() for grant in request.grants]
            if changes:
                changes["revision"] = current["revision"] + 1
                await connection.execute(
                    update(sessions).where(sessions.c.id == session_id).values(**changes)
                )
            return _session_view({**current, **changes})

    async def copy_session(self, session_id: str, request: SessionCopy) -> dict[str, Any]:
        """Duplicate saved history or fork through a successful turn without executing anything."""
        async with self.lock, self.engine.begin() as connection:
            source = (
                (await connection.execute(select(sessions).where(sessions.c.id == session_id)))
                .mappings()
                .first()
            )
            if source is None:
                raise AppError("not_found", "Conversation does not exist.", 404)
            if source["revision"] != request.expected_revision:
                raise AppError(
                    "revision_conflict", "Conversation changed. Reload before copying.", 409
                )
            if source["active_run_id"] or source["compacting_id"]:
                raise AppError(
                    "session_busy", "Finish or cancel the active Run before copying.", 409
                )
            history = [
                dict(row)
                for row in (
                    await connection.execute(
                        select(runs)
                        .where(runs.c.session_id == session_id)
                        .order_by(runs.c.created_at, runs.c.id)
                    )
                ).mappings()
            ]
            if request.operation == "fork":
                point = next(
                    (
                        index
                        for index, run in enumerate(history)
                        if run["id"] == request.through_run_id and run["status"] == "succeeded"
                    ),
                    None,
                )
                if point is None:
                    raise AppError(
                        "fork_point", "Choose a completed successful turn in this conversation."
                    )
                history = history[: point + 1]
            elif request.through_run_id is not None:
                raise AppError("copy_boundary", "Only a fork accepts a turn boundary.")
            if any(run["status"] not in TERMINAL for run in history):
                raise AppError("session_busy", "Only finished Runs can be copied.", 409)
            messages = source["messages"]
            if request.operation == "fork":
                messages = []
                for run in history:
                    if run["status"] == "succeeded":
                        messages.append(
                            {
                                "role": "user",
                                "content": run["snapshot"]["input"],
                                "source": "history",
                                "tool_calls": [],
                                "tool_call_id": None,
                            }
                        )
                        messages.extend(
                            {**message, "source": "history", "blocks": []}
                            for message in run["messages"]
                        )
            copied = {
                "id": uid(),
                "title": f"{source['title'][:114]} {request.operation if request.operation == 'fork' else 'copy'}",
                "revision": 0,
                "active_run_id": None,
                "messages": messages,
                "created_at": now(),
                "folder_id": source["folder_id"],
                "project_id": source["project_id"],
                "archived": False,
                "title_locked": True,
                "copied_from_session_id": session_id,
                # Standing approvals are a decision about one conversation; copies ask again.
                "grants": [],
            }
            await connection.execute(insert(sessions).values(**copied))
            if source["compaction_id"]:
                reduction = (
                    (
                        await connection.execute(
                            select(compactions).where(compactions.c.id == source["compaction_id"])
                        )
                    )
                    .mappings()
                    .one()
                )
                if reduction["data"]["covered_messages"] <= len(messages):
                    copied_reduction = {
                        **reduction,
                        "id": uid(),
                        "session_id": copied["id"],
                        "data": {**reduction["data"], "source_compaction_id": reduction["id"]},
                    }
                    await connection.execute(insert(compactions).values(**copied_reduction))
                    copied["compaction_id"] = copied_reduction["id"]
                    await connection.execute(
                        update(sessions)
                        .where(sessions.c.id == copied["id"])
                        .values(compaction_id=copied_reduction["id"])
                    )
            for original in history:
                new_id = uid()
                # Preserve the original immutable snapshot and wire evidence. Copies own their
                # records, but are never new model requests or resumable checkpoints.
                clone = {
                    **original,
                    "id": new_id,
                    "session_id": copied["id"],
                    "preview_id": uid(),
                    "idempotency_key": uid(),
                    "latest_event_seq": 0,
                    "source_run_id": original["source_run_id"] or original["id"],
                }
                await connection.execute(insert(runs).values(**clone))
                for call in (
                    await connection.execute(select(calls).where(calls.c.run_id == original["id"]))
                ).mappings():
                    call_id = new_id + call["id"][len(original["id"]) :]
                    await connection.execute(
                        insert(calls).values(
                            **{
                                **call,
                                "id": call_id,
                                "run_id": new_id,
                                "data": {
                                    **call["data"],
                                    "copied_from_call_id": call["id"],
                                    "copied_from_run_id": original["id"],
                                },
                            }
                        )
                    )
                await self._event(
                    connection, new_id, "run.copied", {"source_run_id": clone["source_run_id"]}
                )
        return _session_view(copied)

    async def put_preview(self, identifier: str, data: dict[str, Any]) -> None:
        """Persist the compiled preview and its immutable execution snapshot."""
        async with self.lock, self.engine.begin() as connection:
            await connection.execute(insert(previews).values(id=identifier, data=data))

    async def preview(self, identifier: str) -> dict[str, Any]:
        """Load a previously prepared input by its stable identifier."""
        return (await self._one(previews, identifier))["data"]

    async def create_run(
        self,
        preview_id: str,
        request: dict[str, Any],
        key: str,
        submission: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically claim a fresh preview and session, or return an idempotent retry."""
        async with self.lock, self.engine.begin() as connection:
            existing = (
                (await connection.execute(select(runs).where(runs.c.idempotency_key == key)))
                .mappings()
                .first()
            )
            if existing:
                if existing["request_hash"] != digest(submission or request):
                    raise AppError(
                        "idempotency_conflict", "This idempotency key has different input.", 409
                    )
                return dict(existing)
            if (
                await connection.execute(select(runs.c.id).where(runs.c.preview_id == preview_id))
            ).first():
                raise AppError("preview_consumed", "Create a new preview for another Run.", 409)
            preview = (
                await connection.execute(select(previews.c.data).where(previews.c.id == preview_id))
            ).scalar_one_or_none()
            if preview is None:
                raise AppError("not_found", "Preview not found.", 404)
            if preview["expires_at"] <= now() or request["input_hash"] != preview["input_hash"]:
                raise AppError(
                    "stale_preview", "Preview expired or input changed. Preview again.", 409
                )
            snapshot = preview["snapshot"]
            session = (
                (
                    await connection.execute(
                        select(sessions).where(sessions.c.id == snapshot["session_id"])
                    )
                )
                .mappings()
                .one()
            )
            config = (await connection.execute(select(settings_table.c.data))).scalar_one()
            preset = (
                await connection.execute(
                    select(presets.c.data).where(presets.c.id == snapshot["preset"]["id"])
                )
            ).scalar_one()
            if session["compacting_id"]:
                raise AppError(
                    "session_busy",
                    "Context compaction is running. Wait for it, or stop it in Context.",
                    409,
                )
            if session["active_run_id"]:
                raise AppError("session_busy", "Finish or cancel the current Run first.", 409)
            if session["archived"]:
                raise AppError(
                    "session_archived", "Restore this conversation before running a task.", 409
                )
            if (
                session["revision"] != request["expected_session_revision"]
                or session["revision"] != snapshot["session_revision"]
                or config["revision"] != snapshot["config"]["revision"]
                or preset["revision"] != snapshot["preset"]["revision"]
            ):
                raise AppError(
                    "stale_preview", "Configuration or conversation changed. Preview again.", 409
                )
            stamp = now()
            value = {
                "id": uid(),
                "session_id": session["id"],
                "preview_id": preview_id,
                "idempotency_key": key,
                "request_hash": digest(submission or request),
                "snapshot": snapshot,
                "status": "queued",
                "stop_reason": None,
                "messages": [],
                "partial_output": "",
                "latest_event_seq": 0,
                "approval": None,
                "error": None,
                "usage": {"input_tokens": None, "output_tokens": None},
                "created_at": stamp,
                "updated_at": stamp,
            }
            await connection.execute(insert(runs).values(**value))
            # The Run now owns the snapshot; runs.preview_id keeps the preview single-use.
            await connection.execute(delete(previews).where(previews.c.id == preview_id))
            # A command's title is what the user typed, not its expanded template.
            command = snapshot.get("command")
            typed = (
                f"/{command['name']} {command['arguments']}".strip()
                if command
                else snapshot["input"]
            )
            await connection.execute(
                update(sessions)
                .where(sessions.c.id == session["id"])
                .values(
                    active_run_id=value["id"],
                    title=typed[:80] if not session["title_locked"] else session["title"],
                    title_locked=True,
                )
            )
            await self._event(connection, value["id"], "run.queued", {})
            value["latest_event_seq"] = 1
        return value

    async def submitted_run(self, key: str, submission: dict[str, Any]) -> dict[str, Any] | None:
        """Resolve a direct submission retry before creating another input snapshot."""
        async with self.engine.connect() as connection:
            row = (
                (await connection.execute(select(runs).where(runs.c.idempotency_key == key)))
                .mappings()
                .first()
            )
        if row is not None and row["request_hash"] != digest(submission):
            raise AppError("idempotency_conflict", "This idempotency key has different input.", 409)
        return dict(row) if row else None

    async def run(self, identifier: str) -> dict[str, Any]:
        """Read a Run including its private frozen snapshot and current projection."""
        return await self._one(runs, identifier)

    async def run_progress(self, identifier: str) -> dict[str, Any]:
        """Read only a Run's status and event cursor, e.g. for each SSE polling tick."""
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        select(runs.c.status, runs.c.latest_event_seq).where(
                            runs.c.id == identifier
                        )
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise AppError("not_found", "The requested record does not exist.", 404)
        return dict(row)

    async def run_projections(
        self, *, run_id: str | None = None, session_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Read browser-visible Run state with its open calls, without frozen snapshots.

        Snapshots contain the whole prior history, so loading them for every listed turn made
        reading a long conversation quadratic. SQLite extracts the few snapshot fields used."""
        snapshot = runs.c.snapshot
        query = select(
            *(column for column in runs.c if column.name in RUN_PROJECTION),
            func.json_extract(snapshot, "$.input").label("input"),
            func.json_extract(snapshot, "$.workspace_root").label("workspace_root"),
            func.json_extract(snapshot, "$.preset.name").label("preset_name"),
            func.json_extract(snapshot, "$.config.provider.adapter").label("provider"),
            func.json_extract(snapshot, "$.config.provider.profile_id").label("provider_id"),
            func.json_extract(snapshot, "$.config.provider.model").label("model"),
            func.json_extract(snapshot, "$.config.provider.reasoning_effort").label(
                "reasoning_effort"
            ),
            func.json_extract(snapshot, "$.access").label("access"),
            func.json_extract(snapshot, "$.command").label("command"),
        ).order_by(runs.c.created_at, runs.c.id)
        if run_id is not None:
            query = query.where(runs.c.id == run_id)
        if session_id is not None:
            query = query.where(runs.c.session_id == session_id)
        async with self.engine.connect() as connection:
            values = [dict(row) for row in (await connection.execute(query)).mappings()]
            pending = (
                (
                    await connection.execute(
                        select(calls)
                        .where(
                            calls.c.run_id.in_([value["id"] for value in values]),
                            calls.c.status.in_(["executing", "outcome_unknown"]),
                        )
                        .order_by(calls.c.id)
                    )
                )
                .mappings()
                .all()
            )
            changes = await self._file_changes(connection, [value["id"] for value in values])
        for value in values:
            value["open_calls"] = [dict(call) for call in pending if call["run_id"] == value["id"]]
            value["changed_files"] = list(net_changes(changes.get(value["id"], [])))
            value["command"] = json.loads(value["command"]) if value["command"] else None
        return values

    async def file_changes(self, run_id: str) -> list[dict[str, Any]]:
        """A Run's recorded file tool changes in execution order."""
        async with self.engine.connect() as connection:
            return (await self._file_changes(connection, [run_id])).get(run_id, [])

    @staticmethod
    async def _file_changes(
        connection: AsyncConnection, run_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Read only the file change lists of the given Runs' tool receipts."""
        rows = (
            await connection.execute(
                select(
                    calls.c.id,
                    calls.c.run_id,
                    func.json_extract(calls.c.data, "$.file_changes").label("changes"),
                ).where(
                    calls.c.run_id.in_(run_ids),
                    calls.c.kind == "tool",
                    func.json_extract(calls.c.data, "$.file_changes").is_not(None),
                )
            )
        ).all()
        result: dict[str, list[dict[str, Any]]] = {}
        for row in sorted(rows, key=lambda item: call_order(item.id)):
            result.setdefault(row.run_id, []).extend(json.loads(row.changes))
        return result

    async def calls_with_status(self, run_id: str, statuses: list[str]) -> list[dict[str, Any]]:
        """Read the few receipts in the given states, such as executing or uncertain calls."""
        async with self.engine.connect() as connection:
            rows = await connection.execute(
                select(calls).where(calls.c.run_id == run_id, calls.c.status.in_(statuses))
            )
            return sorted(
                (dict(row) for row in rows.mappings()), key=lambda call: call_order(call["id"])
            )

    async def call_summaries(self, run_id: str, kind: str) -> list[dict[str, Any]]:
        """Read per-call attempts and usage without loading request and response evidence."""
        data = calls.c.data
        query = select(
            func.coalesce(func.json_extract(data, "$.attempts"), 1).label("attempts"),
            func.json_extract(data, "$.usage.input_tokens").label("input_tokens"),
            func.json_extract(data, "$.usage.output_tokens").label("output_tokens"),
            func.json_extract(data, "$.cost").label("cost"),
            func.coalesce(func.json_array_length(data, "$.attempt_history"), 0).label("retries"),
        ).where(calls.c.run_id == run_id, calls.c.kind == kind)
        async with self.engine.connect() as connection:
            return [dict(row) for row in (await connection.execute(query)).mappings()]

    async def latest_model_usage(self, session_id: str) -> dict[str, Any] | None:
        """Read the most recent succeeded model call's usage and model for a conversation.

        Each request resends the whole conversation prefix, so the latest request's reported
        prompt size is the live context usage; summing across calls would multiply-count it."""
        data = calls.c.data
        # Calls carry no timestamp; their IDs encode run step order (call_order). The latest
        # request is the highest-step model call of the most recently created run.
        query = (
            select(
                calls.c.id.label("call_id"),
                runs.c.created_at.label("run_created"),
                func.json_extract(data, "$.usage.input_tokens").label("input_tokens"),
                func.json_extract(data, "$.usage.output_tokens").label("output_tokens"),
                func.json_extract(data, "$.usage.cache_read_input_tokens").label("cache_read"),
                func.json_extract(data, "$.usage.cache_creation_input_tokens").label("cache_write"),
                func.json_extract(data, "$.response_metadata.model").label("model"),
                func.json_extract(runs.c.snapshot, "$.config.provider.adapter").label("adapter"),
            )
            .select_from(calls.join(runs, calls.c.run_id == runs.c.id))
            .where(
                runs.c.session_id == session_id,
                calls.c.kind == "model",
                calls.c.status == "succeeded",
            )
        )
        async with self.engine.connect() as connection:
            rows = [dict(row) for row in (await connection.execute(query)).mappings()]
        if not rows:
            return None
        return max(rows, key=lambda row: (row["run_created"], call_order(row["call_id"])))

    async def create_eval(self, name: str, cases: Any, variants: Any) -> dict[str, Any]:
        """Store a new evaluation definition at revision 0, rejecting a duplicate name."""
        value = {
            "id": uid(),
            "name": name,
            "revision": 0,
            "created_at": now(),
            "data": {"cases": cases, "variants": variants},
        }
        async with self.lock, self.engine.begin() as connection:
            if (await connection.execute(select(evals.c.id).where(evals.c.name == name))).first():
                raise AppError("duplicate_name", "An evaluation with this name exists.", 409)
            await connection.execute(insert(evals).values(**value))
        return value

    async def update_eval(
        self, eval_id: str, expected_revision: int, name: str, cases: Any, variants: Any
    ) -> dict[str, Any]:
        """Replace a definition under its observed revision, bumping it by one."""
        async with self.lock, self.engine.begin() as connection:
            row = (
                (await connection.execute(select(evals).where(evals.c.id == eval_id)))
                .mappings()
                .first()
            )
            if row is None:
                raise AppError("not_found", "The requested record does not exist.", 404)
            if row["revision"] != expected_revision:
                raise AppError(
                    "revision_conflict", "Evaluation changed. Reload before saving.", 409
                )
            clash = (
                await connection.execute(
                    select(evals.c.id).where(evals.c.name == name, evals.c.id != eval_id)
                )
            ).first()
            if clash:
                raise AppError("duplicate_name", "An evaluation with this name exists.", 409)
            value = {
                **dict(row),
                "name": name,
                "revision": expected_revision + 1,
                "data": {"cases": cases, "variants": variants},
            }
            await connection.execute(update(evals).where(evals.c.id == eval_id).values(**value))
        return value

    async def eval(self, eval_id: str) -> dict[str, Any]:
        """Load one evaluation definition."""
        return await self._one(evals, eval_id)

    async def list_evals(self) -> list[dict[str, Any]]:
        """List evaluations with their most recent batch, newest definition first."""
        async with self.engine.connect() as connection:
            rows = [
                dict(row)
                for row in (
                    await connection.execute(select(evals).order_by(evals.c.created_at.desc()))
                ).mappings()
            ]
            batches = [
                dict(row)
                for row in (
                    await connection.execute(
                        select(
                            eval_batches.c.id,
                            eval_batches.c.eval_id,
                            eval_batches.c.status,
                            eval_batches.c.created_at,
                        ).order_by(eval_batches.c.created_at)
                    )
                ).mappings()
            ]
        latest = {batch["eval_id"]: batch for batch in batches}
        for row in rows:
            row["last_batch"] = latest.get(row["id"])
        return rows

    async def delete_eval(self, eval_id: str) -> None:
        """Delete an evaluation and its batches; eval Run sessions are removed separately."""
        async with self.lock, self.engine.begin() as connection:
            await connection.execute(delete(evals).where(evals.c.id == eval_id))

    async def create_batch(self, eval_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Record a comparison run as it begins, with the frozen eval definition inside data."""
        value = {
            "id": uid(),
            "eval_id": eval_id,
            "status": "running",
            "created_at": now(),
            "data": data,
        }
        async with self.lock, self.engine.begin() as connection:
            await connection.execute(insert(eval_batches).values(**value))
        return value

    async def save_batch(self, batch_id: str, status: str, data: dict[str, Any]) -> None:
        """Persist batch progress so polling reflects each completed item."""
        async with self.lock, self.engine.begin() as connection:
            await connection.execute(
                update(eval_batches)
                .where(eval_batches.c.id == batch_id)
                .values(status=status, data=data)
            )

    async def batch(self, batch_id: str) -> dict[str, Any]:
        """Load one comparison batch."""
        return await self._one(eval_batches, batch_id)

    async def list_batches(self, eval_id: str) -> list[dict[str, Any]]:
        """List an evaluation's batches, newest first."""
        async with self.engine.connect() as connection:
            return [
                dict(row)
                for row in (
                    await connection.execute(
                        select(eval_batches)
                        .where(eval_batches.c.eval_id == eval_id)
                        .order_by(eval_batches.c.created_at.desc())
                    )
                ).mappings()
            ]

    async def list_runs(self, session_id: str) -> list[dict[str, Any]]:
        """Read conversation Runs in creation order."""
        await self.session(session_id)
        async with self.engine.connect() as connection:
            return [
                dict(row)
                for row in (
                    await connection.execute(
                        select(runs)
                        .where(runs.c.session_id == session_id)
                        .order_by(runs.c.created_at)
                    )
                ).mappings()
            ]

    async def _event(
        self, connection, run_id: str, event_type: str, payload: dict[str, Any], **changes
    ) -> RunEvent:
        """Append a sequence-numbered event and update its projection in one transaction."""
        previous = (
            await connection.execute(select(runs.c.latest_event_seq).where(runs.c.id == run_id))
        ).scalar_one()
        seq, stamp = previous + 1, now()
        await connection.execute(
            insert(events).values(
                run_id=run_id, seq=seq, type=event_type, timestamp=stamp, payload=payload
            )
        )
        await connection.execute(
            update(runs)
            .where(runs.c.id == run_id)
            .values(latest_event_seq=seq, updated_at=stamp, **changes)
        )
        return RunEvent(run_id=run_id, seq=seq, type=event_type, timestamp=stamp, payload=payload)

    async def _notify(self, value: RunEvent) -> None:
        """Observe a redacted committed copy outside the lock; failures cannot replay effects."""
        if self.on_event is None or value.type == "hook.failed":
            return
        try:
            async with asyncio.timeout(2):
                await self.on_event(RunEvent.model_validate(redact(value.model_dump())))
        except Exception as exc:
            await self.emit(
                value.run_id,
                "hook.failed",
                {
                    "event_seq": value.seq,
                    "exception_type": type(exc).__name__,
                },
            )

    async def emit(self, run_id: str, event_type: str, payload: dict[str, Any], **changes) -> None:
        """Commit an event and associated state changes under the writer lock."""
        async with self.lock, self.engine.begin() as connection:
            value = await self._event(connection, run_id, event_type, payload, **changes)
        await self._notify(value)

    async def delta(self, run_id: str, call_id: str, text: str) -> None:
        """Append streamed text and persist its replay event atomically."""
        async with self.lock, self.engine.begin() as connection:
            value = await self._event(
                connection,
                run_id,
                "model.delta",
                {"call_id": call_id, "text": text},
                partial_output=runs.c.partial_output + text,
            )
        await self._notify(value)

    async def finish(
        self, run_id: str, status: str, messages: list[dict], reason: str, error: str | None = None
    ) -> None:
        """Finish once, release the session, and commit history only on success."""
        async with self.lock, self.engine.begin() as connection:
            run = (
                (await connection.execute(select(runs).where(runs.c.id == run_id))).mappings().one()
            )
            if run["status"] in TERMINAL:
                return
            session = (
                (
                    await connection.execute(
                        select(sessions).where(sessions.c.id == run["session_id"])
                    )
                )
                .mappings()
                .one()
            )
            changes: dict[str, Any] = {"active_run_id": None}
            if status == "succeeded":
                changes.update(
                    revision=session["revision"] + 1,
                    messages=session["messages"]
                    + [
                        {
                            "role": "user",
                            "content": run["snapshot"]["input"],
                            "source": "history",
                            "tool_calls": [],
                            "tool_call_id": None,
                        }
                    ]
                    + [{**message, "source": "history", "blocks": []} for message in messages],
                )
            await connection.execute(
                update(sessions).where(sessions.c.id == run["session_id"]).values(**changes)
            )
            event_type = "run.completed" if status == "succeeded" else "run." + status
            value = await self._event(
                connection,
                run_id,
                event_type,
                {"stop_reason": reason},
                status=status,
                messages=messages,
                partial_output="",
                stop_reason=reason,
                error=error,
                approval=None,
            )
        await self._notify(value)

    async def read_events(self, run_id: str, after: int) -> list[dict[str, Any]]:
        """Read a bounded page strictly after the supplied persistent cursor."""
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(events)
                    .where(events.c.run_id == run_id, events.c.seq > after)
                    .order_by(events.c.seq)
                    .limit(200)
                )
            ).mappings()
            return [{"schema_version": 1, **dict(row)} for row in rows]

    async def call(self, identifier: str) -> dict[str, Any] | None:
        """Look up a durable call receipt; missing calls return None."""
        try:
            return await self._one(calls, identifier)
        except AppError:
            return None

    async def save_call(
        self, identifier: str, run_id: str, kind: str, status: str, data: dict, result: Any = None
    ) -> None:
        """Persist call intent or outcome at the model/tool side-effect boundary."""
        async with self.lock, self.engine.begin() as connection:
            exists = (
                await connection.execute(select(calls.c.id).where(calls.c.id == identifier))
            ).first()
            values = {
                "run_id": run_id,
                "kind": kind,
                "status": status,
                "data": data,
                "result": result,
            }
            if exists:
                await connection.execute(
                    update(calls).where(calls.c.id == identifier).values(**values)
                )
            else:
                await connection.execute(insert(calls).values(id=identifier, **values))

    async def trace(self, run_id: str) -> list[dict[str, Any]]:
        """Load stored model and tool evidence in execution order for inspection or recovery."""
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(select(calls).where(calls.c.run_id == run_id))
            ).mappings()
            return sorted((dict(row) for row in rows), key=lambda call: call_order(call["id"]))

    async def recover(self) -> list[str]:
        """Mark abandoned effects uncertain and return work that was still queued."""
        async with self.lock, self.engine.begin() as connection:
            # Provider completion after a crashed reduction is unknown; never replay it.
            for record in (
                await connection.execute(
                    select(compactions).where(compactions.c.status == "running")
                )
            ).mappings():
                data = dict(record["data"])
                data["error"] = "Service restarted; the unfinished summary was not activated."
                for call in data.get("calls", []):
                    if call["status"] == "executing":
                        call["status"] = "outcome_unknown"
                await connection.execute(
                    update(compactions)
                    .where(compactions.c.id == record["id"])
                    .values(status="interrupted", data=data)
                )
            await connection.execute(
                update(sessions)
                .where(sessions.c.compacting_id.is_not(None))
                .values(compacting_id=None)
            )
            for record in (
                await connection.execute(
                    select(processes).where(processes.c.status.in_(ACTIVE_PROCESSES))
                )
            ).mappings():
                data = {
                    **record["data"],
                    "stdin_closed": True,
                    "error": "Service restarted; inspect partial workspace effects before repeating the command.",
                    "ended_at": now(),
                }
                await connection.execute(
                    update(processes)
                    .where(processes.c.id == record["id"])
                    .values(status="interrupted", data=data)
                )
            await connection.execute(
                update(calls).where(calls.c.status == "executing").values(status="outcome_unknown")
            )
            stranded = (
                (
                    await connection.execute(
                        select(runs.c.id).where(runs.c.status.in_(["running", "cancelling"]))
                    )
                )
                .scalars()
                .all()
            )
            for run_id in stranded:
                await self._event(
                    connection,
                    run_id,
                    "run.interrupted",
                    {"reason": "process_restart"},
                    status="interrupted",
                )
            return list(
                (
                    await connection.execute(select(runs.c.id).where(runs.c.status == "queued"))
                ).scalars()
            )

    async def delete_session(self, session_id: str) -> list[str]:
        """Delete an idle session and cascade its owned business records."""
        async with self.lock, self.engine.begin() as connection:
            session = (
                (await connection.execute(select(sessions).where(sessions.c.id == session_id)))
                .mappings()
                .first()
            )
            if session is None:
                raise AppError("not_found", "Session does not exist.", 404)
            if (
                session["active_run_id"]
                or session["compacting_id"]
                or await self._has_process(connection, session_id)
            ):
                raise AppError(
                    "session_busy", "Stop active work before deleting this session.", 409
                )
            run_ids = list(
                (
                    await connection.execute(
                        select(runs.c.id).where(runs.c.session_id == session_id)
                    )
                ).scalars()
            )
            await connection.execute(delete(sessions).where(sessions.c.id == session_id))
            await connection.execute(
                delete(previews).where(
                    func.json_extract(previews.c.data, "$.snapshot.session_id") == session_id
                )
            )
        return run_ids
