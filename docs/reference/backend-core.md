# Backend contracts, configuration, compilation, and policy

This is the computation and contract center of the backend. The domain owns data shapes; the compiler owns deterministic message assembly; policy owns authorization decisions. Process configuration and evidence redaction sit at infrastructure boundaries. None of these modules starts an agent on import.

[Reference index](README.md) · [System architecture](../architecture.md)

## src/rtagent/domain.py

**Layer: Domain / shared contracts.** Defines validated values shared by HTTP, application, persistence, the compiler, and the runtime. It uses Pydantic without depending on FastAPI, SQLite, MCP, or model SDKs.

**Connections and effects:** Constructed by adapters and use cases; serialized into database JSON and OpenAPI. `StudioConfig` chooses an OS-specific default command name from `os.name`; this is a default, not sandbox enforcement. Functions perform computation/validation only.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`uid`](../../src/rtagent/domain.py#L14) | Return a new stable string ID. | Encode a UUID4 as 32 hexadecimal characters. | Independent identifiers avoid database allocation and make URLs, copied conversations, and snapshots refer to fixed identities. |
| [`now`](../../src/rtagent/domain.py#L19) | Return a storage/event timestamp string. | Format an aware `datetime.now(UTC)` as ISO 8601. | One UTC representation avoids timezone ambiguity; the browser formats it locally. |
| [`digest`](../../src/rtagent/domain.py#L24) | Return a SHA-256 fingerprint of JSON-compatible data. | Serialize with sorted keys, compact separators, and Unicode preserved, then hash UTF-8 bytes. | Dictionary insertion order must not change configuration/input identity. This hashes canonical JSON, so a string hash differs from a raw file-byte hash. |
| [`credential_reference`](../../src/rtagent/domain.py#L30) | Validate an optional environment variable name. | Accept empty or an ASCII Python identifier; reject literal key syntax. | Serializable settings retain references rather than normal API keys. Identifier-shaped secrets are not distinguishable automatically, so this is format validation, not secret detection. |
| [`endpoint_url`](../../src/rtagent/domain.py#L37) | Validate an optional HTTP(S) endpoint. | Parse the URL and require a hostname; reject user information, queries, and fragments. | Provider/MCP settings can be stored and displayed without embedded URL credentials. |
| [`AppError.__init__`](../../src/rtagent/domain.py#L58) | Construct an expected failure with code, safe message, HTTP status, and optional details. | Initialize `Exception` and retain the four public error attributes; default status is 422. | Domain/application errors stay transport independent; `main.error_response` performs HTTP translation. |
| [`ProviderConfig.credential_name`](../../src/rtagent/domain.py#L116) | Validate `credential_ref` during Pydantic construction. | Delegate to `credential_reference`. | The provider contract prevents normal literal API keys from entering configuration snapshots. |
| [`ProviderConfig.safe_url`](../../src/rtagent/domain.py#L122) | Validate the serialized provider `base_url`. | Delegate to `endpoint_url`. | Central validation covers Settings API edits as well as snapshot reconstruction. |
| [`MCPServer.credential_name`](../../src/rtagent/domain.py#L142) | Validate the optional MCP bearer reference. | Delegate to the same reference validator as providers. | MCP authentication follows the same server-side secret boundary. |
| [`MCPServer.safe_url`](../../src/rtagent/domain.py#L148) | Validate an optional MCP endpoint independently of transport. | Delegate to `endpoint_url`; an empty value is legal for stdio. | A single connection contract supports both transports without putting their lifecycle in the domain. |
| [`MCPServer.environment_references`](../../src/rtagent/domain.py#L154) | Validate child-variable to server-variable mappings. | Require both sides to be nonempty ASCII identifiers and return the mapping. | Stdio environment values describe references, and only the transport adapter resolves their actual values. |
| [`FolderCreate.nonempty_name`](../../src/rtagent/domain.py#L434) | Normalize a folder display name. | Reject whitespace-only text and strip surrounding whitespace after length validation. | Flat folder names remain meaningful and comparable rather than becoming invisible navigation entries. |
| [`TaskPlan.one_active_step`](../../src/rtagent/domain.py#L239) | Validate a coherent bounded plan. | Reject whitespace-only step labels and more than one in_progress item after field validation. | Agent progress has a clear current step; a completed label remains a model claim rather than proof that a check passed. |
| [`latest_plan`](../../src/rtagent/domain.py#L248) | Recover the latest valid recorded agent plan. | Collect update_plan call IDs from assistant messages, scan paired tool messages in reverse, validate TaskPlan JSON, and return the latest valid value or null. | Arbitrary JSON from other tools cannot impersonate progress; refresh, restart and copied evidence share the same immutable-message projection. |
| [`SessionUpdate.nonempty_title`](../../src/rtagent/domain.py#L473) | Normalize an explicitly supplied conversation title. | Preserve `None`, reject whitespace-only values, and strip valid text. | Optional metadata patches distinguish no rename from an intentional visible title. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`AppError`](../../src/rtagent/domain.py#L55) | Expected business/validation failure. | Exception attributes carry public details; internal causes are separate. | Error vocabulary is shared without making the domain depend on HTTP responses. |
| [`Contract`](../../src/rtagent/domain.py#L64) | Base for first-party serialized contracts. | `extra=forbid` rejects unknown fields; serialization defaults are marked required in JSON Schema. | Unexpected input is caught early and generated frontend output types reflect defaulted fields. |
| [`Variable`](../../src/rtagent/domain.py#L70) | Declaration for one template variable. | String/number/boolean type, required flag, and optional default. | Explicit declarations limit substitution and expose missing/type-invalid values. |
| [`Block`](../../src/rtagent/domain.py#L78) | One configured prompt message. | Stable ID, name, role restricted to system/user/assistant, literal template, enabled flag, placement/order, variable declarations, filters, and structured condition. | Message behavior is inspectable data; runtime-created tool messages cannot be forged as preset blocks. |
| [`Preset`](../../src/rtagent/domain.py#L94) | Editable collection of embedded blocks. | ID/name/revision plus at most 100 blocks; repository writes advance its revision. | Blocks belong to their preset aggregate, avoiding independent block repositories and synchronization machinery. |
| [`ProviderConfig`](../../src/rtagent/domain.py#L103) | Provider options saved in configuration and Runs. | Adapter, model, Base URL, credential reference, temperature, output reservation, and optional context window. | Requests are reproducible from non-secret options while actual credentials are resolved at execution. |
| [`MCPServer`](../../src/rtagent/domain.py#L127) | An explicitly configured external tool connection. | ID/name, stdio executable/argument array/cwd/environment references, or HTTP URL/bearer reference. | Connection permission remains local configuration; the model cannot add servers. |
| [`ToolDefinition`](../../src/rtagent/domain.py#L164) | Frozen tool interface. | Namespaced name, description, JSON Schema, optional server/original name, and schema hash. | A Run can validate and dispatch the exact tool contract it advertised, even after discovery changes. |
| [`StudioConfig`](../../src/rtagent/domain.py#L175) | One versioned execution-settings aggregate. | Provider, allow/ask/deny map, sandbox mode, at most 10 MCP servers, model/tool attempt ceilings, tool deadline, and active Run budget. | Configuration changes have one revision and cannot partially alter a running snapshot. |
| [`ToolCall`](../../src/rtagent/domain.py#L207) | Model invocation intent. | Original ID, exposed tool name, and decoded argument object. | The same model ID must be used by the resulting tool message; the receipt ID is separate. |
| [`Message`](../../src/rtagent/domain.py#L215) | Canonical model conversation item. | Explicit role/content/source, assistant tool calls, and tool-result `tool_call_id`. | SDK-independent messages retain ordering, provenance, and call/result pairing across compilation and storage. |
| [`CompiledPrompt`](../../src/rtagent/domain.py#L267) | Pure compiler result. | Final messages, activation report, source map, estimate/budget status, and input hash. | Inspection and execution share the same evidence rather than two prompt renderers. |
| [`PreviewRequest`](../../src/rtagent/domain.py#L278) | Direct task submission or optional inspection request. | Session/config/preset revision references, nonempty input, variables, and at most 20 explicitly selected skills. | The application can freeze actual inputs without requiring a separate Preview UI step. |
| [`DraftPreviewRequest`](../../src/rtagent/domain.py#L291) | Nonpersisting editor inspection input. | Inline Preset, saved settings revision, optional session/project references, possibly empty task, variables and selected skills. | Unsaved composition can be inspected without saving presets, creating history, or acquiring an execution identity. |
| [`PromptInspection`](../../src/rtagent/domain.py#L364) | Compiled draft projection. | Hash, canonical compiler result, prepared body, and selected schemas. | Inspection preserves the last valid result in the UI but is not an immutable or consumable Run snapshot. |
| [`MemoryDocument`](../../src/rtagent/domain.py#L304) | Last-read workspace note. | Relative path, content, and canonical text hash. | The editor and model tool can detect a stale replacement without a memory database. |
| [`MemorySave`](../../src/rtagent/domain.py#L312) | Explicit memory replacement command. | Bounded content and mandatory expected hash. | An edit acknowledges the exact prior document instead of silently overwriting another Run's notes. |
| [`SkillInfo`](../../src/rtagent/domain.py#L319) | Catalog metadata for a discovered procedure. | Name/description/path, built-in flag, and source hash; no body. | Default model context stays small and the UI can select/inspect a source. |
| [`SkillDocument`](../../src/rtagent/domain.py#L329) | Loaded procedure source. | Inherits metadata and adds full SKILL.md text. | Full instructions enter context only through selection or load_skill; provenance stays attached. |
| [`SkillSave`](../../src/rtagent/domain.py#L335) | Skill authoring command. | Name, short description, Markdown instructions, optional expected hash. | The file adapter owns frontmatter serialization and protected updates, rather than accepting arbitrary filesystem destinations. |
| [`SandboxStatus`](../../src/rtagent/domain.py#L344) | OS isolation probe result. | Backend label, readiness, network label, and diagnostic detail. | UI can report a real failed sandbox without making app startup or model configuration unavailable. |
| [`WorkspaceView`](../../src/rtagent/domain.py#L353) | Editable project context projection. | Root, actual sandbox status, selected root instruction document/hash/path, memory, Skill metadata and invalid-source diagnostics. | Prompt composition and workspace editing expose identical sources without a second configuration store. |
| [`Preview`](../../src/rtagent/domain.py#L373) | Expiring frozen first input. | Extends PromptInspection with a saved ID and expiry. | API clients may consume this immutable record; browser live drafts instead use side-effect-free inspection and direct Run. |
| [`StartRun`](../../src/rtagent/domain.py#L380) | Saved-preview consumption command. | Preview ID/hash and observed session revision only. | Clients cannot replace the frozen tools, settings, or prompt at execution time. |
| [`ApprovalDecision`](../../src/rtagent/domain.py#L388) | Human decision for one pending tool call. | Literal approve/reject value; approval ID comes from the URL. | A model cannot approve a call through its generated text. |
| [`RecoveryDecision`](../../src/rtagent/domain.py#L394) | User reconciliation of one uncertain effect. | Explicit retry or record action plus a result string. | External effects are not treated as exactly-once; callers choose whether repetition is acceptable. |
| [`ResumeRequest`](../../src/rtagent/domain.py#L401) | Reconciliation set for a paused Run. | Mapping from application receipt IDs to RecoveryDecision. | The application requires every unknown receipt to be resolved before graph recovery. |
| [`RunEvent`](../../src/rtagent/domain.py#L407) | Persisted browser event envelope. | Schema version, Run ID, sequence, event type, UTC timestamp, and payload. | Reconnection has a durable cursor rather than an in-memory streaming offset. |
| [`FolderView`](../../src/rtagent/domain.py#L418) | Stored flat folder projection. | ID/name/revision/creation timestamp. | Folders organize conversations, not workspace filesystem paths. |
| [`FolderCreate`](../../src/rtagent/domain.py#L427) | Folder creation input. | Bounded nonempty name plus normalization validator. | A minimal contract avoids nested trees or separate folder settings. |
| [`FolderUpdate`](../../src/rtagent/domain.py#L441) | Version-bound folder rename. | Inherits creation name and adds nonnegative expected revision. | Optimistic concurrency rejects edits based on stale sidebar state. |
| [`ProjectCreate`](../../src/rtagent/domain.py#L447) | Register a local workspace folder. | Inherit nonempty display-name validation and accept an absolute folder string checked by Studio. | Registration is a trusted user action; the model cannot alter its root through tool arguments. |
| [`ProjectView`](../../src/rtagent/domain.py#L453) | Stored named folder projection. | Stable ID, name, canonical workspace_root and creation timestamp. | Root selection is separate from chat-organization folders and the one shared permission/config aggregate. |
| [`SessionUpdate`](../../src/rtagent/domain.py#L462) | Partial conversation metadata command. | Expected revision plus optional title/archive/folder/project; fields_set distinguishes omission from explicit null. | Idle workspace moves affect future Runs while saved snapshots retain their original roots. |
| [`SessionCopy`](../../src/rtagent/domain.py#L480) | Duplicate/fork command. | Revision, operation, and optional successful Run boundary. | One evidence-copy use case covers both operations without replay or checkpoint cloning. |
| [`SessionView`](../../src/rtagent/domain.py#L488) | Committed conversation projection. | History, active Run, metadata/provenance, and nullable project membership. | Null retains the default root; a project binds future turns to its saved folder. |
| [`PlanItem`](../../src/rtagent/domain.py#L225) | One explicit task-progress item. | A nonempty bounded step label and pending/in_progress/completed status. | Small validated values support readable task progress without database-owned jobs. |
| [`TaskPlan`](../../src/rtagent/domain.py#L232) | A complete progress receipt. | Optional bounded explanation plus one to twelve PlanItems with at most one active item. | One update replaces the displayed plan; it cannot schedule work or grant permissions. |
| [`RunView`](../../src/rtagent/domain.py#L503) | Public execution projection. | Status/messages/approval/unknown receipts/usage, immutable workspace root and nullable validated plan derived from paired tool messages. | UI can inspect historical roots and durable progress without mutating execution settings or inventing progress state. |
| [`CallView`](../../src/rtagent/domain.py#L527) | One durable model/tool receipt. | Application call ID, Run ID, kind/status, structured evidence, and optional result. | A receipt survives a graph-node replay and separately identifies uncertain external outcomes. |
| [`TraceView`](../../src/rtagent/domain.py#L538) | Labeled collection of receipts. | Schema version, explicit redacted flag, and CallView list. | Sanitized evidence cannot be mistaken for an exact original request body. |

### Contract field definitions

Field declarations below are extracted from the reviewed source. `Field(...)` bounds apply during validation; method-specific checks such as condition grammar and file hashes occur later. The contracts are data shapes, not database tables with identical columns. `ConfigDict` is base-model behavior, not a user-supplied field.

**Variable**

```python
type: Literal['string', 'number', 'boolean'] = 'string'
required: bool = True
default: Any = None
```

**Block**

```python
id: str = Field(default_factory=uid)
name: str = Field(min_length=1, max_length=120)
role: Literal['system', 'user', 'assistant'] = 'system'
template: str = Field(max_length=100000)
enabled: bool = True
placement: Literal['before_history', 'before_input'] = 'before_history'
order: int = Field(ge=0)
variables: dict[str, Variable] = Field(default_factory=dict)
provider_allowlist: list[str] | None = None
model_allowlist: list[str] | None = None
condition: dict[str, Any] | None = None
```

**Preset**

```python
id: str = Field(default_factory=uid)
name: str = Field(min_length=1, max_length=120)
revision: int = Field(default=0, ge=0)
blocks: list[Block] = Field(default_factory=list, max_length=100)
```

**ProviderConfig**

```python
adapter: Literal['fake', 'openai'] = 'openai'
model: str = ''
base_url: str = 'https://api.openai.com/v1'
credential_ref: str = 'OPENAI_API_KEY'
temperature: float = Field(default=0.2, ge=0, le=2)
max_output_tokens: int = Field(default=2048, ge=1, le=32768)
context_window: int | None = Field(default=None, ge=256)
```

**MCPServer**

```python
id: str = Field(default_factory=uid)
name: str = Field(min_length=1, max_length=60)
transport: Literal['stdio', 'streamable_http'] = 'stdio'
command: str = ''
args: list[str] = Field(default_factory=list)
cwd: str | None = None
url: str = ''
env: dict[str, str] = Field(default_factory=dict)
credential_ref: str = ''
```

**ToolDefinition**

```python
name: str
description: str
input_schema: dict[str, Any]
server_id: str | None = None
original_name: str | None = None
schema_hash: str = ''
```

**StudioConfig**

```python
revision: int = Field(default=0, ge=0)
provider: ProviderConfig = Field(default_factory=ProviderConfig)
tools: dict[str, Literal['allow', 'ask', 'deny']] = Field(default_factory=lambda: {'read_text': 'allow', 'list_files': 'allow', 'search_files': 'allow', 'search_text': 'allow', 'read_instructions': 'allow', 'write_text': 'allow', 'edit_text': 'allow', 'apply_patch': 'allow', 'update_plan': 'allow', 'powershell' if os.name == 'nt' else 'bash': 'allow', 'read_memory': 'allow', 'save_memory': 'allow', 'load_skill': 'allow', 'save_skill': 'allow', 'echo': 'ask'})
sandbox_mode: Literal['workspace-write', 'read-only'] = 'workspace-write'
mcp_servers: list[MCPServer] = Field(default_factory=list, max_length=10)
max_steps: int = Field(default=20, ge=1, le=100)
max_tool_calls: int = Field(default=40, ge=1, le=200)
tool_timeout: float = Field(default=60, ge=0.1, le=300)
run_timeout: float = Field(default=600, ge=1, le=3600)
```

**ToolCall**

```python
id: str
name: str
args: dict[str, Any]
```

**Message**

```python
role: Literal['system', 'user', 'assistant', 'tool']
content: str = ''
source: str = 'runtime'
tool_calls: list[ToolCall] = Field(default_factory=list)
tool_call_id: str | None = None
```

**PlanItem**

```python
step: str = Field(min_length=1, max_length=240)
status: Literal['pending', 'in_progress', 'completed']
```

**TaskPlan**

```python
explanation: str = Field(default='', max_length=1000)
plan: list[PlanItem] = Field(min_length=1, max_length=12)
```

**CompiledPrompt**

```python
messages: list[Message]
activation_report: list[dict[str, Any]]
source_map: list[dict[str, Any]]
token_estimate: int
budget_status: str
input_hash: str
```

**PreviewRequest**

```python
session_id: str
expected_session_revision: int
preset_id: str
preset_revision: int
config_revision: int
input: str = Field(min_length=1, max_length=100000)
variables: dict[str, Any] = Field(default_factory=dict)
skills: list[str] = Field(default_factory=list, max_length=20)
```

**DraftPreviewRequest**

```python
preset: Preset
config_revision: int
session_id: str | None = None
project_id: str | None = None
expected_session_revision: int | None = None
input: str = Field(default='', max_length=100000)
variables: dict[str, Any] = Field(default_factory=dict)
skills: list[str] = Field(default_factory=list, max_length=20)
```

**MemoryDocument**

```python
path: str
content: str
hash: str
```

**MemorySave**

```python
content: str = Field(max_length=65536)
expected_hash: str
```

**SkillInfo**

```python
name: str
description: str
path: str
builtin: bool
hash: str
```

**SkillDocument**

```python
content: str
```

**SkillSave**

```python
name: str = Field(min_length=1, max_length=64)
description: str = Field(min_length=1, max_length=1000)
instructions: str = Field(min_length=1, max_length=65536)
expected_hash: str | None = None
```

**SandboxStatus**

```python
backend: str
ready: bool
network: str
detail: str
```

**WorkspaceView**

```python
root: str
sandbox: SandboxStatus
instructions: MemoryDocument | None
memory: MemoryDocument
skills: list[SkillInfo]
skill_errors: dict[str, str]
```

**PromptInspection**

```python
input_hash: str
compiled: CompiledPrompt
prepared_request: dict[str, Any]
tools: list[ToolDefinition]
```

**Preview**

```python
id: str
expires_at: str
```

**StartRun**

```python
preview_id: str
input_hash: str
expected_session_revision: int
```

**ApprovalDecision**

```python
decision: Literal['approve', 'reject']
```

**RecoveryDecision**

```python
action: Literal['retry', 'record']
result: str = ''
```

**ResumeRequest**

```python
resolutions: dict[str, RecoveryDecision] = Field(default_factory=dict)
```

**RunEvent**

```python
schema_version: int = 1
run_id: str
seq: int
type: str
timestamp: str
payload: dict[str, Any]
```

**FolderView**

```python
id: str
name: str
revision: int
created_at: str
```

**FolderCreate**

```python
name: str = Field(min_length=1, max_length=120)
```

**FolderUpdate**

```python
expected_revision: int = Field(ge=0)
```

**ProjectCreate**

```python
workspace_root: str = Field(min_length=1, max_length=4096)
```

**ProjectView**

```python
id: str
name: str
workspace_root: str
created_at: str
```

**SessionUpdate**

```python
expected_revision: int = Field(ge=0)
title: str | None = Field(default=None, min_length=1, max_length=120)
archived: bool | None = None
folder_id: str | None = None
project_id: str | None = None
```

**SessionCopy**

```python
expected_revision: int = Field(ge=0)
operation: Literal['duplicate', 'fork']
through_run_id: str | None = None
```

**SessionView**

```python
id: str
title: str
revision: int
active_run_id: str | None
messages: list[Message]
created_at: str
folder_id: str | None = None
project_id: str | None = None
archived: bool = False
copied_from_session_id: str | None = None
```

**RunView**

```python
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
```

**CallView**

```python
id: str
run_id: str
kind: str
status: str
data: dict[str, Any]
result: Any
```

**TraceView**

```python
schema_version: int = 1
redacted: bool
calls: list[CallView]
```

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def uid() -> str
def now() -> str
def digest(value: Any) -> str
def credential_reference(value: str) -> str
def endpoint_url(value: str) -> str
def AppError.__init__(self, code: str, message: str, status: int=422, details: Any=None)
def ProviderConfig.credential_name(cls, value: str) -> str
def ProviderConfig.safe_url(cls, value: str) -> str
def MCPServer.credential_name(cls, value: str) -> str
def MCPServer.safe_url(cls, value: str) -> str
def MCPServer.environment_references(cls, value: dict[str, str]) -> dict[str, str]
def TaskPlan.one_active_step(self) -> 'TaskPlan'
def latest_plan(messages: list[Message]) -> TaskPlan | None
def FolderCreate.nonempty_name(cls, value: str) -> str
def SessionUpdate.nonempty_title(cls, value: str | None) -> str | None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/config.py

**Layer: Composition / infrastructure configuration.** Separates process paths and environment overrides from the versioned execution configuration.

**Connections and effects:** Called by main lifespan, Alembic, providers, and MCP transport. Reads environment/.env only when constructing Settings or resolving a secret; initialize creates the data directory. No browser receives resolved credentials.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`resolve_secret`](../../src/rtagent/config.py#L14) | Resolve a credential reference to an in-memory string. | Validate the name, consult process environment first, then read local .env, otherwise return empty. | Late resolution keeps secret values out of Run/config JSON. Empty references permit keyless local endpoints; existing snapshots bind a reference, not a historical secret value. |
| [`Settings.initialize`](../../src/rtagent/config.py#L42) | Normalize process directories before resource creation. | Resolve workspace/data paths and create the data directory if missing. | Absolute stable paths support Windows URLs and prevent later cwd changes from altering workspace identity. |
| [`Settings.apply_provider`](../../src/rtagent/config.py#L48) | Apply explicit startup overrides and Windows shell migration. | Copy config data, transfer old bash permission to powershell without overriding an explicit powershell rule, apply non-None provider/URL/model values, and revalidate. | Only current settings change; old Run snapshots are untouched. Explicit endpoint/model selects the real adapter while later UI edits remain effective until restart. |
| [`Settings.database_url`](../../src/rtagent/config.py#L69) | Compute the application's async database URL property. | Join resolved data directory with app.db and use POSIX path spelling after sqlite+aiosqlite:/// . | SQLAlchemy receives an absolute URL compatible with Windows paths, without coupling domain settings to file locations. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`Settings`](../../src/rtagent/config.py#L23) | Process-level configuration, not a Run aggregate. | BaseSettings reads STUDIO_ fields and OPENAI aliases from environment/.env; contains data/workspace paths, optional provider overrides, and browser origin list. | Resource ownership and startup policy stay outside persisted editable model/tool settings. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def resolve_secret(reference: str) -> str
def Settings.initialize(self) -> None
def Settings.apply_provider(self, config: StudioConfig) -> StudioConfig
def Settings.database_url(self) -> str
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/prompts.py

**Layer: Domain computation / prompt compilation.** Turns frozen preset/config/history/workspace data into one deterministic canonical sequence.

**Connections and effects:** Studio._prepare (inspection and execution) and Runner.model_node call the same compiler. Only domain contracts and Python computation are imported. No repository reads, model requests, file reads, or secret resolution occur here.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`default_preset`](../../src/rtagent/prompts.py#L15) | Build the visible Security analysis starter. | Create seven ordered system blocks: identity, boundaries, practical work, native tools, memory/Skills, reporting, and task focus. | Operational instructions are editable saved configuration; none is an invisible extra runtime prompt. |
| [`builtin_presets`](../../src/rtagent/prompts.py#L117) | Construct five independent practical presets. | Deep-copy the common six blocks and append a distinct task-focus block for General agent, Coding, Debugging, or Code review; retain Security analysis as the original default ID. | Behavioral focus is easy to switch and edit without separate agent classes or hidden permission profiles. |
| [`is_legacy_default`](../../src/rtagent/prompts.py#L177) | Recognize only unchanged historical starter text. | Compare the original three-block seed structurally, or compare the normalized six-block Stage 2 fingerprint excluding revision. | Startup can improve the known seed while retaining every user modification and historical Run snapshot. |
| [`condition_value`](../../src/rtagent/prompts.py#L214) | Evaluate a structured condition to a boolean or an explainable AppError. | Recursively evaluate all/any/not to depth 12; permit eq/in/exists on provider/model/task_type/tools or one-level variables.NAME. Validate shapes and missing comparison fields before evaluation. | A restricted data grammar avoids eval/template code execution. Missing fields and malformed operators must not silently disable instructions; all/any evaluate their whole list, so invalid sibling conditions remain visible. |
| [`compile_prompt`](../../src/rtagent/prompts.py#L258) | Produce CompiledPrompt from frozen inputs, with optional Run delta/context. | Check unique IDs and per-placement orders, evaluate filters/conditions, substitute declared literals, construct the six sequence segments, estimate serialized bytes/4, enforce output reservation, and hash all inputs. | This is the sole renderer for Preview and every model step. Current task and Run delta are inserted once; activation reasons/source indices explain included and excluded blocks, and overflow is explicit rather than silently trimmed. |
| [`compile_prompt.replace`](../../src/rtagent/prompts.py#L312) | Resolve one template regex match against the current block. | Find its declaration, use supplied value or default, enforce required/type rules, and return text or JSON scalar spelling. Reject undeclared/missing/wrong-type values. | A single regex pass does not recursively interpret text supplied by the user, preserving literal data and eliminating arbitrary template execution. |

### Compilation algorithm and failure boundaries

The actual sequence is `before_history blocks → frozen workspace context → successful conversation history → before_input blocks → current user input → current Run delta`. Blocks sort by `(placement, order)` during validation and are collected into named slots before assembly; final slot order is explicit, so lexical sorting does not decide history placement. Orders may repeat across different placements; IDs may not repeat anywhere. A disabled block's supplied condition is still validated/evaluated before exclusion is recorded.

Only `{{ identifier }}` placeholders are legal. Unrecognized braces fail; defaults do not bypass declared types. Number validation deliberately excludes booleans (`type(value)` rather than `isinstance(value, int)`). Sources use block IDs, `current_input`, workspace/skill origins, and runtime/history labels. Estimates are heuristics, not provider usage. An unset context window yields `unverified`; a configured window must contain estimate plus output reservation. The SHA-256 includes compiler version 1, preset, settings, canonical messages, and exposed schemas. It is reproducibility evidence, not encryption.

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def default_preset(revision: int=1) -> Preset
def builtin_presets() -> list[Preset]
def is_legacy_default(preset: Preset) -> bool
def condition_value(condition: dict[str, Any], context: dict[str, Any], depth: int=0) -> bool
def compile_prompt(preset: Preset, config: StudioConfig, history: list[Message], user_input: str, variables: dict[str, Any], tools: list[dict[str, Any]], delta: list[Message] | None=None, workspace_context: list[Message] | None=None) -> CompiledPrompt
def compile_prompt.replace(match: re.Match[str], current=block) -> str
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/policies.py

**Layer: Domain policy.** Decides whether one model-generated call is allowed, denied, or needs human approval.

**Connections and effects:** Runner.tool_node supplies the frozen config and a definition found in that Run's frozen schemas. JSON Schema validation is pure local computation. ToolRegistry additionally enforces read-only exposure/dispatch and MCP schema freshness.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`decide`](../../src/rtagent/policies.py#L8) | Return allow/ask/deny for one call, or reject invalid arguments. | Deny absent definitions or absent permission keys; validate arguments with Draft202012Validator; return the configured permission. | Prompt text and MCP annotations cannot authorize execution. The runtime handles ask as a durable human interrupt, while native/MCP adapters enforce their actual execution boundary. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def decide(call: ToolCall, definition: ToolDefinition | None, config: StudioConfig) -> str
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/tracing.py

**Layer: Evidence presentation boundary.** Creates a sanitized copy for default trace views, events, recovery projections, and exports.

**Connections and effects:** Called by API and Studio.view after reading stored evidence. Does not update database records. Pattern masking is deliberately finite; it is not a guarantee of detecting arbitrary secrets embedded in prose.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`redact`](../../src/rtagent/tracing.py#L7) | Return a recursive masked view of a JSON-like value. | Walk dictionaries/lists, mask api_key/password/authorization/access_token keys, replace supplied secrets of length at least four, and mask sk- tokens and Bearer strings. | Original local receipts remain available for inspection while outward copies are labeled redacted. API callers currently use pattern/key masking without supplying a resolved-secret list, so arbitrary credential formats are not universally recognized. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def redact(value: Any, secrets: list[str] | None=None) -> Any
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/patches.py

**Layer: Domain computation / exact text changes.** Parses the small Add/Update/Delete patch language and computes new text without filesystem access.

**Connections and effects:** Workspace owns path validation, hashes, bounded diffs and file effects; this module imports only the shared AppError contract and standard-library data types.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`parse_patch`](../../src/rtagent/patches.py#L18) | Parse one bounded workspace patch. | Require Begin/End framing, recognized nonempty unique file headers, plus-prefixed Add lines, no Delete body, bounded Update hunks and at most twenty files; produce immutable PatchOperations. | Syntax is checked before any file effect, and unsupported move/fuzzy features fail explicitly. |
| [`apply_hunks`](../../src/rtagent/patches.py#L65) | Compute exact ordered updates from original text. | Track original raw line endings and normalized context, flush each hunk in forward order, preserve untouched mixed endings and a missing final LF, and use the original newline convention for new lines. | Focused edits do not rewrite unrelated bytes or choose an ambiguous occurrence; this pure layer cannot bypass workspace path checks. |
| [`apply_hunks.flush`](../../src/rtagent/patches.py#L75) | Resolve and append one exact hunk. | Find exactly one matching context after cursor, honor optional end-of-file anchoring, append original unchanged lines and replacements, and advance the original-source cursor. | A missing/ambiguous match rejects the whole preflight; no fuzzy guess or quadratic list of all matches is needed. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`PatchOperation`](../../src/rtagent/patches.py#L10) | One immutable parsed file operation. | Frozen dataclass containing a relative path string, add/update/delete action and tuple of raw hunk lines. | Parsing carries intent without granting file authority; Workspace later checks the destination and current contents. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def parse_patch(patch: str) -> list[PatchOperation]
def apply_hunks(original: str, lines: tuple[str, ...]) -> str
def apply_hunks.flush() -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>
