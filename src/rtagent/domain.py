"""Small, framework-independent application contracts."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def uid() -> str:
    """Create a stable, URL-safe identifier without coordination between callers."""
    return uuid4().hex


def now() -> str:
    """Return an ISO timestamp in UTC for storage and event ordering."""
    return datetime.now(UTC).isoformat()


def digest(value: Any) -> str:
    """Hash canonical JSON so equivalent mapping orders share one fingerprint."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def credential_reference(value: str) -> str:
    """Accept an optional environment variable name, never a literal secret."""
    if value and (not value.isidentifier() or not value.isascii()):
        raise ValueError("Use an environment variable name, not a secret value.")
    return value


def endpoint_url(value: str) -> str:
    """Keep URLs free of embedded credentials; authentication uses separate references."""
    if value:
        url = urlparse(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "Use an HTTP(S) URL without credentials, query parameters, or fragments."
            )
    return value


class AppError(Exception):
    """An expected application failure with a safe message and HTTP mapping."""

    def __init__(self, code: str, message: str, status: int = 422, details: Any = None):
        """Keep public error details separate from internal exception diagnostics."""
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details


class Contract(BaseModel):
    """Reject unknown fields at configuration and API boundaries."""

    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class Variable(Contract):
    """Declare the type and fallback used by literal template substitution."""

    type: Literal["string", "number", "boolean"] = "string"
    required: bool = True
    default: Any = None


class Block(Contract):
    """One configurable message, activated and ordered by the pure compiler."""

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
    """Version a collection of embedded blocks as a single editable aggregate."""

    id: str = Field(default_factory=uid)
    name: str = Field(min_length=1, max_length=120)
    revision: int = Field(default=0, ge=0)
    blocks: list[Block] = Field(default_factory=list, max_length=100)


