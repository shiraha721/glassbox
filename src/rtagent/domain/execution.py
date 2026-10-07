"""The current recorded tool call's trusted ownership, output callback and file changes."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

OutputCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class ToolExecution:
    """Supply server-owned Run/session identity without accepting ownership from model arguments."""

    run_id: str
    session_id: str
    on_output: OutputCallback
    # Workspace files the call changed, appended by the file tools as they write:
    # {"path", "before", "after"}, where each side is a content hash or None for absent.
    changes: list[dict[str, Any]] = field(default_factory=list)


current_execution: ContextVar[ToolExecution | None] = ContextVar(
    "rtagent_tool_execution", default=None
)
