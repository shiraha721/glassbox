"""SQLite persistence with short transactions and one local writer."""

import asyncio
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
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import create_async_engine

from rtagent.domain import (
    AppError,
    Preset,
    SessionCopy,
    SessionUpdate,
    StudioConfig,
    digest,
    now,
    uid,
)
from rtagent.prompts import default_preset, is_legacy_default

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
    Column("messages", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    Column("folder_id", String, ForeignKey("conversation_folders.id", ondelete="SET NULL")),
    Column("project_id", String, ForeignKey("projects.id")),
    Column("archived", Boolean, nullable=False, default=False),
    Column("title_locked", Boolean, nullable=False, default=False),
    Column("copied_from_session_id", String),
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
TERMINAL = {"succeeded", "failed", "cancelled"}


def _session_view(row: Any) -> dict[str, Any]:
    """Exclude the internal auto-title flag from the public conversation contract."""
    return {key: value for key, value in dict(row).items() if key != "title_locked"}


class Repository:
    """Persist business data with short SQLite transactions and serialized writers."""

    def __init__(self, database_url: str):
        """Configure the async engine and SQLite integrity settings."""
        self.url = database_url
        self.engine = create_async_engine(database_url)
        self.lock = asyncio.Lock()

        @event.listens_for(self.engine.sync_engine, "connect")
        def configure_sqlite(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    async def initialize(self) -> None:
        """Apply Alembic migrations and seed the initial preset and settings once."""

        def migrate() -> None:
            root = Path(__file__).resolve().parents[2]
            config = Config(str(root / "alembic.ini"))
            config.set_main_option("sqlalchemy.url", self.url.replace("+aiosqlite", ""))
            command.upgrade(config, "head")

        await asyncio.to_thread(migrate)
        async with self.lock, self.engine.begin() as connection:
            if not (await connection.execute(select(settings_table))).first():
                await connection.execute(
                    insert(settings_table).values(
                        id="default", data=StudioConfig(revision=1).model_dump()
                    )
                )
            existing = (await connection.execute(select(presets.c.data))).scalars().all()
            if not existing:
                preset = default_preset()
                await connection.execute(
                    insert(presets).values(id=preset.id, data=preset.model_dump())
                )
            else:
                for value in existing:
                    preset = Preset.model_validate(value)
                    if is_legacy_default(preset):
                        # Only migrate recognized starter content, never user edits or Run snapshots.
                        await connection.execute(
                            update(presets)
                            .where(presets.c.id == preset.id)
                            .values(data=default_preset(preset.revision + 1).model_dump())
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
        self, title: str = "New investigation", project_id: str | None = None
    ) -> dict[str, Any]:
        """Create an empty conversation bound to a project or the default workspace."""
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
        """Return the most recent local conversations for the sidebar."""
        async with self.engine.connect() as connection:
            return [
                _session_view(row)
                for row in (
                    await connection.execute(
                        select(sessions).order_by(sessions.c.created_at.desc())
                    )
                ).mappings()
            ]

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
                if current["active_run_id"]:
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
                if current["active_run_id"]:
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
            if source["active_run_id"]:
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
                            {**message, "source": "history"} for message in run["messages"]
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
            }
            await connection.execute(insert(sessions).values(**copied))
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
            if (
                await connection.execute(select(runs.c.id).where(runs.c.preview_id == preview_id))
            ).first():
                raise AppError("preview_consumed", "Create a new preview for another Run.", 409)
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
            await connection.execute(
                update(sessions)
                .where(sessions.c.id == session["id"])
                .values(
                    active_run_id=value["id"],
                    title=(
                        snapshot["input"][:80] if not session["title_locked"] else session["title"]
                    ),
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

    async def list_runs(self, session_id: str) -> list[dict[str, Any]]:
        """Read conversation Runs in creation order."""
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
    ) -> None:
        """Append a sequence-numbered event and update its projection in one transaction."""
        row = (await connection.execute(select(runs).where(runs.c.id == run_id))).mappings().one()
        seq, stamp = row["latest_event_seq"] + 1, now()
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

    async def emit(self, run_id: str, event_type: str, payload: dict[str, Any], **changes) -> None:
        """Commit an event and associated state changes under the writer lock."""
        async with self.lock, self.engine.begin() as connection:
            await self._event(connection, run_id, event_type, payload, **changes)

    async def delta(self, run_id: str, call_id: str, text: str) -> None:
        """Append streamed text and persist its replay event atomically."""
        async with self.lock, self.engine.begin() as connection:
            previous = (
                await connection.execute(select(runs.c.partial_output).where(runs.c.id == run_id))
            ).scalar_one()
            await self._event(
                connection,
                run_id,
                "model.delta",
                {"call_id": call_id, "text": text},
                partial_output=previous + text,
            )

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
                    + [{**message, "source": "history"} for message in messages],
                )
            await connection.execute(
                update(sessions).where(sessions.c.id == run["session_id"]).values(**changes)
            )
            event_type = "run.completed" if status == "succeeded" else "run." + status
            await self._event(
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
        """Load stored model and tool evidence for inspection or recovery."""
        async with self.engine.connect() as connection:
            return [
                dict(row)
                for row in (
                    await connection.execute(
                        select(calls).where(calls.c.run_id == run_id).order_by(calls.c.id)
                    )
                ).mappings()
            ]

    async def recover(self) -> list[str]:
        """Mark abandoned effects uncertain and return work that was still queued."""
        async with self.lock, self.engine.begin() as connection:
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
            if session["active_run_id"]:
                raise AppError(
                    "session_busy", "Cancel the active Run before deleting this session.", 409
                )
            run_ids = list(
                (
                    await connection.execute(
                        select(runs.c.id).where(runs.c.session_id == session_id)
                    )
                ).scalars()
            )
            await connection.execute(delete(sessions).where(sessions.c.id == session_id))
            for preview in (await connection.execute(select(previews))).mappings():
                if preview["data"]["snapshot"]["session_id"] == session_id:
                    await connection.execute(delete(previews).where(previews.c.id == preview["id"]))
        return run_ids
