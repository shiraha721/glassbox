"""The current recorded tool call's trusted ownership and output callback."""

from contextvars import ContextVar
from dataclasses import dataclass

from rtagent.command_io import OutputCallback


@dataclass(frozen=True)
class ToolExecution:
    """Supply server-owned Run/session identity without accepting ownership from model arguments."""

    run_id: str
    session_id: str
    on_output: OutputCallback


current_execution: ContextVar[ToolExecution | None] = ContextVar(
    "rtagent_tool_execution", default=None
)
