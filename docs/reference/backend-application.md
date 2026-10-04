# Backend composition, application use cases, and HTTP

These modules connect contracts to concrete resources. The composition root owns resource lifetime, Studio owns multi-step actions, and routes adapt HTTP. Simple CRUD routes call Repository directly where no additional workflow is needed; there is no fabricated service-interface layer.

[Reference index](README.md) · [System architecture](../architecture.md)

## src/rtagent/main.py

**Layer: Composition root / HTTP infrastructure.** Assembles the concrete modular monolith, enforces the local browser boundary, and serves the built SPA.

**Connections and effects:** Constructs Settings, Repository, ToolRegistry, Runner, and Studio. Lifespan opens/closes resources; factory construction alone does not migrate databases or launch tasks. FastAPI state stores the shared process objects.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`error_response`](../../src/rtagent/main.py#L19) | Translate AppError to JSONResponse. | Emit code/message/details plus a random request ID and use error.status. | Transport mapping stays outside domain/application functions and avoids exposing a traceback as a public error. |
| [`create_app`](../../src/rtagent/main.py#L34) | Return a configured FastAPI application. | Register lifespan, loopback TrustedHostMiddleware, expected-error handler, origin middleware, API router, and SPA fallback. | One explicit composition root keeps resource ownership visible and permits tests/schema export to construct the app without starting it. |
| [`create_app.lifespan`](../../src/rtagent/main.py#L44) | Open and close the shared concrete resource owner. | Construct Harness with trusted hooks, invoke optional configure before entry, expose Studio/settings and yield inside its async context. | Web/embedding use identical migration/graph/recovery/shutdown behavior; no duplicate lifecycle or second loop is introduced. |
| [`create_app.app_error`](../../src/rtagent/main.py#L58) | Adapt FastAPI's expected-error callback. | Ignore request-specific diagnostics and delegate to error_response. | All AppError failures share the stable envelope without HTTP logic in business methods. |
| [`create_app.local_access`](../../src/rtagent/main.py#L62) | Validate local browser requests and add response headers. | For /api/ paths reject unknown Origin or cross-site Sec-Fetch-Site; headerless native clients are accepted. Set nosniff and no-referrer on normal responses. | A local single-user API has no login/token; loopback hosts and browser-origin checks bound access without introducing accounts. This is not remote authentication. |
| [`create_app.frontend`](../../src/rtagent/main.py#L82) | Return a built asset, SPA index, or explicit missing-build/API error. | Resolve paths under web/dist, serve existing contained files, refuse API fallback, otherwise serve index.html or 503. | Deep links can load the SPA while traversal and mistyped API calls do not become misleading HTML success responses. |
| [`serve`](../../src/rtagent/main.py#L104) | Start the installed/module entry point. | Ask Uvicorn to construct the factory on 127.0.0.1:8001. | One loopback worker is the supported ownership model; the entry point does not imply multi-worker coordination. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def error_response(error: AppError) -> JSONResponse
def create_app(settings: Settings | None=None, *, hooks: Hooks | None=None, configure: Callable[[Harness], None] | None=None) -> FastAPI
async def create_app.lifespan(app: FastAPI)
async def create_app.app_error(_request: Request, error: AppError)
async def create_app.local_access(request: Request, call_next)
async def create_app.frontend(path: str)
def serve() -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/application.py

**Layer: Application use cases.** Coordinates versioned snapshots, Run commands, workspace edits, and cross-store deletion.

**Connections and effects:** Studio shares Repository, ToolRegistry, and Runner. Its command lock serializes start/approval/resume/cancel/delete in this process; repository transactions separately serialize database writes. File reads/writes run with asyncio.to_thread; compilation itself is pure.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`Studio.__init__`](../../src/rtagent/application.py#L49) | Bind process-owned resources and a command mutex. | Store concrete repository/registry/runner references and create asyncio.Lock. | Only one implementation exists; concrete dependencies keep coordination explicit without unused abstraction interfaces. |
| [`Studio.workspace_registry`](../../src/rtagent/application.py#L145) | Resolve a project-native file/tool scope. | Read the project folder or use the default root and obtain its concrete registry. | UI inputs select saved IDs; native tool calls cannot choose another project root. |
| [`Studio.create_project`](../../src/rtagent/application.py#L154) | Register an existing local folder. | Validate absolute existing directory off-thread, reject application runtime data, then persist canonical root/name. | Opening a project neither copies files nor requires accounts; existing Runs remain unchanged. |
| [`Studio.workspace`](../../src/rtagent/application.py#L176) | Inspect the selected project context and actual sandbox. | Resolve a registered root, read instructions/memory/skill metadata off-thread, and reuse that roots native probe. | Editors and Prompt show the same file sources used by new Runs. |
| [`Studio.save_memory`](../../src/rtagent/application.py#L193) | Replace notes in the selected project. | Resolve its registry and perform a hash-guarded file write off-thread, then reload the document. | Notes remain ordinary per-workspace files with no cross-project default-root write. |
| [`Studio.skill`](../../src/rtagent/application.py#L201) | Read one current skill source in the chosen project. | Resolve project registry, load a validated file off-thread, and return SkillDocument. | Bodies are loaded on demand and retain their origin/hash. |
| [`Studio.save_skill`](../../src/rtagent/application.py#L208) | Save a user-authored project procedure. | Resolve the project registry, write frontmatter/body through guarded save_skill, then load the same source. | UI and model authoring use the same validation and filesystem boundary. |
| [`Studio._prepare`](../../src/rtagent/application.py#L214) | Capture and compile shared current inputs. | Resolve explicit/catalogued Skill selections, create the recorded runtime:environment message from root/OS/frozen tool exposure/mode/bounds, snapshot file context, compile once, and prepare the provider body. | Inspection and direct Run share the same compiler; operational environment instructions are visible evidence, not a separate hidden prompt. |
| [`Studio.inspect`](../../src/rtagent/application.py#L281) | Compile a draft without persistence or model calls. | Validate saved settings/session revisions, resolve authoritative session project or draft project, use committed history and _prepare. | Empty-input or unsaved block inspection needs no session/preset/preview database mutation. |
| [`Studio.preview`](../../src/rtagent/application.py#L305) | Freeze a version-bound execution input. | Validate idle/restored conversation and saved preset/config revisions, resolve its project registry, call shared _prepare, and save the context/root/request snapshot with expiry. | Direct Run uses the same path; browser live inspection does not produce disposable database rows. |
| [`Studio.start`](../../src/rtagent/application.py#L361) | Accept direct task data or StartRun and return RunView. | Validate Idempotency-Key, take command lock, find an identical prior submission before compilation, optionally call preview internally, atomically claim Run, and schedule queued work. | Run is direct from task input, while unchanged HTTP retries retain identity even after revisions/context have changed. Preview remains optional inspection. |
| [`Studio.view`](../../src/rtagent/application.py#L385) | Project saved execution state for clients. | Read Run, derive plan and active receipt output, label original workspace/preset/provider and redact unknown-call views. | Ordinary chat includes actual progress and uncertainty while raw canonical/wire bodies stay in explicit trace views. |
| [`Studio.approve`](../../src/rtagent/application.py#L433) | Record one human approval/rejection and resume. | Under the command lock validate pending approval ID/state, accept an identical repeated decision, reject a conflicting one, emit resolved/queued state, and await checkpoint completion before scheduling. | The decision binds the displayed arguments and persists before effects. Reentrant interrupt nodes must honor a decision already recorded. |
| [`Studio._resume_after_checkpoint`](../../src/rtagent/application.py#L455) | Schedule recovery after the preceding task has settled. | Shield-await an existing graph task, then call Runner.start(resume=True). | A fast approval must not race the interrupt checkpoint write or start two invocations for one thread. |
| [`Studio.resume`](../../src/rtagent/application.py#L462) | Reconcile uncertain receipts and resume their original Run. | Require interrupted state and exact resolution IDs; allow record only for tools; preserve old evidence in attempt_history; set retry receipts pending or recorded receipts succeeded; emit queued and resume after checkpoint. | Unknown writes/model charges are never silently retried, and new attempts cannot erase uncertainty evidence from prior attempts. |
| [`Studio.cancel`](../../src/rtagent/application.py#L514) | Apply Stop and return final operational state. | Serialize with other Run commands, await Runner.cancel, then project the saved Run. | HTTP completion means local cleanup has been awaited; it does not claim rollback of external effects. |
| [`Studio.save_config`](../../src/rtagent/application.py#L520) | Save a versioned settings aggregate and invalidate incompatible discovery. | Read previous config, let Repository enforce its revision, and reset registry if MCP connection definitions changed. | New Runs use new settings; existing frozen snapshots remain unchanged, and stale discovered names are not kept after server changes. |
| [`Studio.delete_session`](../../src/rtagent/application.py#L531) | Remove an idle conversation across app/checkpoint stores. | Hold command lock, verify idle, await each owned task, use the checkpointer's public adelete_thread API, then delete repository records. | Checkpoints must not outlive deleted application references. This crosses two stores and is deliberately ordered, not claimed as an atomic distributed transaction. |
| [`Studio.close`](../../src/rtagent/application.py#L55) | Cancel and await explicit summary tasks. | Signal every owned compaction task and gather completion before repository shutdown. | Summarization cannot write into a closed database or activate unfinished text during shutdown. |
| [`Studio.context`](../../src/rtagent/application.py#L63) | Inspect the original and effective history. | Resolve the session, validate/project active summary, read current provider limits and list reduction projections. | Users can see what is reduced and restore source context without modifying original messages. |
| [`Studio.compact`](../../src/rtagent/application.py#L83) | Start one explicit source-bound reduction. | Under the command lock validate config/session revisions, use only successful complete turns, retain recent turns, carry the earlier summary, freeze source digest/config and reserve/schedule the job. | Roles/tool pairing remain intact in the untouched suffix; only a checked result can become future Run input. |
| [`Studio.stop_compaction`](../../src/rtagent/application.py#L134) | Stop one owned reduction. | Check session ownership, cancel/await its task and return its saved terminal projection. | Cancelled partial text never replaces an existing active summary; uncertain remote completion remains recorded. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`Studio`](../../src/rtagent/application.py#L46) | Application coordinator. | Concrete resources plus an asyncio command lock; exposes task/context/recovery use cases. | Routes and tests reuse business workflows while HTTP serialization and graph internals stay outside the class. |

### Snapshot ownership

`Studio.preview` saves `schema_version`, preset, configuration, successful history, input, variables, schemas, session identity/revision, absolute workspace root/project ID, literal workspace messages, and prepared first request. The public Preview omits the private snapshot aggregate. `Repository.create_run` copies this snapshot into a Run and rechecks versions in its writer transaction; changing files or settings later cannot mutate it. Tool calls such as load_skill read the current file at call time and their result is recorded as new runtime evidence; only the initial selected skill content is frozen here.

The process command lock is not a global queue or a multi-worker guarantee. Metadata routes use the database writer lock independently. A concurrent save between Preview and Run creation produces an explicit stale-preview/revision conflict rather than changing the frozen input.

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def Studio.__init__(self, repository: Repository, registry: ToolRegistry, runner: Runner) -> None
async def Studio.close(self) -> None
async def Studio.context(self, session_id: str) -> ContextView
async def Studio.compact(self, session_id: str, request: CompactRequest) -> CompactionView
async def Studio.stop_compaction(self, session_id: str, identifier: str) -> CompactionView
async def Studio.workspace_registry(self, project_id: str | None=None) -> ToolRegistry
async def Studio.create_project(self, request: ProjectCreate) -> ProjectView
async def Studio.workspace(self, refresh: bool=False, project_id: str | None=None) -> WorkspaceView
async def Studio.save_memory(self, request: MemorySave, project_id: str | None=None) -> MemoryDocument
async def Studio.skill(self, name: str, project_id: str | None=None) -> SkillDocument
async def Studio.save_skill(self, request: SkillSave, project_id: str | None=None) -> SkillDocument
async def Studio._prepare(self, preset: Preset, config: StudioConfig, history: list[dict[str, Any]], user_input: str, variables: dict[str, Any], skills: list[str], registry: ToolRegistry) -> tuple[PromptInspection, list[Message]]
async def Studio.inspect(self, request: DraftPreviewRequest) -> PromptInspection
async def Studio.preview(self, request: PreviewRequest) -> Preview
async def Studio.start(self, request: StartRun | PreviewRequest, key: str) -> RunView
async def Studio.view(self, run_id: str) -> RunView
async def Studio.approve(self, run_id: str, approval_id: str, decision: str) -> RunView
async def Studio._resume_after_checkpoint(self, run_id: str) -> None
async def Studio.resume(self, run_id: str, request: ResumeRequest) -> RunView
async def Studio.cancel(self, run_id: str) -> RunView
async def Studio.save_config(self, value: StudioConfig) -> StudioConfig
async def Studio.delete_session(self, session_id: str) -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/api.py

**Layer: Transport / HTTP-SSE adapter.** Defines /api/v1 contracts and delegates actions to Studio or short Repository CRUD calls.

**Connections and effects:** Obtains Studio through request.app.state. FastAPI validates Pydantic input/output; application errors use main's handler. Trace/export/SSE perform presentation serialization/redaction; GET endpoints do not schedule execution.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`health`](../../src/rtagent/api.py#L140) | GET /health: return process availability/version. | Return a small literal mapping without database/model/sandbox checks. | Liveness is separate from workspace sandbox or model readiness. |
| [`bootstrap`](../../src/rtagent/api.py#L146) | GET /bootstrap: load saved browser projections. | Read config, presets, sessions, folders, projects, current catalog/errors, and the default root. | One query initializes local navigation without model calls or implicit MCP discovery. |
| [`save_config`](../../src/rtagent/api.py#L162) | PUT /config: save StudioConfig. | Delegate to Studio.save_config and return its advanced revision. | Configuration conflict/discovery invalidation remain application behavior, not route branches. |
| [`workspace`](../../src/rtagent/api.py#L168) | GET /workspace: inspect a selected project or the default root. | Delegate refresh and optional project_id to Studio.workspace. | File/sandbox behavior stays in the application and adapters. |
| [`save_memory`](../../src/rtagent/api.py#L176) | PUT /workspace/memory: save selected-project notes. | Forward validated MemorySave and optional project_id. | The server resolves the root and enforces hash conflicts instead of trusting a browser file path. |
| [`skill`](../../src/rtagent/api.py#L184) | GET /workspace/skills/name: inspect a selected-project skill. | Forward its name and optional project_id to Studio.skill. | A metadata catalog does not eagerly send every procedure body. |
| [`save_skill`](../../src/rtagent/api.py#L190) | PUT /workspace/skills: create/update a selected-project procedure. | Delegate SkillSave and optional project_id to Studio. | Root resolution, validation, and guarded edits are shared with native tools. |
| [`save_preset`](../../src/rtagent/api.py#L198) | PUT /presets/{preset_id}: save an aggregate. | Require route/body ID equality, then Repository.save_preset. | Resource identity is a transport consistency check; optimistic revisions and ownership stay in storage. |
| [`refresh_tools`](../../src/rtagent/api.py#L208) | POST /tools/refresh: discover saved MCP servers. | Read current config and call ToolRegistry.discover explicitly. | Server discovery is user-triggered; model text cannot load a server or grant its tools permission. |
| [`create_session`](../../src/rtagent/api.py#L215) | POST /sessions: open a project-bound empty conversation. | Pass title and nullable project_id to the repository, which validates membership. | Conversation creation is independent of model execution. |
| [`get_session`](../../src/rtagent/api.py#L227) | GET /sessions/{session_id}: retrieve committed history. | Return Repository.session projected as SessionView. | Reopening the UI reads state and never resumes a checkpoint. |
| [`update_session`](../../src/rtagent/api.py#L275) | PATCH /sessions/{session_id}: edit metadata. | Pass SessionUpdate to Repository.update_session. | Archive/folder/title revisions and idle rules are checked atomically with database changes. |
| [`copy_session`](../../src/rtagent/api.py#L281) | POST /sessions/{session_id}/copy: duplicate/fork (201). | Pass revision/operation/boundary to Repository.copy_session. | Saved evidence is copied transactionally without model dispatch or graph checkpoint cloning. |
| [`create_folder`](../../src/rtagent/api.py#L287) | POST /folders: create a flat folder (201). | Delegate normalized name to Repository.save_folder. | Sidebar grouping is ordinary metadata with no filesystem move or nested hierarchy. |
| [`rename_folder`](../../src/rtagent/api.py#L293) | PATCH /folders/{folder_id}: rename one observed revision. | Pass name, ID, and expected revision to Repository.save_folder. | Stale edits and duplicate names use the same transaction as creation. |
| [`delete_folder`](../../src/rtagent/api.py#L301) | DELETE /folders/{folder_id}: ungroup members (204). | Await Repository.delete_folder and return no body. | Folder deletion preserves conversation ownership/evidence. |
| [`delete_session`](../../src/rtagent/api.py#L307) | DELETE /sessions/{session_id}: remove idle history (204). | Delegate to Studio.delete_session for both checkpoint and application cleanup. | Cross-resource deletion needs lifecycle coordination rather than a direct SQL-only route. |
| [`session_runs`](../../src/rtagent/api.py#L313) | GET /sessions/{session_id}/runs: list inspectable turns. | Read ordered stored Runs and call Studio.view for each. | All saved turns, including failed/cancelled/copied ones, appear separately from successful input history; unknown outcomes are included in projections. |
| [`inspect_prompt`](../../src/rtagent/api.py#L320) | POST /prompts/inspect: compile a live editor draft. | Delegate validated DraftPreviewRequest to Studio.inspect and return PromptInspection. | Inspection only reads saved/file context and never calls a model or persists execution input. |
| [`create_project`](../../src/rtagent/api.py#L221) | POST /projects: register a named workspace folder. | Delegate ProjectCreate to Studio and return its projection. | Root validation and registration belong to the application layer. |
| [`preview`](../../src/rtagent/api.py#L326) | POST /prompts/preview: inspect compiled input. | Delegate PreviewRequest to Studio.preview. | Inspection shares direct Run compilation but never calls the model. |
| [`start_run`](../../src/rtagent/api.py#L332) | POST /runs: accept direct input or a saved preview (202). | Require Idempotency-Key header, validate the union contract, and call Studio.start. | The transport exposes retry identity; it does not require Preview or accept ad hoc replacement permissions. |
| [`get_run`](../../src/rtagent/api.py#L340) | GET /runs/{run_id}: read RunView. | Delegate to Studio.view. | Polling/refresh is safe and independent of task scheduling. |
| [`cancel_run`](../../src/rtagent/api.py#L346) | POST /runs/{run_id}/cancel: stop local work. | Await Studio.cancel and return its saved projection. | Disconnection is not Stop; an explicit command owns cancellation semantics. |
| [`approve_run`](../../src/rtagent/api.py#L352) | POST /runs/{run_id}/approvals/{approval_id}: approve/reject one call. | Forward the URL identities and literal decision to Studio.approve. | Approval binds one pending invocation rather than updating the global tool permission map. |
| [`resume_run`](../../src/rtagent/api.py#L360) | POST /runs/{run_id}/resume: recover explicitly. | Validate per-receipt resolutions and call Studio.resume. | Unknown outcomes are reconciled through a command, never through reconnecting SSE. |
| [`trace`](../../src/rtagent/api.py#L366) | GET /runs/{run_id}/trace: inspect receipts. | Verify Run exists, fetch calls, return a labeled recursively redacted copy unless raw=true. | Original local evidence is an explicit inspection choice; default views do not overwrite receipts. |
| [`export`](../../src/rtagent/api.py#L375) | GET /runs/{run_id}/export: download evidence JSON. | Remove idempotency_key/request_hash, include Run and calls, recursively redact, label schema/redaction versions and replayable=false, and set attachment headers. | Exports are review copies, not runnable bundles; redaction may remove exact original input. |
| [`run_events`](../../src/rtagent/api.py#L402) | GET /runs/{run_id}/events: prepare SSE replay. | Prefer Last-Event-ID to after; parse an integer and reject negative/ahead-of-Run cursors with 410 before constructing the stream. | Cursor validation catches stale client state and never creates a new Run. |
| [`run_events.stream`](../../src/rtagent/api.py#L417) | Yield persistent SSE records until disconnect or terminal catch-up. | Poll pages of 200 every 150 ms, shield short database reads, write seq IDs/type/redacted JSON, and heartbeat every 40 iterations. | Durable sequences support browser deduplication; disconnect closes the reader, not execution, and shielded reads avoid half-closed async SQLite connections. |
| [`preset_export`](../../src/rtagent/api.py#L58) | GET a reusable saved preset package. | Read the typed preset and wrap it in its versioned format. | Export has no model/tool effects and excludes settings, secrets and histories. |
| [`preset_import`](../../src/rtagent/api.py#L64) | POST a new preset from a data package. | Validate the format, replace aggregate/block identities and save a new revision-one preset. | Import cannot overwrite a user preset or introduce an execution record. |
| [`skill_export`](../../src/rtagent/api.py#L70) | GET one selected-project procedure package. | Resolve the trusted project registry and collect validated ordinary UTF-8 resources off-thread. | Export includes required supporting files without links, runtime files or credentials. |
| [`skill_import`](../../src/rtagent/api.py#L77) | POST a new selected-project procedure package. | Enforce current read-only policy, resolve workspace and validate/stage/publish through packages.py. | Imported data cannot silently overwrite a procedure or grant permissions. |
| [`event_log`](../../src/rtagent/api.py#L88) | GET a finite persisted event page. | Check Run existence and read at most 200 events after a nonnegative sequence cursor. | CLI/RPC clients can reconnect without maintaining SSE or resubmitting effects. |
| [`list_processes`](../../src/rtagent/api.py#L95) | GET a conversation's original owned process records. | Join through the original Runs and project each durable lifecycle row. | Copies may retain tool evidence but never acquire another process's live controls. |
| [`process_input`](../../src/rtagent/api.py#L104) | POST explicit stdin to an owned live process. | Forward bounded text/EOF and session identity to ProcessManager.input. | The backend checks ownership and current read-only policy independently of disabled UI controls. |
| [`stop_process`](../../src/rtagent/api.py#L114) | POST stop and await native cleanup. | Resolve conversation ownership and await the manager's Job/process-group termination. | A successful HTTP response establishes completed cleanup, while partial file effects remain inspectable. |
| [`session_context`](../../src/rtagent/api.py#L233) | GET effective history accounting. | Adapt Studio.context to the typed ContextView. | A projection is read-only and cannot trigger a paid reduction. |
| [`compact_session`](../../src/rtagent/api.py#L239) | POST an explicit summary job (202). | Validate CompactRequest and return the durable source-bound CompactionView immediately. | Async acceptance has a distinct completion state and preserved request evidence. |
| [`restore_context`](../../src/rtagent/api.py#L247) | PUT the full-history selection (204). | Forward expected revision to Repository.reset_compaction. | Restoring source history is reversible and does not erase summaries or original Runs. |
| [`compaction_evidence`](../../src/rtagent/api.py#L254) | GET original or labeled redacted reduction evidence. | Verify session ownership and apply redaction by default; redacted=false explicitly requests local originals. | Ordinary context lists and exported copies do not masquerade as unmodified outbound bodies. |
| [`cancel_compaction`](../../src/rtagent/api.py#L269) | POST cancel for one reduction. | Forward both ownership identifiers to Studio.stop_compaction. | Stop cannot redirect to another conversation or implicitly restart provider work. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`SessionCreate`](../../src/rtagent/api.py#L119) | Empty-conversation creation input. | Optional bounded title and nullable project_id. | New conversations use a registered root without placing arbitrary workspace paths in model tool arguments. |
| [`Bootstrap`](../../src/rtagent/api.py#L126) | Browser initialization output. | Current config/presets/sessions/folders/tools/errors/root using domain contracts. | One generated typed response seeds frontend query state; credentials remain references and skill bodies are absent. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
async def preset_export(preset_id: str, request: Request) -> PresetPackage
async def preset_import(body: PresetPackage, request: Request) -> Preset
async def skill_export(name: str, request: Request, project_id: str | None=None) -> SkillPackage
async def skill_import(body: SkillPackage, request: Request, project_id: str | None=None) -> dict
async def event_log(run_id: str, request: Request, after: int=0) -> list[dict]
async def list_processes(session_id: str, request: Request) -> list[ProcessView]
async def process_input(session_id: str, identifier: str, body: ProcessInput, request: Request) -> ProcessView
async def stop_process(session_id: str, identifier: str, request: Request) -> ProcessView
async def health() -> dict[str, str]
async def bootstrap(request: Request) -> Bootstrap
async def save_config(body: StudioConfig, request: Request) -> StudioConfig
async def workspace(request: Request, refresh: bool=False, project_id: str | None=None) -> WorkspaceView
async def save_memory(body: MemorySave, request: Request, project_id: str | None=None) -> MemoryDocument
async def skill(name: str, request: Request, project_id: str | None=None) -> SkillDocument
async def save_skill(body: SkillSave, request: Request, project_id: str | None=None) -> SkillDocument
async def save_preset(preset_id: str, body: Preset, request: Request) -> Preset
async def refresh_tools(request: Request) -> list[ToolDefinition]
async def create_session(body: SessionCreate, request: Request) -> dict[str, Any]
async def create_project(body: ProjectCreate, request: Request) -> ProjectView
async def get_session(session_id: str, request: Request) -> dict[str, Any]
async def session_context(session_id: str, request: Request) -> ContextView
async def compact_session(session_id: str, body: CompactRequest, request: Request) -> CompactionView
async def restore_context(session_id: str, body: ContextRestore, request: Request) -> Response
async def compaction_evidence(session_id: str, identifier: str, request: Request, redacted: bool=True) -> dict[str, Any]
async def cancel_compaction(session_id: str, identifier: str, request: Request) -> CompactionView
async def update_session(session_id: str, body: SessionUpdate, request: Request) -> dict[str, Any]
async def copy_session(session_id: str, body: SessionCopy, request: Request) -> dict[str, Any]
async def create_folder(body: FolderCreate, request: Request) -> dict[str, Any]
async def rename_folder(folder_id: str, body: FolderUpdate, request: Request) -> dict[str, Any]
async def delete_folder(folder_id: str, request: Request) -> None
async def delete_session(session_id: str, request: Request) -> None
async def session_runs(session_id: str, request: Request) -> list[RunView]
async def inspect_prompt(body: DraftPreviewRequest, request: Request) -> PromptInspection
async def preview(body: PreviewRequest, request: Request) -> Preview
async def start_run(body: StartRun | PreviewRequest, request: Request, idempotency_key: str=Header()) -> RunView
async def get_run(run_id: str, request: Request) -> RunView
async def cancel_run(run_id: str, request: Request) -> RunView
async def approve_run(run_id: str, approval_id: str, body: ApprovalDecision, request: Request) -> RunView
async def resume_run(run_id: str, body: ResumeRequest, request: Request) -> RunView
async def trace(run_id: str, request: Request, raw: bool=False) -> dict[str, Any]
async def export(run_id: str, request: Request) -> Response
async def run_events(run_id: str, request: Request, after: int=0, last_event_id: str | None=Header(default=None))
async def run_events.stream()
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/harness.py

**Layer: Application composition / public Python ownership.** Owns the same concrete Studio, Registry, Repository and Runner used by the Web application.

**Connections and effects:** The async context opens one worker/checkpointer and shuts down summaries, Runs and processes before persistence. Embeddings must own a separate data directory from an already running service; attaching clients use AgentClient.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`Harness.__init__`](../../src/rtagent/harness.py#L18) | Prepare one resource owner without executing I/O. | Construct Settings, SQLite Repository, native/MCP/local Registry, Runner and Studio; accept optional trusted hooks. | Explicit local composition supports customization without a plugin framework or another agent loop. |
| [`Harness.__aenter__`](../../src/rtagent/harness.py#L32) | Open the harness lifecycle. | Normalize paths, migrate/seed, apply explicit provider overrides, open graph/checkpointer and clean up failed setup. | Web and embedded clients get identical startup/recovery policy and one active ownership context. |
| [`Harness.__aexit__`](../../src/rtagent/harness.py#L51) | Await shutdown effects before closing storage. | Stop summary tasks and Runner/process resources, dispose the database in finally and clear the entered flag. | Exiting cannot leave owned OS commands silently running or write receipts after disposal. |
| [`Harness.run`](../../src/rtagent/harness.py#L60) | Submit direct task input using observed revisions. | Require entered state, resolve/create a session, read preset/config, construct PreviewRequest and call Studio.start with an idempotency key. | Shared compilation/policy/checkpoint behavior is exposed without requiring Preview or duplicating execution code. |
| [`Harness.wait`](../../src/rtagent/harness.py#L93) | Await terminal or attention state. | Poll Studio.view under a bounded caller timeout; return approval/interrupted states as actionable results. | A wait timeout detaches instead of cancelling or automatically approving work. |
| [`Harness.approve`](../../src/rtagent/harness.py#L107) | Resolve a saved single-call approval. | Validate approve/reject and invoke Studio.approve with the original approval identity. | The SDK cannot broaden tool policy through an alternate approval path. |
| [`Harness.cancel`](../../src/rtagent/harness.py#L113) | Stop one Run through the application boundary. | Await Studio.cancel and native cleanup. | Client code observes the same partial-effect and durable status semantics as Web. |
| [`Harness.resume`](../../src/rtagent/harness.py#L117) | Continue an interrupted original snapshot. | Pass explicit ResumeRequest resolutions or an empty request to Studio.resume. | Unknown outcomes require decisions rather than being replayed by an SDK-specific retry loop. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`Harness`](../../src/rtagent/harness.py#L15) | Concrete shared lifecycle and typed Run facade. | One repository, registry, runner and studio with explicit async context ownership. | Custom integrations reuse the implemented graph while resources and checkpoint shutdown have one owner. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def Harness.__init__(self, settings: Settings | None=None, hooks: Hooks | None=None) -> None
async def Harness.__aenter__(self) -> 'Harness'
async def Harness.__aexit__(self, *_exception: Any) -> None
async def Harness.run(self, task: str, *, session_id: str | None=None, preset_id: str='general-agent', variables: dict[str, Any] | None=None, skills: list[str] | None=None, key: str | None=None) -> RunView
async def Harness.wait(self, run_id: str, timeout: float=600) -> RunView
async def Harness.approve(self, run_id: str, approval_id: str, decision: str) -> RunView
async def Harness.cancel(self, run_id: str) -> RunView
async def Harness.resume(self, run_id: str, resolutions: ResumeRequest | None=None) -> RunView
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/client.py

**Layer: Application transport / public HTTP client.** Attaches Python callers, CLI and JSON/RPC to the existing single-worker local service.

**Connections and effects:** Owns only an HTTP connection pool; it never opens app.db, checkpoints or native handles. Closing the client does not stop server work.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`AgentClient.__init__`](../../src/rtagent/client.py#L16) | Select the service API base. | Normalize the caller-supplied base URL and retain an unopened pool reference. | Existing deployments can expose the same contracts without a second worker owning their databases. |
| [`AgentClient.__aenter__`](../../src/rtagent/client.py#L21) | Open a reusable client pool. | Create httpx with explicit timeouts and trust_env=false. | Ambient proxy credentials are not inherited by local service requests. |
| [`AgentClient.__aexit__`](../../src/rtagent/client.py#L26) | Detach and release sockets. | Await pool close and clear the reference. | Client lifetime remains independent of durable Runs and background commands. |
| [`AgentClient.request`](../../src/rtagent/client.py#L32) | Exchange a relative API JSON command/projection. | Reject missing context or external authority paths, preserve valid identifier colons, send optional idempotency header and map safe HTTP/application errors. | One transport path underlies all external facades and preserves server validation. |
| [`AgentClient.start`](../../src/rtagent/client.py#L63) | Submit an explicitly captured Run request. | POST the supplied PreviewRequest and fixed key and parse RunView. | Retrying this same request/key retains identity despite later revision changes. |
| [`AgentClient.run`](../../src/rtagent/client.py#L69) | Submit a convenient current task. | Fetch revisions, resolve/create session, forbid implicit project moves, find preset and call start. | Convenience avoids Preview but is not the stable retry primitive when revisions/session creation change. |
| [`AgentClient.get`](../../src/rtagent/client.py#L107) | Read one durable Run projection. | Quote the Run identifier, GET and validate RunView. | Reading never recreates execution or consumes a checkpoint. |
| [`AgentClient.wait`](../../src/rtagent/client.py#L111) | Await completion or required attention. | Poll get under a caller deadline and return terminal/approval/interrupted projections. | Timeout leaves the Run active and asks for explicit inspect/cancel rather than retrying effects. |
| [`AgentClient.events`](../../src/rtagent/client.py#L125) | Read a finite event page. | Quote identity and request events after the supplied sequence cursor. | Persisted cursors support reconnecting machine clients without replaying a Run. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`AgentClient`](../../src/rtagent/client.py#L13) | Typed connection to a running service. | Async context plus Run operations and one relative JSON transport. | Service ownership and client attachment are distinct; a client is not another agent runtime. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def AgentClient.__init__(self, base_url: str='http://127.0.0.1:8001') -> None
async def AgentClient.__aenter__(self) -> 'AgentClient'
async def AgentClient.__aexit__(self, *_exception: Any) -> None
async def AgentClient.request(self, method: str, path: str, body: Any=None, key: str | None=None) -> Any
async def AgentClient.start(self, request: PreviewRequest, key: str) -> RunView
async def AgentClient.run(self, task: str, *, session_id: str | None=None, project_id: str | None=None, preset_id: str='general-agent', variables: dict[str, Any] | None=None, skills: list[str] | None=None, key: str | None=None) -> RunView
async def AgentClient.get(self, run_id: str) -> RunView
async def AgentClient.wait(self, run_id: str, timeout: float=600) -> RunView
async def AgentClient.events(self, run_id: str, after: int=0) -> list[dict[str, Any]]
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/cli.py

**Layer: Application transport / command line.** Adapts explicit command-line actions to AgentClient; the default command starts the normal Web service.

**Connections and effects:** The installed console entry and python -m rtagent call main. __main__.py contains only this entry invocation. Client commands share server permissions and revisions, never launch a separate local execution path.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`parser`](../../src/rtagent/cli.py#L15) | Define service, Run, inspection/control and package commands. | Build argparse subcommands with session/project/preset/Skill selection, file/stdin task input, JSON/wait flags and explicit approval/recovery IDs. | Users can script concrete operations without arbitrary tool dispatch or CLI-only settings stores. |
| [`execute`](../../src/rtagent/cli.py#L55) | Execute one parsed client command. | Open AgentClient, dispatch fixed API operations, emit JSON or concise assistant output, bound imported package size and export with exclusive file creation. | Web, CLI and RPC reuse the same saved state; --wait returns attention without approving it. |
| [`main`](../../src/rtagent/cli.py#L150) | Select service composition or client execution. | Reconfigure standard streams to UTF-8 (including Windows pipes), parse args, call serve or asyncio.run and emit safe stderr/exit codes. | Unicode machine output remains usable; Ctrl+C detaches and cancellation is explicit. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def parser() -> argparse.ArgumentParser
async def execute(args: argparse.Namespace) -> int
def main() -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/rpc.py

**Layer: Application transport / bounded JSON-RPC.** Forwards a fixed set of newline JSON-RPC requests to the local HTTP service.

**Connections and effects:** Requests are sequential, ID-bearing JSON objects capped at 512 KiB per line. No arbitrary URL/tool/function execution, notifications or batch envelope is supported; stdout contains response JSON only.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`dispatch`](../../src/rtagent/rpc.py#L61) | Map one method to a supported application operation. | Validate convenience/captured Run contracts, finite event/wait options, fixed quoted resource paths and allowed body/project fields; publish a method/contract schema. | Extensibility starts with concrete typed operations, not a generic execution endpoint that bypasses policy. |
| [`serve_rpc`](../../src/rtagent/rpc.py#L117) | Read and answer bounded request lines. | Parse JSON and a strict version/ID/method envelope, dispatch through AgentClient and emit separate parse/envelope/method/parameter/service errors. | Machine clients can distinguish malformed input from an application failure; disconnect does not replay or cancel work. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`Submission`](../../src/rtagent/rpc.py#L40) | Current-revision convenience task parameters. | Bounded task, optional session/project/preset/variables/Skills and key. | run.submit is convenient while run.start accepts a frozen explicit request for stable retry. |
| [`RPCRequest`](../../src/rtagent/rpc.py#L52) | Observable JSON-RPC request envelope. | Literal version 2.0, required strict string/integer ID (booleans rejected), nonempty method and object params; unknown fields fail. | One input line maps to one response, with explicit supported protocol limits. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
async def dispatch(client: AgentClient, method: str, params: dict[str, Any]) -> Any
async def serve_rpc(client: AgentClient) -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>