class ProviderConfig(Contract):
    """Serializable provider options containing references, never API key values."""

    adapter: Literal["fake", "openai"] = "openai"
    model: str = ""
    base_url: str = "https://api.openai.com/v1"
    credential_ref: str = "OPENAI_API_KEY"
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=2048, ge=1, le=32768)
    context_window: int | None = Field(default=None, ge=256)

    @field_validator("credential_ref")
    @classmethod
    def credential_name(cls, value: str) -> str:
        """Accept environment variable names and reject literal credential values."""
        return credential_reference(value)

    @field_validator("base_url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        """Reject credentials embedded in a serialized provider URL."""
        return endpoint_url(value)


class MCPServer(Contract):
    """Connection settings for an explicitly configured MCP server."""

    id: str = Field(default_factory=uid)
    name: str = Field(min_length=1, max_length=60)
    transport: Literal["stdio", "streamable_http"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    url: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    credential_ref: str = ""

    @field_validator("credential_ref")
    @classmethod
    def credential_name(cls, value: str) -> str:
        """Keep MCP bearer credentials out of configuration snapshots."""
        return credential_reference(value)

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        """Validate an optional HTTP endpoint independently of its transport choice."""
        return endpoint_url(value)

    @field_validator("env")
    @classmethod
    def environment_references(cls, value: dict[str, str]) -> dict[str, str]:
        """Validate both child environment names and their server-side references."""
        for key, reference in value.items():
            if not key or not reference:
                raise ValueError("Each environment entry needs a name and a credential reference.")
            credential_reference(key)
            credential_reference(reference)
        return value


class ToolDefinition(Contract):
    """A namespaced tool schema pinned into each execution snapshot."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_id: str | None = None
    original_name: str | None = None
    schema_hash: str = ""


class StudioConfig(Contract):
    """Version provider, tool permissions, connections, and execution limits together."""

    revision: int = Field(default=0, ge=0)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    tools: dict[str, Literal["allow", "ask", "deny"]] = Field(
        default_factory=lambda: {
            "read_text": "allow",
            "list_files": "allow",
            "write_text": "allow",
            "edit_text": "allow",
            "bash": "allow",
            "read_memory": "allow",
            "save_memory": "allow",
            "load_skill": "allow",
            "save_skill": "allow",
            "echo": "ask",
        }
    )
    sandbox_mode: Literal["workspace-write", "read-only"] = "workspace-write"
    mcp_servers: list[MCPServer] = Field(default_factory=list, max_length=10)
    max_steps: int = Field(default=20, ge=1, le=100)
    max_tool_calls: int = Field(default=40, ge=1, le=200)
    tool_timeout: float = Field(default=60, ge=0.1, le=300)
    run_timeout: float = Field(default=600, ge=1, le=3600)


class ToolCall(Contract):
    """Preserve a model-generated call ID with its decoded arguments."""

    id: str
    name: str
    args: dict[str, Any]


class Message(Contract):
    """Canonical message with provenance and explicit tool-call pairing."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    source: str = "runtime"
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None


class CompiledPrompt(Contract):
    """Deterministic messages, activation decisions, provenance, and budget estimate."""

    messages: list[Message]
    activation_report: list[dict[str, Any]]
    source_map: list[dict[str, Any]]
    token_estimate: int
    budget_status: str
    input_hash: str


class PreviewRequest(Contract):
    """Compile a task for optional inspection or immediate execution."""

    session_id: str
    expected_session_revision: int
    preset_id: str
    preset_revision: int
    config_revision: int
    input: str = Field(min_length=1, max_length=100_000)
    variables: dict[str, Any] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list, max_length=20)


class MemoryDocument(Contract):
    """Editable project memory with a fingerprint for conflict detection."""

    path: str
    content: str
    hash: str


class MemorySave(Contract):
    """An explicit replacement of the memory text the user last read."""

    content: str = Field(max_length=65_536)
    expected_hash: str


class SkillInfo(Contract):
    """Lightweight skill catalog entry; bodies load only on demand."""

    name: str
    description: str
    path: str
    builtin: bool
    hash: str


class SkillDocument(SkillInfo):
    """Full skill source for inspection or an explicit model load."""

    content: str


class SkillSave(Contract):
    """A concise skill authored directly by the user or the skill-creator workflow."""

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1000)
    instructions: str = Field(min_length=1, max_length=65_536)
    expected_hash: str | None = None


class SandboxStatus(Contract):
    """Report the checked OS boundary, including when it is unavailable."""

    backend: str
    ready: bool
    network: str
    detail: str


class WorkspaceView(Contract):
    """File-backed workspace context and actual sandbox availability."""

    root: str
    sandbox: SandboxStatus
    memory: MemoryDocument
    skills: list[SkillInfo]
    skill_errors: dict[str, str]


class Preview(Contract):
    """An expiring compiled input and the exact prepared provider payload."""

    id: str
    input_hash: str
    compiled: CompiledPrompt
    prepared_request: dict[str, Any]
    tools: list[ToolDefinition]
    expires_at: str


class StartRun(Contract):
    """Consume a saved preview without accepting replacement execution settings."""

    preview_id: str
    input_hash: str
    expected_session_revision: int


class ApprovalDecision(Contract):
    """A human decision for one pending tool invocation."""

    decision: Literal["approve", "reject"]


class RecoveryDecision(Contract):
    """Explicitly retry an uncertain call or supply an externally verified result."""

    action: Literal["retry", "record"]
    result: str = ""


class ResumeRequest(Contract):
    """Require a decision for every call whose external outcome is unknown."""

    resolutions: dict[str, RecoveryDecision] = Field(default_factory=dict)


class RunEvent(Contract):
    """Versioned, persistent SSE envelope with a per-Run sequence number."""

    schema_version: int = 1
    run_id: str
    seq: int
    type: str
    timestamp: str
    payload: dict[str, Any]


class SessionView(Contract):
    """Conversation history containing only committed successful turns."""

    id: str
    title: str
    revision: int
    active_run_id: str | None
    messages: list[Message]
    created_at: str


class RunView(Contract):
    """Browser projection of execution state, approvals, usage, and recovery needs."""

    id: str
    session_id: str
    input: str
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


class CallView(Contract):
    """A persisted invocation with provider-specific evidence kept as structured data."""

    id: str
    run_id: str
    kind: str
    status: str
    data: dict[str, Any]
    result: Any


class TraceView(Contract):
    """Label redaction explicitly when exposing stored invocation evidence."""

    schema_version: int = 1
    redacted: bool
    calls: list[CallView]
