"""Small, framework-independent application contracts."""

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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

    def __init__(self, code: str, message: str, status: int = 422, details: Any = None) -> None:
        """Keep public error details separate from internal exception diagnostics."""
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details


class Contract(BaseModel):
    """Reject unknown fields at configuration and API boundaries."""

    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class ErrorInfo(Contract):
    """A displayable service failure with a stable code and diagnostic correlation ID."""

    code: str
    message: str
    details: Any = None
    request_id: str


class ErrorEnvelope(Contract):
    """The common HTTP error body, including field validation and unexpected service failures."""

    error: ErrorInfo


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

    adapter: Literal["fake", "openai", "anthropic"] = "openai"
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
    writes_workspace: bool = False


class StudioConfig(Contract):
    """Version provider, tool permissions, connections, and execution limits together."""

    revision: int = Field(default=0, ge=0)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    tools: dict[str, Literal["allow", "ask", "deny"]] = Field(
        default_factory=lambda: {
            "read_text": "allow",
            "list_files": "allow",
            "search_files": "allow",
            "search_text": "allow",
            "read_instructions": "allow",
            "write_text": "allow",
            "edit_text": "allow",
            "apply_patch": "allow",
            "update_plan": "allow",
            "start_process": "ask",
            "poll_process": "allow",
            "send_process_input": "ask",
            "stop_process": "allow",
            "fetch_url": "ask",
            "powershell" if os.name == "nt" else "bash": "allow",
            "read_memory": "allow",
            "save_memory": "allow",
            "load_skill": "allow",
            "save_skill": "allow",
            "echo": "ask",
        }
    )
    sandbox_mode: Literal["workspace-write", "read-only"] = "workspace-write"
    toolchain_roots: list[str] = Field(default_factory=list, max_length=3)
    network_hosts: list[str] = Field(default_factory=list, max_length=20)
    mcp_servers: list[MCPServer] = Field(default_factory=list, max_length=10)
    max_steps: int = Field(default=20, ge=1, le=100)
    max_tool_calls: int = Field(default=40, ge=1, le=200)
    tool_timeout: float = Field(default=60, ge=0.1, le=300)
    run_timeout: float = Field(default=600, ge=1, le=3600)

    @field_validator("network_hosts")
    @classmethod
    def exact_hosts(cls, value: list[str]) -> list[str]:
        """Accept exact DNS hostnames only; wildcards and URL credentials are not policy."""
        import re

        for host in value:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host) or ".." in host:
                raise ValueError("Use lowercase exact hostnames without ports, URLs or wildcards.")
        return list(dict.fromkeys(value))


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


class PlanItem(Contract):
    """One model-authored task step, separate from verified execution outcomes."""

    step: str = Field(min_length=1, max_length=240)
    status: Literal["pending", "in_progress", "completed"]


class TaskPlan(Contract):
    """A bounded progress update recorded through the ordinary tool receipt and message ledger."""

    explanation: str = Field(default="", max_length=1000)
    plan: list[PlanItem] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def one_active_step(self) -> "TaskPlan":
        """Reject simultaneous active steps and empty labels rather than silently normalizing them."""
        if sum(item.status == "in_progress" for item in self.plan) > 1:
            raise ValueError("At most one plan step can be in_progress.")
        if any(not item.step.strip() for item in self.plan):
            raise ValueError("Plan step labels must contain non-whitespace text.")
        return self


def latest_plan(messages: list[Message]) -> TaskPlan | None:
    """Project the last successful paired plan result without trusting arbitrary JSON tool output."""
    identifiers = {
        call.id
        for message in messages
        if message.role == "assistant"
        for call in message.tool_calls
        if call.name == "update_plan"
    }
    for message in reversed(messages):
        if message.role != "tool" or message.tool_call_id not in identifiers:
            continue
        try:
            return TaskPlan.model_validate_json(message.content)
        except ValueError:
            continue
    return None


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


class DraftPreviewRequest(Contract):
    """Inspect an unsaved preset without creating a conversation or a saved preview."""

    preset: Preset
    config_revision: int
    session_id: str | None = None
    project_id: str | None = None
    expected_session_revision: int | None = None
    input: str = Field(default="", max_length=100_000)
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


class PresetPackage(Contract):
    """A reusable prompt aggregate without runtime settings, secrets, history or effects."""

    format: Literal["rtagent.preset.v1"] = "rtagent.preset.v1"
    preset: Preset


class SkillPackage(Contract):
    """A bounded UTF-8 Skill directory manifest; import publishes only a new validated name."""

    format: Literal["rtagent.skill.v1"] = "rtagent.skill.v1"
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    files: dict[str, str]

    @field_validator("files")
    @classmethod
    def bounded_files(cls, value: dict[str, str]) -> dict[str, str]:
        """Bound expansion before any filesystem effects and require the actual entry document."""
        if "SKILL.md" not in value or not 1 <= len(value) <= 50:
            raise ValueError("Include SKILL.md and at most fifty UTF-8 files.")
        if (
            any(len(text.encode()) > 65536 for text in value.values())
            or sum(len(text.encode()) for text in value.values()) > 1024 * 1024
        ):
            raise ValueError("Package files are limited to 64 KiB each and 1 MiB total.")
        return value


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
    instructions: MemoryDocument | None
    memory: MemoryDocument
    skills: list[SkillInfo]
    skill_errors: dict[str, str]


