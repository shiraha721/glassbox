"""Small, framework-independent application contracts."""

import hashlib
import json
import os
import re
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


ReasoningEffort = Literal["low", "medium", "high", "xhigh", "max"]
# Effort levels each request format accepts. Chat Completions takes reasoning_effort; the
# Messages API takes output_config.effort. Unsupported levels are rejected, never rewritten.
EFFORT_LEVELS: dict[str, tuple[str, ...]] = {
    "openai": ("low", "medium", "high"),
    "anthropic": ("low", "medium", "high", "xhigh", "max"),
}
# The pseudo provider ID that selects the offline simulated model.
SIMULATED = "simulated"
# The native command tool of this operating system; settings name the shell they permit.
SHELL_TOOL = "powershell" if os.name == "nt" else "bash"


class ModelPrice(Contract):
    """What one model costs, in US dollars per million tokens, as entered by the user.

    A cost is known only when both input and output prices are set. Unset cache prices fall
    back to the input price, which can only overstate a cost."""

    input: float | None = Field(default=None, ge=0, le=10_000)
    output: float | None = Field(default=None, ge=0, le=10_000)
    cache_read: float | None = Field(default=None, ge=0, le=10_000)
    cache_write: float | None = Field(default=None, ge=0, le=10_000)


def call_cost(usage: dict[str, Any], price: "ModelPrice | None", adapter: str) -> float | None:
    """Price one model call from its reported usage, or None when usage or price is unknown.

    The Messages API reports `input_tokens` without the cached part; Chat Completions counts
    cached tokens inside `input_tokens`, so they are subtracted before pricing the rest."""
    if price is None or price.input is None or price.output is None:
        return None
    inputs, output = usage.get("input_tokens"), usage.get("output_tokens")
    if inputs is None or output is None:
        return None
    read = usage.get("cache_read_input_tokens") or 0
    write = usage.get("cache_creation_input_tokens") or 0
    uncached = inputs if adapter == "anthropic" else max(0, inputs - read - write)
    read_price = price.input if price.cache_read is None else price.cache_read
    write_price = price.input if price.cache_write is None else price.cache_write
    total = uncached * price.input + read * read_price + write * write_price + output * price.output
    return round(total / 1_000_000, 6)


class ProviderConfig(Contract):
    """The resolved model endpoint and options a Run uses; credentials are referenced by name.

    `profile_id` names the configured provider this was resolved from (or `simulated`). Saved
    settings use it as the default model; Run snapshots keep the fully resolved values."""

    profile_id: str | None = None
    adapter: Literal["fake", "openai", "anthropic"] = "openai"
    model: str = ""
    base_url: str = "https://api.openai.com/v1"
    credential_ref: str = "OPENAI_API_KEY"
    reasoning_effort: ReasoningEffort | None = None
    # None leaves sampling to the provider; current Claude models reject the parameter.
    temperature: float | None = Field(default=None, ge=0, le=2)
    # Tool calls that write files need room; 2048 truncated ordinary edits mid-argument.
    max_output_tokens: int = Field(default=8192, ge=1, le=128_000)
    context_window: int | None = Field(default=None, ge=256)
    # Frozen with the Run so its cost stays reproducible after prices are edited.
    price: ModelPrice | None = None

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


