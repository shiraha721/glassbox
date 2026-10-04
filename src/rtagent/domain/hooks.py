"""Focused trusted Python callbacks for event observation and additional tool denial."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from rtagent.domain import RunEvent, StudioConfig, ToolCall

EventHook = Callable[[RunEvent], Awaitable[None]]
ToolGuard = Callable[[ToolCall, StudioConfig], Awaitable[str | None]]


@dataclass(frozen=True)
class Hooks:
    """Observe committed events or further restrict tools; hooks cannot grant permission."""

    on_event: EventHook | None = None
    before_tool: ToolGuard | None = None