class PromptInspection(Contract):
    """The compiled draft and prepared payload, with no persisted execution identity."""

    input_hash: str
    compiled: CompiledPrompt
    prepared_request: dict[str, Any]
    tools: list[ToolDefinition]
    transformations: list[str] = Field(default_factory=list)


class Preview(PromptInspection):
    """An expiring, persisted input that can be consumed by Run."""

    id: str
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


class FolderView(Contract):
    """A flat collection of conversations, independent of workspace files."""

    id: str
    name: str
    revision: int
    created_at: str


class FolderCreate(Contract):
    """Name a conversation folder without introducing nested paths."""

    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        """Reject invisible names and normalize surrounding whitespace."""
        if not value.strip():
            raise ValueError("Enter a name.")
        return value.strip()


class FolderUpdate(FolderCreate):
    """Rename only the folder revision the user has actually seen."""

    expected_revision: int = Field(ge=0)


class ProjectCreate(FolderCreate):
    """Register an existing local folder as a conversation workspace."""

    workspace_root: str = Field(min_length=1, max_length=4096)


class FolderPickerRequest(Contract):
    """Suggest the initial desktop location without registering a project or modifying files."""

    initial_path: str = Field(default="", max_length=4096)


class FolderSelection(Contract):
    """Distinguish a selected path, user cancellation and an unavailable native desktop."""

    status: Literal["selected", "cancelled", "unavailable"]
    path: str | None = None
    detail: str | None = None


class ProjectView(Contract):
    """A named workspace folder; tool policy remains in the shared settings aggregate."""

    id: str
    name: str
    workspace_root: str
    created_at: str


class SessionCreate(Contract):
    """Create a meaningfully named empty conversation in a selected project."""

    title: str = Field(default="New investigation", min_length=1, max_length=120)
    project_id: str | None = None

    @field_validator("title")
    @classmethod
    def nonempty_title(cls, value: str) -> str:
        """Keep explicit titles meaningful and consistent with folder names."""
        if not value.strip():
            raise ValueError("Conversation title cannot be empty.")
        return value.strip()


class SessionUpdate(Contract):
    """Edit conversation metadata; an explicit null removes folder membership."""

    expected_revision: int = Field(ge=0)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None
    folder_id: str | None = None
    project_id: str | None = None

    @field_validator("title")
    @classmethod
    def nonempty_title(cls, value: str | None) -> str | None:
        """Treat a supplied title as an intentional nonempty rename."""
        if value is not None and not value.strip():
            raise ValueError("Enter a conversation title.")
        return value.strip() if value is not None else None


class SessionCopy(Contract):
    """Copy saved evidence, optionally stopping at a completed turn for a fork."""

    expected_revision: int = Field(ge=0)
    operation: Literal["duplicate", "fork"]
    through_run_id: str | None = None


class CompactRequest(Contract):
    """Explicitly summarize older complete turns using observed session/settings revisions."""

    expected_revision: int = Field(ge=0)
    config_revision: int = Field(ge=0)
    keep_recent_turns: int = Field(default=2, ge=1, le=10)


class ContextRestore(Contract):
    """Restore the complete effective history using the observed conversation revision."""

    expected_revision: int = Field(ge=0)


class CompactionView(Contract):
    """Inspectable summary provenance; original messages and requests stay in local evidence."""

    id: str
    session_id: str
    status: Literal["running", "succeeded", "failed", "interrupted", "cancelled"]
    created_at: str
    source_revision: int
    covered_messages: int
    source_hash: str
    summary: str = ""
    source_compaction_id: str | None = None
    error: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    model_calls: int = 0


class ContextView(Contract):
    """Estimated history size and explicit summary boundaries, separate from actual model usage."""

    session_id: str
    raw_messages: int
    effective_messages: int
    raw_estimated_tokens: int
    effective_estimated_tokens: int
    context_window: int | None
    reserved_output_tokens: int
    active_compaction_id: str | None
    compacting_id: str | None
    compactions: list[CompactionView]


class SessionView(Contract):
    """Conversation history containing only committed successful turns."""

    id: str
    title: str
    revision: int
    active_run_id: str | None
    compaction_id: str | None = None
    compacting_id: str | None = None
    messages: list[Message]
    created_at: str
    folder_id: str | None = None
    project_id: str | None = None
    archived: bool = False
    copied_from_session_id: str | None = None


class RunView(Contract):
    """Browser projection of execution state, approvals, usage, and recovery needs."""

    id: str
    session_id: str
    input: str
    workspace_root: str
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
    source_run_id: str | None = None
    unknown_calls: list[dict[str, Any]] = Field(default_factory=list)
    plan: TaskPlan | None = None
    active_tool: dict[str, Any] | None = None


class ProcessInput(Contract):
    """Send bounded UTF-8 pipe input or EOF to an explicitly owned process."""

    text: str = Field(default="", max_length=8192)
    eof: bool = False


class ProcessView(Contract):
    """Persist a sandbox process lifecycle independently of an already completed turn."""

    id: str
    run_id: str
    session_id: str
    status: str
    created_at: str
    command: str
    cwd: str
    workspace_root: str
    mode: str
    pid: int | None = None
    stdin_closed: bool = False
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    result: dict[str, Any] | None = None
    error: str | None = None


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