class ProviderProfile(Contract):
    """A configured model endpoint: connection, key reference, known models and options."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    name: str = Field(min_length=1, max_length=60)
    adapter: Literal["openai", "anthropic"] = "openai"
    base_url: str = Field(min_length=1)
    credential_ref: str = ""
    models: list[str] = Field(default_factory=list, max_length=500)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int = Field(default=8192, ge=1, le=128_000)
    context_window: int | None = Field(default=None, ge=256)
    # Per-model prices keyed by model ID; a model without an entry has an unknown cost.
    prices: dict[str, ModelPrice] = Field(default_factory=dict)

    @field_validator("credential_ref")
    @classmethod
    def credential_name(cls, value: str) -> str:
        """Store the name of the key, never its value."""
        return credential_reference(value)

    @field_validator("base_url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        """Keep credentials and query strings out of the stored URL."""
        return endpoint_url(value)

    @field_validator("models")
    @classmethod
    def model_ids(cls, value: list[str]) -> list[str]:
        """Keep a sorted list of distinct, nonempty model IDs."""
        if any(not item.strip() or len(item) > 200 for item in value):
            raise ValueError("Model IDs must be nonempty and at most 200 characters.")
        return sorted(set(item.strip() for item in value))


class ModelChoice(Contract):
    """The model selected for a Run: a configured provider (or `simulated`) and options."""

    provider_id: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=200)
    reasoning_effort: ReasoningEffort | None = None


# Read only: no workspace changes. Ask: changes and commands need approval. Workspace write:
# the configured tool permissions apply unchanged.
WorkspaceAccess = Literal["read-only", "ask", "workspace-write"]


def default_providers() -> list[ProviderProfile]:
    """Seed one OpenAI-compatible endpoint that reads its key from OPENAI_API_KEY."""
    return [
        ProviderProfile(
            id="openai",
            name="OpenAI-compatible",
            base_url="https://api.openai.com/v1",
            credential_ref="OPENAI_API_KEY",
        )
    ]


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


class CommandRule(Contract):
    """A permission for shell commands that start with a prefix, ahead of the tool default.

    The longest matching prefix wins. Allow and ask rules apply only to simple commands, so a
    rule for `npm test` never covers `npm test; rm -rf data`; deny rules match any command
    that starts with the prefix."""

    prefix: str = Field(min_length=1, max_length=200)
    permission: Literal["allow", "ask", "deny"]

    @field_validator("prefix")
    @classmethod
    def trimmed(cls, value: str) -> str:
        """Compare prefixes as typed words, without surrounding whitespace."""
        if not value.strip():
            raise ValueError("Enter the command prefix.")
        return " ".join(value.split())


class Grant(Contract):
    """A user's standing approval for one tool in one conversation.

    For a shell tool it covers simple commands starting with `prefix`; otherwise any call of
    the tool. A grant turns an approval prompt into an allow; it never overrides a deny."""

    tool: str = Field(min_length=1, max_length=200)
    prefix: str | None = Field(default=None, max_length=200)
    created_at: str = Field(default_factory=lambda: now())


class AutoCompact(Contract):
    """Opt-in compaction after a successful Run once the conversation history grows large.

    It starts the same recorded compaction as the Context panel, with the Run's model, so the
    summary is inspectable and Restore full context undoes it. Off unless the user enables it."""

    enabled: bool = False
    # Compared with the byte-based estimate of the history the next Run would send.
    threshold_tokens: int = Field(default=100_000, ge=1_000, le=2_000_000)
    keep_recent_turns: int = Field(default=2, ge=1, le=10)


class StudioConfig(Contract):
    """Version provider, tool permissions, connections, and execution limits together."""

    revision: int = Field(default=0, ge=0)
    providers: list[ProviderProfile] = Field(default_factory=default_providers, max_length=20)
    # The default model; Runs may choose another and snapshot the resolved result.
    provider: ProviderConfig = Field(
        default_factory=lambda: ProviderConfig(profile_id="openai", temperature=None)
    )
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
            SHELL_TOOL: "allow",
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
    # Shell command prefixes with their own permission; see CommandRule.
    command_rules: list[CommandRule] = Field(default_factory=list, max_length=100)
    mcp_servers: list[MCPServer] = Field(default_factory=list, max_length=10)
    auto_compact: AutoCompact = Field(default_factory=AutoCompact)
    max_steps: int = Field(default=20, ge=1, le=100)
    max_tool_calls: int = Field(default=40, ge=1, le=200)
    tool_timeout: float = Field(default=60, ge=0.1, le=300)
    run_timeout: float = Field(default=600, ge=1, le=3600)

    @field_validator("providers")
    @classmethod
    def unique_providers(cls, value: list[ProviderProfile]) -> list[ProviderProfile]:
        """Provider IDs identify a Run's model choice, so they must be unique."""
        identifiers = [profile.id for profile in value]
        if len(set(identifiers)) != len(identifiers) or SIMULATED in identifiers:
            raise ValueError("Provider IDs must be unique and cannot be 'simulated'.")
        return value

    @field_validator("network_hosts")
    @classmethod
    def exact_hosts(cls, value: list[str]) -> list[str]:
        """Accept exact DNS hostnames only; wildcards and URL credentials are not policy."""
        import re

        for host in value:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host) or ".." in host:
                raise ValueError("Use lowercase exact hostnames without ports, URLs or wildcards.")
        return list(dict.fromkeys(value))


