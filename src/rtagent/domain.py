"""Small, framework-independent application contracts."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def uid() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, details: Any = None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Variable(Contract):
    type: Literal["string", "number", "boolean"] = "string"
    required: bool = True
    default: Any = None


class Block(Contract):
    id: str = Field(default_factory=uid)
    name: str = Field(min_length=1, max_length=120)
    role: Literal["system", "user", "assistant"] = "system"
    template: str = Field(max_length=100_000)
    enabled: bool = True
    placement: Literal["before_history", "before_input"] = "before_history"
    order: int = Field(ge=0)
    variables: dict[str, Variable] = Field(default_factory=dict)
    provider_allowlist: list[str] | None = None
    model_allowlist: list[str] | None = None
    condition: dict[str, Any] | None = None


class Preset(Contract):
    id: str = Field(default_factory=uid)
    name: str = Field(min_length=1, max_length=120)
    revision: int = Field(default=0, ge=0)
    blocks: list[Block] = Field(default_factory=list, max_length=100)


class ProviderConfig(Contract):
    adapter: Literal["fake", "openai"] = "fake"
    model: str = "studio-demo"
    base_url: str = "http://127.0.0.1:11434/v1"
    credential_ref: str = "OPENAI_API_KEY"
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=2048, ge=1, le=32768)
    context_window: int | None = Field(default=None, ge=256)

    @field_validator("credential_ref")
    @classmethod
    def credential_name(cls, value: str) -> str:
        if value and (not value.isidentifier() or not value.isascii()):
            raise ValueError("Use an environment variable name, not a secret value.")
        return value


class MCPServer(Contract):
    id: str = Field(default_factory=uid)
    name: str = Field(min_length=1, max_length=60)
    transport: Literal["stdio", "streamable_http"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    url: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    credential_ref: str = ""


class ToolDefinition(Contract):
    name: str
    description: str
    input_schema: dict[str, Any]
    server_id: str | None = None
    original_name: str | None = None
    schema_hash: str = ""


class StudioConfig(Contract):
    revision: int = Field(default=0, ge=0)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    tools: dict[str, Literal["allow", "ask", "deny"]] = Field(
        default_factory=lambda: {"read_text": "allow", "echo": "ask"}
    )
    mcp_servers: list[MCPServer] = Field(default_factory=list, max_length=10)
    max_steps: int = Field(default=20, ge=1, le=100)
    max_tool_calls: int = Field(default=40, ge=1, le=200)
    tool_timeout: float = Field(default=60, ge=0.1, le=300)
    run_timeout: float = Field(default=600, ge=1, le=3600)


class ToolCall(Contract):
    id: str
    name: str
    args: dict[str, Any]


class Message(Contract):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    source: str = "runtime"
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None


class CompiledPrompt(Contract):
    messages: list[Message]
    activation_report: list[dict[str, Any]]
    source_map: list[dict[str, Any]]
    token_estimate: int
    budget_status: str
    input_hash: str


class PreviewRequest(Contract):
    session_id: str
    expected_session_revision: int
    preset_id: str
    preset_revision: int
    config_revision: int
    input: str = Field(min_length=1, max_length=100_000)
    variables: dict[str, Any] = Field(default_factory=dict)


class Preview(Contract):
    id: str
    input_hash: str
    compiled: CompiledPrompt
    prepared_request: dict[str, Any]
    tools: list[ToolDefinition]
    expires_at: str


class StartRun(Contract):
    preview_id: str
    input_hash: str
    expected_session_revision: int


class ApprovalDecision(Contract):
    decision: Literal["approve", "reject"]


class RecoveryDecision(Contract):
    action: Literal["retry", "record"]
    result: str = ""


class ResumeRequest(Contract):
    resolutions: dict[str, RecoveryDecision] = Field(default_factory=dict)


class RunEvent(Contract):
    schema_version: int = 1
    run_id: str
    seq: int
    type: str
    timestamp: str
    payload: dict[str, Any]


class SessionView(Contract):
    id: str
    title: str
    revision: int
    active_run_id: str | None
    messages: list[Message]
    created_at: str


class RunView(Contract):
    id: str
    session_id: str
    status: str
    stop_reason: str | None
    messages: list[Message]
    partial_output: str
    latest_event_seq: int
    approval: dict[str, Any] | None
    error: str | None
    created_at: str
    updated_at: str
    preset_name: str
    provider: str
    usage: dict[str, Any]
    unknown_calls: list[dict[str, Any]] = Field(default_factory=list)
