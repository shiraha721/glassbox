# Architecture and Design

Last updated: 2026-10-04. This describes implemented behavior. Future work is defined in the [delivery stages](README.md#delivery-stages).

## Read from the whole system to individual functions

This page explains the system's shape, resource ownership, data flow, and design choices. The [Architecture Reference](reference/README.md) follows those boundaries down to **every implemented module, class, named function/method, nested helper, and anonymous callback**. Each entry explains purpose, mechanism, reason, layer, architectural role, and source location. [Engineering](reference/engineering.md) also explains every declared dependency package, migration, script, and supporting test function.

| Detail to inspect | Reference |
| --- | --- |
| Data contracts, process settings, compilation, policy, redaction | [Backend core](reference/backend-core.md) |
| Composition root, Studio actions, exact HTTP/SSE handlers | [Application and transport](reference/backend-application.md) |
| Model serialization/capture, graph nodes, budgets, receipts, recovery | [Execution](reference/backend-execution.md) |
| Tables, version checks, transactions, history copies/deletion | [Storage](reference/backend-storage.md) |
| File context, memory, Skills, tool schemas/dispatch, native OS isolation | [Workspace and tools](reference/backend-workspace.md) |
| Components, query state, drafts, handlers, responsive layouts | [Frontend](reference/frontend.md) |
| Dependencies, tooling, schema upgrades, verification functions | [Engineering](reference/engineering.md) |
| Local predicates, effects, state updaters, and event closures | [Callbacks](reference/callbacks.md) |

## What the application owns

The product takes a task, composes its initial model input from visible configuration and workspace context, executes one bounded agent loop, and preserves the evidence. A conversation organizes turns; each new turn creates a Run with its own immutable input and graph thread. Prompt editing is one way to control those inputs. It does not replace the task-to-execution workflow.

The system has four concrete external boundaries: the browser talks to local HTTP/SSE; a model adapter talks to an explicitly configured endpoint; native command tools execute inside an OS sandbox; MCP talks to explicitly configured external processes/services. SQLite and ordinary context files are local persistence. Those boundaries have different permissions and lifetimes, so treating them as one universal plugin or sandbox would misdescribe the implementation.

### Actual repository layout

```text
src/rtagent/                    One installable Python package
  __init__.py                  Package version; no worker
  main.py                      Composition root, lifespan, local browser boundary, SPA
  config.py                    Process environment/paths and secret references
  api.py                       HTTP routes, validated contracts, SSE serialization
  application.py               Studio use cases and Run command coordination
  domain.py                    Strict data contracts and canonical utility values
  prompts.py                   Pure compilation, conditions, variables, source mapping
  policies.py                  Frozen-schema argument validation and allow/ask/deny
  runtime.py                   Explicit LangGraph loop, tasks, budgets, receipts/reentry
  providers.py                 LangChain adapter, prepared/captured requests, fake stream
  tools.py                     Native schemas, stateless MCP lifecycle, one-call dispatch
  workspace.py                 Guarded files and file-backed instructions/memory/Skills
  sandbox.py                   Linux bubblewrap/Bash and process-group cleanup
  windows_sandbox.py           Win32 AppContainer/PowerShell/Job Object lifecycle
  sandbox_files.py             Windows filtered copy and conflict-checked reconciliation
  storage.py                   SQLAlchemy Core tables/transactions/events/copies
  tracing.py                   Sanitized evidence copies
  skills/skill-creator/         Packaged SKILL.md resource
web/src/                       React presentation and local editor state
  main.tsx, App.tsx            Browser bootstrap and workspace coordination
  Composer.tsx                 Prompt block controls and rule drafts
  usePresetDrafts.ts           Independent bounded undo/redo histories
  useLivePreview.ts            Debounced draft compilation and stale-response handling
  Projects.tsx                 Named workspace registration and navigation
  ConversationHistory.tsx     History groups, menus, and copy/metadata dialogs
  Inspector.tsx                Preview versus actual call evidence
  Settings.tsx                 Versioned execution configuration editor
  WorkspacePanel.tsx           Memory/skill editors and sandbox inspection
  api/client.ts                Same-origin JSON transport and generated type aliases
  api/events.ts                Persistent event subscription/query invalidation
  api/generated.ts             Generated type-only OpenAPI contract
  style.css                    Visual hierarchy, fixed/scroll areas, responsive behavior
migrations/                    Application schema history, separate from checkpoints
scripts/                       Contract export and isolated real-HTTP smoke checks
tests/, web/e2e/               Backend/component/browser verification
docs/                          Project guide, system design, development/reference
```

This is the current layout. Responsibilities are modules, not all separate packages. Splitting them further requires a concrete need; the design does not assume microservices, a plugin marketplace, a vector store, or a second persistence backend.

### Layers and reasons for the boundaries

| Layer | Concrete owner | What it does / how it connects | Why this boundary exists |
| --- | --- | --- | --- |
| Presentation and local UI state | React components, usePresetDrafts, style.css | Displays saved projections and collects explicit task/editor/action input. | Drafts/selection/layout are browser concerns; authorization and effects are server concerns. |
| Transport | api.py, browser client/events | Validates/serializes HTTP contracts and reads persistent SSE; delegates actions. | Opening/reconnecting UI is a read operation, not implicit model execution. |
| Application use cases | Studio | Resolves revisions/context, freezes input, starts/approves/resumes/stops, coordinates checkpoint deletion. | Multi-step behavior has one owner independent of route rendering and graph-node internals. |
| Domain computation and policy | domain.py, prompts.py, policies.py | Represents values, deterministically compiles, validates frozen arguments, returns a configured decision. | No network/database/model SDK can make pure composition or authorization depend on hidden effects. |
| Runtime orchestration | Runner / LangGraph | Orders model/tool steps, checkpoints progress, enforces budgets, persists receipts and interrupts. | One explicit loop makes control flow and recovery visible; model adapters do not nest another loop. |
| Concrete infrastructure | providers/tools/workspace/sandboxes/config | Talks to SDKs/OS/files/transports and resolves credential references at their boundary. | The external system decides execution mechanics; the model cannot select an unrestricted host implementation. |
| Persistence and evidence | Repository, AsyncSqliteSaver, tracing | Stores application ownership/projections/receipts and separate graph progress; creates redacted copies. | Durable evidence and progress are different facts, and sanitizing a view must not rewrite originals. |
| Composition and engineering | main, migrations, export/smoke/tests/config | Owns lifetime, migrations, contract generation, and validation. | Resource startup/cleanup and development tooling do not belong in task prompts or frontend flows. |

These are responsibility layers, not a promise of a strict dependency-inverted framework. Studio directly calls the concrete Runner/Repository/ToolRegistry and provider preparation functions. Simple metadata routes delegate to Repository when no additional use case is needed; complicated execution/deletion goes through Studio. Domain/compiler/policy depend only on validated data and local computation. The entry point wires implementations and owns their lifetime. Detailed references identify the actual imports/effects, including these practical cross-layer calls.

### State and authority

| State | Authority / location | Lifetime and architectural role |
| --- | --- | --- |
| Process settings | Settings, environment/.env | Paths and startup overrides for one service process; not model-editable. |
| Current execution settings | settings row, StudioConfig revision | Edited explicitly; new Runs freeze a copy. Contains references, not actual secrets. |
| Current preset | presets row, embedded Block values/revision | Editable aggregate; old Run copies never follow later edits. |
| Project registration | projects and sessions.project_id | One existing absolute folder per Project; null sessions use the process default. Future Runs bind to session membership. |
| Live draft inspection | useLivePreview and POST /prompts/inspect | No preset/session/preview writes; retain previous valid output and abort superseded requests. |
| Successful history | sessions.messages | Only succeeded turns become subsequent model input; all other turns remain inspectable separately. |
| Prepared input | previews JSON with 15-minute expiry | Same compiler path for optional inspection and direct Run; one consumption per preview. |
| Run snapshot | runs.snapshot | Original configuration/history/task/schema/context/root/body, immutable through the Run and recovery. |
| Runtime projection | runs messages/status/approval/usage/partial output | Browser-visible current state; updated alongside numbered events. |
| Effect receipts | calls table | Intent/result/uncertainty survives node replay and separately records what is known about dispatch. |
| Graph position | checkpoints.db, thread_id=run_id | LangGraph's node state; original Run only, never cloned by duplicate/fork. |
| Instructions/memory/Skills | Ordinary workspace/package files | Initial text is snapshotted; later explicit reads/writes are new recorded tool effects. |
| UI drafts/navigation | Component state and selected session ID | Page-local editing/selection; not execution authority or durable model history. |

The model can propose a ToolCall, not change this ownership. File/tool/model text is data. The server matches its original ID, validates against frozen schemas, enforces local allow/ask/deny and adapter boundaries, then returns matching evidence. An ask decision is a persisted human action for one invocation. Credential references are frozen, while their actual environment values resolve when the provider/MCP call executes; snapshots do not preserve old secret values.

### Resource lifetime and concurrency

`main.create_app` registers behavior without opening runtime stores. Its lifespan resolves directories, migrates/seeds Repository, applies explicit startup overrides, constructs ToolRegistry and Studio/Runner, then opens the checkpoint saver/graph and classifies queued/stranded work. Normal shutdown stops/gathers Run tasks before closing checkpoints, then disposes application connections.

Studio's command lock sequences start/approval/resume/cancel/deletion commands within this process. Repository's writer lock protects short SQLite transactions; it is not held across model/tool execution. Runner's semaphore allows one graph invocation at a time, across conversations. A waiting approval ends that invocation and releases the slot, while its conversation remains reserved by active_run_id. Workspace's RLock serializes native guarded file replacements and Windows reconciliation, separate from database locks. Linux subprocesses use async I/O; blocking file/Win32 operations run off the event loop. One Uvicorn worker is the supported ownership model.

The application does not claim atomicity across model services, workspace edits, app.db, and checkpoints.db. Receipts/checkpoint reuse and explicit uncertainty are how it recovers when those boundaries cannot commit together. Stop awaits local cleanup; it cannot undo a completed file write or remote effect.

## Application shape

One local FastAPI process owns one LangGraph worker, SQLite persistence, root-bound concrete workspace tools, and the React Web UI. Each responsibility is a module, without generic service interfaces, brokers, or a plugin runtime.

```mermaid
flowchart TD
    UI[React workspace] -->|HTTP / SSE| API[FastAPI]
    API --> APP[Studio use cases]
    APP --> FILES[Instructions / memory / Skills]
    APP --> COMP[Pure compiler]
    COMP --> SNAP[Immutable input snapshot]
    SNAP --> RUN[LangGraph model / tool loop]
    RUN --> MODEL[Chat Completions adapter]
    RUN --> POLICY[Schema and permission checks]
    POLICY --> TOOLS[Native tools / MCP]
    TOOLS --> SHELL[Windows AppContainer / Linux bubblewrap]
    TOOLS --> FILES
    APP --> DB[(SQLite)]
    RUN --> DB
    RUN --> CP[(Checkpoints)]
    DB -->|Numbered events and saved state| API
```

| Module | Responsibility |
| --- | --- |
| main.py, config.py | Startup, migrations, resource ownership, loopback boundary, static UI |
| api.py, application.py | HTTP contracts; resolve context/revisions, start/approve/cancel/resume Runs, workspace editing |
| domain.py, prompts.py | Pydantic contracts; pure ordering, activation, substitution, sources, estimates and hashes |
| providers.py | Canonical mapping, prepared requests, wire capture, real or explicit simulated streaming |
| runtime.py | Bounded graph, checkpoints, receipts, interrupts and recovery |
| policies.py, tools.py | Frozen schemas/permissions, native dispatch, stateless MCP lifecycle |
| workspace.py, sandbox.py, windows_sandbox.py, sandbox_files.py | File context, memory, Skills, guarded edits, native Windows/Linux isolation and Windows file reconciliation |
| storage.py, tracing.py | Transactions, revisions, events, traces, redaction and export |
| web/src/ | Conversation, Prompt, Inspector, Workspace and Settings, generated API types |

Domain/compiler/policy logic does not depend on HTTP or storage. The application reads workspace files before pure compilation. Blocking file operations run in a worker thread; model/MCP/subprocess calls use async I/O. The entry point closes tasks, checkpoints, and database resources.

## One task through the concrete functions

```mermaid
sequenceDiagram
    participant UI as Workspace.start
    participant API as api.start_run
    participant APP as Studio
    participant DB as Repository
    participant FILES as Workspace files
    participant COMP as Compiler/provider preparation
    participant RUN as Runner
    UI->>API: Direct PreviewRequest + Idempotency-Key
    API->>APP: start(request, key)
    APP->>DB: submitted_run(key, original submission)
    alt Identical accepted retry
        DB-->>APP: Original Run
    else New direct submission
        APP->>DB: Read session/preset/config revisions
        APP->>FILES: Capture instructions/memory/catalog/selected skills
        APP->>COMP: compile_prompt + prepare (no model request)
        APP->>DB: put_preview + create_run (validate and reserve)
        DB-->>APP: Queued Run and event sequence 1
    end
    APP->>RUN: start(run_id) if queued
    APP-->>API: view(run_id)
    API-->>UI: 202 RunView
    RUN->>DB: Model/tool receipts + projection/events
    UI->>API: GET events and saved Run/trace state
    API-->>UI: Numbered SSE and current projections
```

HTTP acceptance is separate from completion. The background graph uses the saved Run even if the browser closes. UI start keeps a submission key only until it has an accepted response; a lost response can be retried unchanged. Browser Preview uses `Studio.inspect` with an inline unsaved preset and optional project/session references. `Studio._prepare` shares file capture, compilation, and provider preparation with execution; inspection never creates persistent input records. The API also retains `Studio.preview` / `StartRun` for clients explicitly consuming a saved immutable preview. Direct Run always captures current saved input independently.

### Each model/tool cycle

| Step | Concrete functions | State/effect and reason |
| --- | --- | --- |
| Reconstruct model input | Runner.model_node → compile_prompt → prepare | Frozen history/context/config plus current Run delta; first body must equal saved input. |
| Record/send one request | Repository.save_call → generate/capture → on_wire | Persist intent and application body before transmission; no SDK retry. |
| Stream/save response | Repository.delta / save_call / emit | Partial text is persistent; full response/call IDs/usage are recorded before graph progress. |
| Choose graph route | Runner.open conditional callbacks | No pending tools means completion; otherwise execute the pending batch in order. |
| Check one intent | Runner.tool_node → decide | Frozen schema/permission; invalid/denied calls receive matching errors instead of unauthorized effects. |
| Pause if required | interrupt → Studio.approve → Runner resume | Human decision precedes dispatch and survives restart/reentry. |
| Dispatch real tool | save_call(executing) → ToolRegistry.execute | File adapter/native OS boundary/MCP schema check owns the concrete effect. |
| Preserve outcome | save_call(result) → paired tool Message → emit | Matching original tool_call_id, reusable receipt, or explicit outcome_unknown before any retry. |
| Commit successful turn | Runner.drive → Repository.finish | Append one task plus delta to history and release session in one transaction. |

### Run states and user actions

```mermaid
stateDiagram-v2
    [*] --> queued: accepted input
    queued --> running: worker slot
    running --> waiting_approval: ask before effect
    waiting_approval --> queued: explicit approve or reject
    running --> interrupted: restart or unknown outcome
    interrupted --> queued: explicit reconciliation/resume
    running --> succeeded: model returns no calls
    running --> failed: known failure or budget
    queued --> cancelled: Stop
    running --> cancelling: Stop
    cancelling --> cancelled: local cleanup awaited
    waiting_approval --> cancelled: Stop
    interrupted --> cancelled: Stop
```

Terminal means succeeded, failed, or cancelled. Waiting/interrupted keeps the conversation active and prevents archive/copy/delete until resolved or cancelled. A denied tool can still lead to a succeeded Run because the model receives a paired error and chooses a final response; tool permission outcome and Run completion are different facts. Shutdown keeps pending human approvals; abandoned dispatched calls become uncertain. Reading history or reconnecting events makes no state transition in this diagram.

### Other vertical workflows

| User action | Concrete path | Result / design reason |
| --- | --- | --- |
| Edit and save a preset | Composer → usePresetDrafts → Workspace.savePreset → save_preset | Local undoable content becomes a new aggregate revision; snapshots remain immutable. |
| Change settings/discover MCP | Settings.save → Studio.save_config / ToolRegistry.discover | New config revision, explicit fresh discovery, tools still disabled until allowed/asked. |
| Save note/procedure | MemoryEditor/SkillEditor → Studio → Workspace.write/save_skill | Same hash/file-format checks as model tools, with Preview invalidated. |
| Duplicate/fork | History.copy or turn fork → Repository.copy_session | New owned evidence IDs/provenance, successful prefix history, zero model/tool replay. |
| Rename/move/archive | History.update → Repository.update_session | Revisioned metadata; archive requires idle, execution requires restoration. |
| Delete folder | History.submit → Repository.delete_folder | Members become Unfiled and their revisions advance; history remains. |
| Delete conversation | History.submit → Studio.delete_session → checkpointer/Repository | Idle-only owned checkpoint/evidence cleanup; independent copies remain. |
| Inspect/export | Inspector → trace/export handlers → redact | Canonical/prepared/wire/results are distinct; original local view is explicit, export is labeled non-replayable. |

## Direct Run and optional Preview

POST /api/v1/runs accepts a direct task:

```json
{
  "session_id": "existing-session-id",
  "expected_session_revision": 0,
  "preset_id": "existing-preset-id",
  "preset_revision": 1,
  "config_revision": 1,
  "input": "Inspect the project and fix the startup error.",
  "variables": {},
  "skills": []
}
```

Supply an Idempotency-Key header. A retry is resolved by key and the original submission fingerprint before revision checks. A new direct submission resolves the session/preset/settings, captures workspace context, compiles, and internally persists the frozen input. Run creation and session reservation are transactional. No UI preview is required.

POST /prompts/preview accepts the same body and returns messages, source map, activation report, estimate, prepared payload, and hash. To use that inspected input, send preview_id, input_hash, and expected_session_revision to /runs. A preview expires after 15 minutes and can create at most one Run. Stale preset/config/session revisions fail; changed workspace files do not mutate an existing input snapshot.

Both paths snapshot the preset, config, schemas, history, input, variables, initial workspace context, root path, and prepared request. Round zero must serialize to that request. Later rounds compile the same snapshot plus assistant/tool messages. They are not individually approved previews.

Idempotent retry returns the original Run even after session revision changes; a different body with the same key returns 409. The UI retains a key across a failed-response retry of unchanged input. Refresh and SSE reconnect only retrieve existing state.

## Prompt composition

Blocks support system/user/assistant roles, enabled state, placement/order, provider/model filters, declared variables, and a condition. Presets own embedded blocks and one revision; there is no separate block-version framework.

Message order:

1. Active before_history blocks sorted by order.
2. Frozen root instructions, memory, skill catalog, and explicitly selected skill bodies.
3. Committed successful conversation history.
4. Active before_input blocks sorted by order.
5. Current user input exactly once.
6. This Run's assistant/tool messages.

Duplicate block IDs anywhere in a preset and duplicate orders within a placement fail. Every block retains its inclusion/exclusion reason; every message has a source. Workspace text is literal, not a template.

Variables use declared names such as {{task_type}}, with string/number/boolean types and explicit defaults. No code evaluation, attribute access, filesystem access, or arbitrary template execution occurs. Conditions support all/any/not and field/op/value with eq/in/exists. Fields are provider, model, task_type, tools, or one variables.name. Invalid or missing operands fail.

Estimates use serialized bytes divided by four. A configured context window reserves output and rejects estimated overflow; unknown context remains unverified. There is no implicit trimming. Actual provider usage is separate and unknown values stay null.

## Workspace context, memory, and Skills

STUDIO_WORKSPACE_ROOT is the default process root. A trusted user can register an existing absolute folder as a Project; sessions store nullable project_id. Studio resolves that ID for inspection and new Runs, captures the root and project ID, and uses ToolRegistry.for_workspace to bind native file adapters and OS sandboxes. Runner dispatches from the frozen Run root, including after restart or a conversation move. Current catalog/error discovery is shared; explicit MCP transports remain outside native command isolation. Project roots are fixed registrations, not model-editable tool parameters. Changing an idle session’s Project advances its revision; active moves fail. Historical snapshots and copied wire evidence do not change.

Within the selected root, root AGENTS.md and .rtagent/MEMORY.md are captured with sources workspace:AGENTS.md and workspace:memory. The catalog source is workspace:skills; selected bodies use skill:<name>. Context is frozen once per Run and is not persisted as repeated conversation turns.

Memory is explicit Markdown, not hidden summarization. Reads return text/hash; save_memory requires that hash. Atomic replacement avoids partial files; stale hashes reject concurrent native edits. Shell/external edits are outside this lock, so this is not a transaction across host processes. Changes become initial context on the next Run; a current Run can read updated files explicitly and record the tool result.

Skills are discovered under .agents/skills/<name>/SKILL.md plus the packaged skill-creator. YAML frontmatter requires a matching lowercase hyphenated name and a description. Only metadata enters the catalog. load_skill, explicit Prompt/Workspace selection, or $skill-name loads the body. Invalid entries are reported; discovery is bounded to 100 entries. Workspace skills cannot shadow bundled skills.

save_skill writes validated frontmatter and Markdown instructions; updates require the current hash. The bundled creator uses those tools to author and verify a focused reusable workflow. Supporting resources remain ordinary files. Skills cannot grant permissions, load host plugins, or bypass the sandbox.

## Tools and OS isolation

The model receives frozen schemas selected from the configured permission map and available catalog. Read-only mode excludes native mutation tools. Each call is JSON-schema checked and evaluated as allow, ask, or deny before effects occur. Prompt, memory, Skill, and tool-result text cannot broaden permissions.

Native tools: list_files, read_text, write_text, edit_text, powershell (Windows) or bash (Linux), read_memory, save_memory, load_skill, save_skill, and the side-effect-free approval check echo.

File tools require relative paths and reject traversal, links/junctions, credential paths, Git metadata, runtime data, and dependency directories. UTF-8 files and serialized outputs are bounded. Replacement uses a content hash; edits require one exact match. These application checks assume a non-hostile concurrent host environment.

WindowsSandbox launches the built-in Windows PowerShell in a new AppContainer for each call. CreateProcessW receives SECURITY_CAPABILITIES with no capabilities, so Windows denies network access. An explicit inherited-handle list contains only stdin/stdout/stderr. The process starts suspended, is assigned to a kill-on-close Job Object with a 32-process limit, then resumes. Completion, timeout, and cancellation terminate the whole Job; the server waits for zero active processes before copying results back. Blocking Windows calls run in a worker thread, with cancellation signalled by an event and cleanup awaited by the runtime.

A filtered private Windows workspace copy receives the AppContainer SID's read/execute or modify ACL. Scratch space is writable. Original project, user, and system ACLs remain unchanged. Excluded credentials never enter the copy, so filename case aliases cannot expose them. After cleanup, changed files are copied back with preflight content-hash checks against concurrent host edits. This is not a multi-file atomic transaction. Copies are bounded to 64 MiB and 10,000 entries; links, hard links, aliases, and special files are rejected. PowerShell gets a Workspace: drive rooted at the copy; this avoids granting directory traversal/listing rights on host drive roots. AppContainer retains normal access to shared Windows resources; it is not a VM or a promise to hide every public file. Git Bash's MSYS runtime is not supported in this AppContainer; Windows commands use PowerShell syntax.

BashSandbox runs only on Linux. Bubblewrap creates private mount/process/network namespaces, binds the project at /workspace, exposes read-only system binaries/libraries and private scratch storage, clears the environment, and masks excluded files. A trusted setsid driver reports its process group before untrusted output. Timeout/Stop kills that group; bubblewrap's parent-death behavior cleans up namespace children.

Both paths use explicit host process arguments, bounded retained output, no inherited credentials, and cleanup before returning a result. Only the command supplied to the chosen shell is executable script. Results include stdout/stderr, exit code, timeout/truncation, and sandbox metadata. Timeout can leave partial edits. Missing OS support fails without unrestricted fallback. Startup upgrades the old Windows bash permission to powershell, preserving allow/ask/deny and creating a new settings revision; existing Run snapshots are unchanged. Resuming an old Bash call on Windows reports that its tool is unavailable.

Network allowlists, interactive terminals, background jobs, disk quotas, and automatic import of host development toolchains remain future work. See [AppContainer isolation](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer) for the Windows boundary.

MCP is outside the command sandbox: explicitly configured host processes or HTTP services use fresh SDK sessions per discovery/call. Namespaced schema hashes are checked before execution. Changed schemas stop the call, credentials use environment references, and new tools default to denied. Stateful analysis sessions are deferred.

## Runtime, recovery, and provider requests

The explicit graph is model → policy/tools → model; no calls means completion. One worker runs one graph invocation at a time; approval interrupts release it. Steps, tool counts, deadlines, and output bounds limit execution. Every original tool_call_id gets one success/error result before the model continues.

thread_id equals run_id. New turns create new Runs; recovery resumes the original checkpoint/snapshot. Reentrant nodes reuse known receipts; uncertain effects are never retried implicitly. Approvals precede effects. Executing calls interrupted by failure/cancellation become outcome-unknown. Recovery requires retry or a verified tool result; model calls can only be retried. Previous attempt evidence remains. Stop awaits cleanup but does not undo completed local/remote effects. Only successful turns enter history.

OPENAI_BASE_URL, OPENAI_MODEL, and STUDIO_PROVIDER override saved configuration at startup when present. API keys resolve on the server at execution and do not enter snapshots/frontend state. Settings changes apply to new Runs immediately, until explicit environment settings win again after restart.

The real Chat Completions adapter is default; a blank model fails clearly. Explicit simulation records no wire request. LangChain maps canonical roles/IDs; a prepared body is compared with the actual HTTP request before transmission. Role changes are rejected. Retries/provider fallbacks are disabled. Every call records canonical messages, prepared/captured application-level bodies, sources, transformations, metadata and usage. Provider-internal additions are not visible.

## Persistence, API, and UI

SQLite tables: settings, presets, conversation_folders, sessions, previews, runs, events, calls. Alembic manages these; LangGraph manages its separate checkpoint database. Memory/Skills are files and need no business-table migration. UTC timestamps, stable IDs, and schema versions identify records.

A state projection and its numbered event commit together. Successful completion appends history and releases the session together. A session has one active Run. Settings use optimistic revisions; Run snapshots remain immutable. SSE sequence deduplication and active-state polling keep the UI current.

Actual routes under /api/v1:

| Group | Behavior |
| --- | --- |
| /health, /bootstrap | Availability, saved config/presets/sessions/folders/projects, tool catalog |
| /config, /presets/{id}, /tools/refresh | Versioned edits and explicit discovery |
| /workspace, /workspace/memory, /workspace/skills | Selected-project sandbox/instructions/memory/Skills through optional project_id; /workspace/skills/{name} loads a body |
| /projects | Register an existing named absolute workspace folder; membership is part of session metadata |
| /sessions, /sessions/{id}, /sessions/{id}/runs | Conversations, versioned metadata edits, deletion, and saved Run history |
| /sessions/{id}/copy | Duplicate all completed records or fork through a successful Run |
| /folders, /folders/{id} | Create, rename, or delete flat conversation folders |
| /prompts/inspect, /prompts/preview, /runs | Nonpersisting live draft inspection, optional saved preview, or direct execution |
| /runs/{id} subresources | State, cancel, approval, recovery, events, trace, export |

Generated /openapi.json and frontend types define exact contracts. The UI opens Prompt, Inspector, and Workspace only when needed. Run is enabled for nonempty input without a preview. Draft changes invalidate optional previews. File editor saves use hashes.

Conversation metadata includes archive state and optional folder membership. Renames and moves use observed session revisions; explicit titles survive the first Run. Archiving requires an idle conversation, and the server rejects execution until restoration. Folder deletion clears membership and advances affected revisions without deleting history. Schema migration adds columns in place so SQLite foreign-key cascades cannot discard existing Runs.

Duplicate/fork is a single business-data transaction. Duplicate retains all terminal Runs and committed history. Fork requires a successful Run boundary and rebuilds committed messages from successful turns through that point. Both assign new session, Run, and receipt IDs and preserve immutable source snapshots and request evidence. Run source_run_id and receipt provenance identify copied history; a new run.copied event records the copy instead of replaying original SSE events. Checkpoints are not copied and historical effects cannot be resumed or executed by copying. Source IDs are provenance strings without foreign-key ownership, so deleting a source leaves its copies intact. Subsequent turns use new Runs with the copied history and the current shared workspace/configuration.

The UI renders all turns in one scroll region under a fixed history toolbar and above a fixed message dock. The sidebar has separate fixed search/filter controls and a scrolling folder/history list. Prompt controls and save/discard actions stay fixed while grouped message blocks scroll. Each preset has a page-local draft with bounded, reversible snapshots; switching preserves edits and saving updates the revision baseline. Menu actions derive state from the latest session projection even while a menu remains open across Run completion.

The app binds to loopback without login/access tokens. TrustedHost and Origin/Sec-Fetch-Site checks reject foreign browser requests; local native clients can call directly. This is not remote deployment authentication. Original traces are explicit local views; exports are labeled redacted copies.

## Acceptance and next stages

Run the service and check actual behavior before growing tests. scripts/smoke_workspace.py starts a temporary HTTP server and exercises real OS effects in a disposable workspace. Focused checks protect compilation, pairing, request capture, snapshot consistency, recovery, and confidentiality. Browser checks cover direct Run, optional preview, approvals, cancellation, history, settings, and workspace editing.

scripts/smoke_conversations.py checks management through a separate real HTTP server: copy boundaries, trace provenance, stale revisions, archive/restore, folder operations, and continuation after source deletion. Browser checks exercise preset switching/undo and the complete conversation-menu workflow.

A basic live-model/native-PowerShell loop has passed on Windows; substantial coding/analysis acceptance is still required. Next stages add long-session context handling, diff review and command/process usability, then CLI/RPC/SDK and focused extension hooks. Specialized security integrations and evaluation follow real workflows. Multi-agent orchestration, more workers, PostgreSQL, and remote teams are not prerequisites for a useful local harness.

## Stage 2 acceptance

The Windows local-agent boundary is accepted through substantial real-model work in a registered project, native sandbox checks, per-project context/file checks, browser workflows, and existing-data migration checks. See [Stage 2 Acceptance](acceptance-stage2.md) for tasks, recorded call counts, verification commands, and unimplemented later-stage scope. This is not a claim that excluded host runtimes or network dependencies are available inside AppContainer.