def resolve_model(config: StudioConfig, choice: ModelChoice | None = None) -> ProviderConfig:
    """Resolve a model choice (or the saved default) against the configured providers.

    The result carries everything a request needs, so a Run snapshot stays reproducible after
    provider settings change. A saved default without a profile is used as stored."""
    current = config.provider
    if choice is not None:
        provider_id, model, effort = choice.provider_id, choice.model, choice.reasoning_effort
    elif current.profile_id is not None:
        provider_id, model, effort = current.profile_id, current.model, current.reasoning_effort
    else:
        return current
    if provider_id == SIMULATED:
        return ProviderConfig(
            profile_id=SIMULATED,
            adapter="fake",
            model="simulated",
            base_url="",
            credential_ref="",
            max_output_tokens=current.max_output_tokens,
        )
    profile = next((item for item in config.providers if item.id == provider_id), None)
    if profile is None:
        raise AppError("provider_unknown", f"Provider {provider_id} is not configured.")
    if effort and effort not in EFFORT_LEVELS[profile.adapter]:
        raise AppError(
            "reasoning_effort", f"{profile.name} does not accept reasoning effort {effort}."
        )
    return ProviderConfig(
        profile_id=profile.id,
        adapter=profile.adapter,
        model=model,
        base_url=profile.base_url,
        credential_ref=profile.credential_ref,
        reasoning_effort=effort,
        temperature=profile.temperature,
        max_output_tokens=profile.max_output_tokens,
        context_window=profile.context_window,
        price=profile.prices.get(model),
    )


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
    # Provider content blocks to send back unchanged within the same Run, such as Messages
    # API thinking blocks with their signatures. Empty for most messages.
    blocks: list[dict[str, Any]] = Field(default_factory=list)


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
    # Per-Run choices; omitted values use the saved default model and workspace policy.
    model: ModelChoice | None = None
    access: WorkspaceAccess | None = None


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
    model: ModelChoice | None = None
    access: WorkspaceAccess | None = None


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
    # Bundled with the app, shared by every project (global), or in this project.
    scope: Literal["builtin", "global", "project"] = "project"


class WorkspaceSkills(Contract):
    """A skill catalog with the reasons any skill folders could not be loaded."""

    skills: list[SkillInfo]
    errors: dict[str, str]


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


class CredentialStatus(Contract):
    """Where a named key comes from, without its value; `hint` is the last four characters."""

    name: str
    source: Literal["stored", "environment", "missing"]
    hint: str | None = None


class CredentialSave(Contract):
    """A key entered in Settings; it is stored on the server and never returned."""

    value: str = Field(min_length=1, max_length=8192)


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

    # "approve_conversation" also records a Grant so later matching calls in the conversation
    # run without asking.
    decision: Literal["approve", "approve_conversation", "reject"]


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
    """Register a local folder as a conversation workspace, optionally creating it first."""

    workspace_root: str = Field(min_length=1, max_length=4096)
    create: bool = False


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
    # A replacement grant list; sending fewer grants revokes the missing ones.
    grants: list[Grant] | None = None

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
    model: ModelChoice | None = None


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
    # "auto" when started by the opt-in AutoCompact setting rather than the Context panel.
    trigger: Literal["manual", "auto"] = "manual"


class ContextUsage(Contract):
    """Provider-reported accounting for the conversation's most recent model request.

    Each request resends the whole conversation prefix, so the latest one reports the live
    context size. `prompt_tokens` is the total sent (cached and uncached); `cache_read_tokens`
    is the part served from the provider's prompt cache. Fields are null when unreported."""

    model: str | None = None
    prompt_tokens: int | None = None
    input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None


class ContextView(Contract):
    """Estimated history size and explicit summary boundaries, separate from actual model usage."""

    session_id: str
    raw_messages: int
    effective_messages: int
    raw_estimated_tokens: int
    effective_estimated_tokens: int
    context_window: int | None
    reserved_output_tokens: int
    live_usage: ContextUsage | None
    # Sum of priced Runs in US dollars; Runs without a price or usage are counted separately.
    spent: float = 0
    unpriced_runs: int = 0
    active_compaction_id: str | None
    compacting_id: str | None
    compactions: list[CompactionView]


EvalCheckKind = Literal["succeeds", "contains", "not_contains", "equals", "regex"]


class EvalCheck(Contract):
    """How one eval case decides a Run passed, from its final assistant text.

    `succeeds` only needs the Run to finish; the others match the text. Matching is a plain
    byte check, not a tokenizer or a model judge, so results are deterministic and offline."""

    kind: EvalCheckKind = "contains"
    value: str = Field(default="", max_length=4000)
    ignore_case: bool = True

    @model_validator(mode="after")
    def check_is_usable(self) -> "EvalCheck":
        """A text check needs a value, and a regex must compile, so saving catches mistakes."""
        if self.kind != "succeeds" and not self.value.strip():
            raise ValueError("Enter the text or pattern this check should look for.")
        if self.kind == "regex":
            try:
                re.compile(self.value)
            except re.error as error:
                raise ValueError(f"Invalid regular expression: {error}") from error
        return self


