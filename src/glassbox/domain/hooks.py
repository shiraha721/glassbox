"""Trusted Python callbacks and the selection of user-configured shell hooks."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Literal

from glassbox.domain import GlassboxConfig, RunEvent, ShellHook, ToolCall

EventHook = Callable[[RunEvent], Awaitable[None]]
ToolGuard = Callable[[ToolCall, GlassboxConfig], Awaitable[str | None]]


@dataclass(frozen=True)
class Hooks:
    """Observe committed events or further restrict tools; hooks cannot grant permission."""

    on_event: EventHook | None = None
    before_tool: ToolGuard | None = None


def matching_hooks(
    hooks: list[ShellHook],
    event: Literal["before_tool", "after_tool", "run_end"],
    tool: str | None = None,
) -> list[tuple[int, ShellHook]]:
    """The configured hooks for an event, with their positions, in configured order."""
    return [
        (index, hook)
        for index, hook in enumerate(hooks)
        if hook.event == event and (tool is None or fnmatchcase(tool, hook.tools))
    ]
