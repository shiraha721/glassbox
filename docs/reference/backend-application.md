# Backend composition, application use cases, and HTTP

These modules connect contracts to concrete resources. The composition root owns resource lifetime, Studio owns multi-step actions, and routes adapt HTTP. Simple CRUD routes call Repository directly where no additional workflow is needed; there is no fabricated service-interface layer.

[Reference index](README.md) · [System architecture](../architecture.md)

## src/rtagent/main.py

**Layer: Composition root / HTTP infrastructure.** Assembles the concrete modular monolith, enforces the local browser boundary, and serves the built SPA.

**Connections and effects:** Constructs Settings, Repository, ToolRegistry, Runner, and Studio. Lifespan opens/closes resources; factory construction alone does not migrate databases or launch tasks. FastAPI state stores the shared process objects.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`error_response`](../../src/rtagent/main.py#L20) | Translate AppError to JSONResponse. | Emit code/message/details plus a random request ID and use error.status. | Transport mapping stays outside domain/application functions and avoids exposing a traceback as a public error. |
| [`create_app`](../../src/rtagent/main.py#L35) | Return a configured FastAPI application. | Register lifespan, loopback TrustedHostMiddleware, expected-error handler, origin middleware, API router, and SPA fallback. | One explicit composition root keeps resource ownership visible and permits tests/schema export to construct the app without starting it. |
| [`create_app.lifespan`](../../src/rtagent/main.py#L40) | Open and close process resources. | Normalize paths, migrate/seed Repository, save changed startup overrides, create tools/runner/studio, open checkpoints/recover work, yield, then close runner before repository. | Tasks must stop and checkpoint writes finish while the database is still available. One lifecycle owns concrete resources rather than module-global background tasks. |
| [`create_app.app_error`](../../src/rtagent/main.py#L64) | Adapt FastAPI's expected-error callback. | Ignore request-specific diagnostics and delegate to error_response. | All AppError failures share the stable envelope without HTTP logic in business methods. |
| [`create_app.local_access`](../../src/rtagent/main.py#L68) | Validate local browser requests and add response headers. | For /api/ paths reject unknown Origin or cross-site Sec-Fetch-Site; headerless native clients are accepted. Set nosniff and no-referrer on normal responses. | A local single-user API has no login/token; loopback hosts and browser-origin checks bound access without introducing accounts. This is not remote authentication. |
| [`create_app.frontend`](../../src/rtagent/main.py#L88) | Return a built asset, SPA index, or explicit missing-build/API error. | Resolve paths under web/dist, serve existing contained files, refuse API fallback, otherwise serve index.html or 503. | Deep links can load the SPA while traversal and mistyped API calls do not become misleading HTML success responses. |
| [`serve`](../../src/rtagent/main.py#L110) | Start the installed/module entry point. | Ask Uvicorn to construct the factory on 127.0.0.1:8001. | One loopback worker is the supported ownership model; the entry point does not imply multi-worker coordination. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def error_response(error: AppError) -> JSONResponse
def create_app(settings: Settings | None=None) -> FastAPI
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
| [`Studio.__init__`](../../src/rtagent/application.py#L44) | Bind process-owned resources and a command mutex. | Store concrete repository/registry/runner references and create asyncio.Lock. | Only one implementation exists; concrete dependencies keep coordination explicit without unused abstraction interfaces. |
| [`Studio.workspace_registry`](../../src/rtagent/application.py#L49) | Resolve a project-native file/tool scope. | Read the project folder or use the default root and obtain its concrete registry. | UI inputs select saved IDs; native tool calls cannot choose another project root. |
| [`Studio.create_project`](../../src/rtagent/application.py#L58) | Register an existing local folder. | Validate absolute existing directory off-thread, reject application runtime data, then persist canonical root/name. | Opening a project neither copies files nor requires accounts; existing Runs remain unchanged. |
| [`Studio.workspace`](../../src/rtagent/application.py#L80) | Inspect the selected project context and actual sandbox. | Resolve a registered root, read instructions/memory/skill metadata off-thread, and reuse that roots native probe. | Editors and Prompt show the same file sources used by new Runs. |
| [`Studio.save_memory`](../../src/rtagent/application.py#L97) | Replace notes in the selected project. | Resolve its registry and perform a hash-guarded file write off-thread, then reload the document. | Notes remain ordinary per-workspace files with no cross-project default-root write. |
| [`Studio.skill`](../../src/rtagent/application.py#L105) | Read one current skill source in the chosen project. | Resolve project registry, load a validated file off-thread, and return SkillDocument. | Bodies are loaded on demand and retain their origin/hash. |
| [`Studio.save_skill`](../../src/rtagent/application.py#L112) | Save a user-authored project procedure. | Resolve the project registry, write frontmatter/body through guarded save_skill, then load the same source. | UI and model authoring use the same validation and filesystem boundary. |
| [`Studio._prepare`](../../src/rtagent/application.py#L118) | Capture and compile shared current inputs. | Resolve explicit/catalogued Skill selections, create the recorded runtime:environment message from root/OS/frozen tool exposure/mode/bounds, snapshot file context, compile once, and prepare the provider body. | Inspection and direct Run share the same compiler; operational environment instructions are visible evidence, not a separate hidden prompt. |
| [`Studio.inspect`](../../src/rtagent/application.py#L176) | Compile a draft without persistence or model calls. | Validate saved settings/session revisions, resolve authoritative session project or draft project, use committed history and _prepare. | Empty-input or unsaved block inspection needs no session/preset/preview database mutation. |
| [`Studio.preview`](../../src/rtagent/application.py#L200) | Freeze a version-bound execution input. | Validate idle/restored conversation and saved preset/config revisions, resolve its project registry, call shared _prepare, and save the context/root/request snapshot with expiry. | Direct Run uses the same path; browser live inspection does not produce disposable database rows. |
| [`Studio.start`](../../src/rtagent/application.py#L254) | Accept direct task data or StartRun and return RunView. | Validate Idempotency-Key, take command lock, find an identical prior submission before compilation, optionally call preview internally, atomically claim Run, and schedule queued work. | Run is direct from task input, while unchanged HTTP retries retain identity even after revisions/context have changed. Preview remains optional inspection. |
| [`Studio.view`](../../src/rtagent/application.py#L278) | Project private Run evidence for the browser. | Copy operational fields, preserve the frozen root/provenance, derive the latest valid plan from paired update_plan messages, and redact unknown receipt data. | Plans survive refresh/restart/copies through existing evidence; this projection needs no new database table or independent scheduler state. |
| [`Studio.approve`](../../src/rtagent/application.py#L313) | Record one human approval/rejection and resume. | Under the command lock validate pending approval ID/state, accept an identical repeated decision, reject a conflicting one, emit resolved/queued state, and await checkpoint completion before scheduling. | The decision binds the displayed arguments and persists before effects. Reentrant interrupt nodes must honor a decision already recorded. |
| [`Studio._resume_after_checkpoint`](../../src/rtagent/application.py#L335) | Schedule recovery after the preceding task has settled. | Shield-await an existing graph task, then call Runner.start(resume=True). | A fast approval must not race the interrupt checkpoint write or start two invocations for one thread. |
| [`Studio.resume`](../../src/rtagent/application.py#L342) | Reconcile uncertain receipts and resume their original Run. | Require interrupted state and exact resolution IDs; allow record only for tools; preserve old evidence in attempt_history; set retry receipts pending or recorded receipts succeeded; emit queued and resume after checkpoint. | Unknown writes/model charges are never silently retried, and new attempts cannot erase uncertainty evidence from prior attempts. |
| [`Studio.cancel`](../../src/rtagent/application.py#L394) | Apply Stop and return final operational state. | Serialize with other Run commands, await Runner.cancel, then project the saved Run. | HTTP completion means local cleanup has been awaited; it does not claim rollback of external effects. |
| [`Studio.save_config`](../../src/rtagent/application.py#L400) | Save a versioned settings aggregate and invalidate incompatible discovery. | Read previous config, let Repository enforce its revision, and reset registry if MCP connection definitions changed. | New Runs use new settings; existing frozen snapshots remain unchanged, and stale discovered names are not kept after server changes. |
| [`Studio.delete_session`](../../src/rtagent/application.py#L408) | Remove an idle conversation across app/checkpoint stores. | Hold command lock, verify idle, await each owned task, use the checkpointer's public adelete_thread API, then delete repository records. | Checkpoints must not outlive deleted application references. This crosses two stores and is deliberately ordered, not claimed as an atomic distributed transaction. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`Studio`](../../src/rtagent/application.py#L41) | Application coordinator. | Concrete resources plus an asyncio command lock; exposes task/context/recovery use cases. | Routes and tests reuse business workflows while HTTP serialization and graph internals stay outside the class. |

### Snapshot ownership

`Studio.preview` saves `schema_version`, preset, configuration, successful history, input, variables, schemas, session identity/revision, absolute workspace root/project ID, literal workspace messages, and prepared first request. The public Preview omits the private snapshot aggregate. `Repository.create_run` copies this snapshot into a Run and rechecks versions in its writer transaction; changing files or settings later cannot mutate it. Tool calls such as load_skill read the current file at call time and their result is recorded as new runtime evidence; only the initial selected skill content is frozen here.

The process command lock is not a global queue or a multi-worker guarantee. Metadata routes use the database writer lock independently. A concurrent save between Preview and Run creation produces an explicit stale-preview/revision conflict rather than changing the frozen input.

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def Studio.__init__(self, repository: Repository, registry: ToolRegistry, runner: Runner) -> None
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
| [`health`](../../src/rtagent/api.py#L68) | GET /health: return process availability/version. | Return a small literal mapping without database/model/sandbox checks. | Liveness is separate from workspace sandbox or model readiness. |
| [`bootstrap`](../../src/rtagent/api.py#L74) | GET /bootstrap: load saved browser projections. | Read config, presets, sessions, folders, projects, current catalog/errors, and the default root. | One query initializes local navigation without model calls or implicit MCP discovery. |
| [`save_config`](../../src/rtagent/api.py#L90) | PUT /config: save StudioConfig. | Delegate to Studio.save_config and return its advanced revision. | Configuration conflict/discovery invalidation remain application behavior, not route branches. |
| [`workspace`](../../src/rtagent/api.py#L96) | GET /workspace: inspect a selected project or the default root. | Delegate refresh and optional project_id to Studio.workspace. | File/sandbox behavior stays in the application and adapters. |
| [`save_memory`](../../src/rtagent/api.py#L104) | PUT /workspace/memory: save selected-project notes. | Forward validated MemorySave and optional project_id. | The server resolves the root and enforces hash conflicts instead of trusting a browser file path. |
| [`skill`](../../src/rtagent/api.py#L112) | GET /workspace/skills/name: inspect a selected-project skill. | Forward its name and optional project_id to Studio.skill. | A metadata catalog does not eagerly send every procedure body. |
| [`save_skill`](../../src/rtagent/api.py#L118) | PUT /workspace/skills: create/update a selected-project procedure. | Delegate SkillSave and optional project_id to Studio. | Root resolution, validation, and guarded edits are shared with native tools. |
| [`save_preset`](../../src/rtagent/api.py#L126) | PUT /presets/{preset_id}: save an aggregate. | Require route/body ID equality, then Repository.save_preset. | Resource identity is a transport consistency check; optimistic revisions and ownership stay in storage. |
| [`refresh_tools`](../../src/rtagent/api.py#L136) | POST /tools/refresh: discover saved MCP servers. | Read current config and call ToolRegistry.discover explicitly. | Server discovery is user-triggered; model text cannot load a server or grant its tools permission. |
| [`create_session`](../../src/rtagent/api.py#L143) | POST /sessions: open a project-bound empty conversation. | Pass title and nullable project_id to the repository, which validates membership. | Conversation creation is independent of model execution. |
| [`get_session`](../../src/rtagent/api.py#L155) | GET /sessions/{session_id}: retrieve committed history. | Return Repository.session projected as SessionView. | Reopening the UI reads state and never resumes a checkpoint. |
| [`update_session`](../../src/rtagent/api.py#L161) | PATCH /sessions/{session_id}: edit metadata. | Pass SessionUpdate to Repository.update_session. | Archive/folder/title revisions and idle rules are checked atomically with database changes. |
| [`copy_session`](../../src/rtagent/api.py#L167) | POST /sessions/{session_id}/copy: duplicate/fork (201). | Pass revision/operation/boundary to Repository.copy_session. | Saved evidence is copied transactionally without model dispatch or graph checkpoint cloning. |
| [`create_folder`](../../src/rtagent/api.py#L173) | POST /folders: create a flat folder (201). | Delegate normalized name to Repository.save_folder. | Sidebar grouping is ordinary metadata with no filesystem move or nested hierarchy. |
| [`rename_folder`](../../src/rtagent/api.py#L179) | PATCH /folders/{folder_id}: rename one observed revision. | Pass name, ID, and expected revision to Repository.save_folder. | Stale edits and duplicate names use the same transaction as creation. |
| [`delete_folder`](../../src/rtagent/api.py#L187) | DELETE /folders/{folder_id}: ungroup members (204). | Await Repository.delete_folder and return no body. | Folder deletion preserves conversation ownership/evidence. |
| [`delete_session`](../../src/rtagent/api.py#L193) | DELETE /sessions/{session_id}: remove idle history (204). | Delegate to Studio.delete_session for both checkpoint and application cleanup. | Cross-resource deletion needs lifecycle coordination rather than a direct SQL-only route. |
| [`session_runs`](../../src/rtagent/api.py#L199) | GET /sessions/{session_id}/runs: list inspectable turns. | Read ordered stored Runs and call Studio.view for each. | All saved turns, including failed/cancelled/copied ones, appear separately from successful input history; unknown outcomes are included in projections. |
| [`inspect_prompt`](../../src/rtagent/api.py#L206) | POST /prompts/inspect: compile a live editor draft. | Delegate validated DraftPreviewRequest to Studio.inspect and return PromptInspection. | Inspection only reads saved/file context and never calls a model or persists execution input. |
| [`create_project`](../../src/rtagent/api.py#L149) | POST /projects: register a named workspace folder. | Delegate ProjectCreate to Studio and return its projection. | Root validation and registration belong to the application layer. |
| [`preview`](../../src/rtagent/api.py#L212) | POST /prompts/preview: inspect compiled input. | Delegate PreviewRequest to Studio.preview. | Inspection shares direct Run compilation but never calls the model. |
| [`start_run`](../../src/rtagent/api.py#L218) | POST /runs: accept direct input or a saved preview (202). | Require Idempotency-Key header, validate the union contract, and call Studio.start. | The transport exposes retry identity; it does not require Preview or accept ad hoc replacement permissions. |
| [`get_run`](../../src/rtagent/api.py#L226) | GET /runs/{run_id}: read RunView. | Delegate to Studio.view. | Polling/refresh is safe and independent of task scheduling. |
| [`cancel_run`](../../src/rtagent/api.py#L232) | POST /runs/{run_id}/cancel: stop local work. | Await Studio.cancel and return its saved projection. | Disconnection is not Stop; an explicit command owns cancellation semantics. |
| [`approve_run`](../../src/rtagent/api.py#L238) | POST /runs/{run_id}/approvals/{approval_id}: approve/reject one call. | Forward the URL identities and literal decision to Studio.approve. | Approval binds one pending invocation rather than updating the global tool permission map. |
| [`resume_run`](../../src/rtagent/api.py#L246) | POST /runs/{run_id}/resume: recover explicitly. | Validate per-receipt resolutions and call Studio.resume. | Unknown outcomes are reconciled through a command, never through reconnecting SSE. |
| [`trace`](../../src/rtagent/api.py#L252) | GET /runs/{run_id}/trace: inspect receipts. | Verify Run exists, fetch calls, return a labeled recursively redacted copy unless raw=true. | Original local evidence is an explicit inspection choice; default views do not overwrite receipts. |
| [`export`](../../src/rtagent/api.py#L261) | GET /runs/{run_id}/export: download evidence JSON. | Remove idempotency_key/request_hash, include Run and calls, recursively redact, label schema/redaction versions and replayable=false, and set attachment headers. | Exports are review copies, not runnable bundles; redaction may remove exact original input. |
| [`run_events`](../../src/rtagent/api.py#L288) | GET /runs/{run_id}/events: prepare SSE replay. | Prefer Last-Event-ID to after; parse an integer and reject negative/ahead-of-Run cursors with 410 before constructing the stream. | Cursor validation catches stale client state and never creates a new Run. |
| [`run_events.stream`](../../src/rtagent/api.py#L303) | Yield persistent SSE records until disconnect or terminal catch-up. | Poll pages of 200 every 150 ms, shield short database reads, write seq IDs/type/redacted JSON, and heartbeat every 40 iterations. | Durable sequences support browser deduplication; disconnect closes the reader, not execution, and shielded reads avoid half-closed async SQLite connections. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`SessionCreate`](../../src/rtagent/api.py#L47) | Empty-conversation creation input. | Optional bounded title and nullable project_id. | New conversations use a registered root without placing arbitrary workspace paths in model tool arguments. |
| [`Bootstrap`](../../src/rtagent/api.py#L54) | Browser initialization output. | Current config/presets/sessions/folders/tools/errors/root using domain contracts. | One generated typed response seeds frontend query state; credentials remain references and skill bodies are absent. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
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