class EvalCase(Contract):
    """One task the compared prompts each run, with the condition that marks it passed."""

    id: str = Field(default_factory=uid)
    name: str = Field(min_length=1, max_length=120)
    input: str = Field(min_length=1, max_length=100_000)
    variables: dict[str, Any] = Field(default_factory=dict)
    check: EvalCheck = Field(default_factory=EvalCheck)


class EvalVariant(Contract):
    """One prompt under comparison: a preset, optionally with its own model and access."""

    id: str = Field(default_factory=uid)
    name: str = Field(min_length=1, max_length=120)
    preset_id: str = Field(min_length=1, max_length=64)
    model: ModelChoice | None = None
    access: WorkspaceAccess | None = None


class EvalDraft(Contract):
    """The editable definition of an evaluation: the cases and the prompts to compare."""

    name: str = Field(min_length=1, max_length=80)
    cases: list[EvalCase] = Field(min_length=1, max_length=50)
    variants: list[EvalVariant] = Field(min_length=1, max_length=10)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        """Keep evaluation names meaningful for the list and the comparison header."""
        if not value.strip():
            raise ValueError("Enter an evaluation name.")
        return value.strip()

    @model_validator(mode="after")
    def unique_members(self) -> "EvalDraft":
        """Cases and variants are matched by id in results, so ids and names must be distinct."""
        for label, items in (("case", self.cases), ("variant", self.variants)):
            ids = [item.id for item in items]
            names = [item.name.strip().lower() for item in items]
            if len(set(ids)) != len(ids):
                raise ValueError(f"Each {label} needs a distinct id.")
            if len(set(names)) != len(names):
                raise ValueError(f"Each {label} needs a distinct name.")
        return self


class EvalUpdate(EvalDraft):
    """A revision-checked replacement of an existing evaluation definition."""

    expected_revision: int = Field(ge=0)


class Eval(Contract):
    """A stored evaluation definition with its own revision, separate from Run snapshots."""

    id: str
    name: str
    revision: int
    created_at: str
    cases: list[EvalCase]
    variants: list[EvalVariant]


class EvalSummary(Contract):
    """Evaluation metadata plus the status of its most recent comparison, for the list."""

    id: str
    name: str
    revision: int
    created_at: str
    case_count: int
    variant_count: int
    last_batch_id: str | None = None
    last_status: str | None = None
    last_run_at: str | None = None


class EvalLabel(Contract):
    """A stable id and display name for a case or variant referenced in results."""

    id: str
    name: str


class EvalItemResult(Contract):
    """One case run under one variant: its Run, grade, output snippet and recorded usage."""

    case_id: str
    variant_id: str
    run_id: str | None = None
    run_session_id: str | None = None
    status: str
    passed: bool | None = None
    detail: str = ""
    output: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    active_seconds: float | None = None
    cost: float | None = None


class VariantSummary(Contract):
    """Aggregate effectiveness of one compared prompt across the evaluation's cases."""

    variant_id: str
    name: str
    graded: int
    passed: int
    pass_rate: float | None = None
    avg_input_tokens: float | None = None
    avg_output_tokens: float | None = None
    avg_active_seconds: float | None = None
    avg_cost: float | None = None


class EvalBatchView(Contract):
    """One comparison run: its progress, per-variant aggregates and every case/variant result."""

    id: str
    eval_id: str
    eval_name: str
    status: str
    created_at: str
    completed: int
    total: int
    error: str | None = None
    cases: list[EvalLabel]
    variants: list[VariantSummary]
    items: list[EvalItemResult]


class SessionSummary(Contract):
    """Conversation metadata for navigation, without the committed message history."""

    id: str
    title: str
    revision: int
    active_run_id: str | None
    compaction_id: str | None = None
    compacting_id: str | None = None
    created_at: str
    folder_id: str | None = None
    project_id: str | None = None
    archived: bool = False
    copied_from_session_id: str | None = None
    grants: list[Grant] = Field(default_factory=list)
    message_count: int


class SessionView(SessionSummary):
    """Conversation history containing only committed successful turns."""

    messages: list[Message]


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
    provider_id: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    access: str | None = None
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
