# Dependencies, migrations, scripts, and verification functions

This reference covers the supporting parts of the same repository, not additional runtime services. Tests and smoke scripts provide evidence for behavior; they do not define product capabilities that are absent from production code. Documentation-only verification checks links and source coverage without starting the application.

[Reference index](README.md) · [System architecture](../architecture.md)

## Package and dependency roles

The installable backend package is **rtagent**, built from src/rtagent by Hatchling. [__init__.py](../../src/rtagent/__init__.py) contains a package docstring and version constant; it has no functions or import-time worker. All actual backend boundaries are single `.py` modules, not the hypothetical api/application/domain subpackages in the target-layout development guide. `skills/skill-creator` is packaged text, not another Python package. The browser npm package is **security-agent-studio-web**; its src/api directory holds transport code and generated type contracts.

Dependency declarations are [pyproject.toml](../../pyproject.toml) and [web/package.json](../../web/package.json); resolved versions are in uv.lock/package-lock.json. The tables explain every declared direct dependency and the additional transitively supplied packages directly imported by first-party code. They do not claim to document third-party internal functions or every transitive lock entry.

### Backend runtime dependencies

| Package | Used by / architectural layer | How and why it is used |
| --- | --- | --- |
| fastapi | main/api; HTTP adapter. | Router, validation, OpenAPI, lifespan, HTTP responses; business decisions remain in Studio/Repository/runtime. |
| uvicorn[standard] | main and smoke/browser server launch; host. | ASGI server with loopback entry point; standard extras supply platform-specific server conveniences, not another service. |
| pydantic-settings | config; composition. | Typed process environment/.env settings kept separate from versioned StudioConfig. |
| python-dotenv | config; secret boundary. | Read server-side .env references on demand; never serialize resolved values into config JSON. |
| sqlalchemy[asyncio] | storage/migration host; persistence. | Core table/select/transaction engine with async application I/O; no ORM model hierarchy is needed. |
| aiosqlite | storage/checkpoint driver; persistence. | Async SQLite access under the application/checkpointer lifecycles without blocking FastAPI's event loop. |
| alembic | storage initialize and migrations; schema lifecycle. | Explicit versioned upgrades/downgrades to existing app.db; independent of SDK checkpoint tables. |
| langgraph | runtime; orchestration. | StateGraph, explicit model/tools nodes, interrupt/Command resume, sync checkpoint durability. It controls the only agent loop. |
| langgraph-checkpoint-sqlite | runtime; recovery infrastructure. | AsyncSqliteSaver for graph progress and public thread deletion; no first-party checkpoint-table SQL. |
| langchain-openai | providers; model infrastructure. | ChatOpenAI message streaming/Chat Completions serializer; no nested prebuilt agent. |
| mcp | tools and fixture; tool infrastructure. | ClientSession/stdio/Streamable HTTP SDK lifecycle, tool schema discovery/calls; fixture FastMCP for offline contracts. |
| httpx | providers/tools/scripts/tests; HTTP infrastructure. | Async model/MCP HTTP, pre-send request hook, real local smoke requests, and mocked/ASGI test transports. |
| jsonschema | policies; domain policy. | Draft202012 argument validation against the frozen advertised schema before permission dispatch. |
| anyio | api; transport resource boundary. | Shield short database reads inside SSE cancellation handling; SDKs also use async task groups transitively. |
| pyyaml | workspace; file adapter. | safe_load/safe_dump of the minimal skill frontmatter; no arbitrary YAML object execution. |
| pydantic (transitive, directly imported) | domain/config/api; contracts. | BaseModel/Field/validators for strict input/output, revalidation, and JSON Schema. It is supplied by declared dependencies rather than separately declared here. |
| starlette (transitive, directly imported) | main; HTTP boundary. | TrustedHostMiddleware bounds accepted loopback hostnames without adding a login system. |
| langchain-core (transitive, directly imported) | providers; canonical-to-SDK mapping. | System/Human/AI/ToolMessage and streaming chunk combination preserve roles/tool IDs. |
| langsmith (transitive, directly imported) | runtime; trace boundary. | tracing_context(enabled=False) prevents remote tracing around execution; evidence stays in the local repository. |

### Backend development/build tools

| Package / tool | Layer and use | Reason |
| --- | --- | --- |
| pytest | Verification discovery/assertions/fixtures/parameterization. | Test actual compiler/runtime/storage/API behavior offline. |
| pytest-asyncio | Async verification. | Auto asyncio mode supports concrete coroutine integration checks and fixture lifetimes. |
| ruff | Static verification. | E4/E7/E9/F/B/UP linting; import sorting deliberately remains with isort. |
| black | Source formatting. | Shared 100-column style reduces formatting-only review noise. |
| isort | Import formatting. | Black profile and 100-column width; avoids competing Ruff import rules. |
| hatchling | Wheel build backend. | Package src/rtagent and its resources without adding runtime behavior. |
| uv (external dependency manager) | Environment/lock/commands. | uv.lock captures resolved Python dependencies; sync --locked installs their recorded resolution. |

### Frontend runtime dependencies

| Package | Layer / implementation | Why |
| --- | --- | --- |
| react | Presentation/state. | Components/hooks and declarative view composition; state ownership remains explicit per feature. |
| react-dom | Browser mounting/portals. | Root rendering and menus/dialogs outside sidebar overflow clipping. |
| @tanstack/react-query | Server-state synchronization. | Cache keyed saved projections, focus/poll refetch, and event invalidation; avoids duplicating backend Run storage in browser state. |
| lucide-react | Presentation. | Consistent icons for operational controls, accompanied by text/accessibility labels. |
| react-markdown | Evidence presentation. | Render assistant text with raw HTML skipped and external images replaced by inert labels. |

### Frontend development dependencies

| Package | Use / layer | Why |
| --- | --- | --- |
| @eslint/js | Static checks. | Base JavaScript recommended rules for config and source. |
| eslint | Static checks. | Runs the configured frontend lint pipeline. |
| typescript-eslint | Static checks. | TypeScript-aware rules and flat-config composition. |
| eslint-plugin-react-hooks | React state checks. | Hook placement/dependency checks guard rendering/state lifecycle mistakes. |
| eslint-plugin-react-refresh | Development export checks. | Warn about module export shapes incompatible with reliable Fast Refresh. |
| globals | Static environment declarations. | Browser and Node globals for source/build configuration. |
| typescript | Type verification/build. | Strict checking of components, tests, configuration, and generated contracts before Vite build. |
| @types/node | Tool/config type declarations. | Node filesystem/path/process types for Vite/Playwright configuration. |
| @types/react | UI type declarations. | Component props/hooks/JSX compile-time contracts. |
| @types/react-dom | Mount/portal declarations. | Typed createRoot/createPortal use. |
| vite | Development/bundle tooling. | Local frontend dev server/proxy and production web/dist assets served by FastAPI. |
| @vitejs/plugin-react | React build integration. | JSX transform and development refresh in Vite. |
| vitest | Component verification. | Small unit cases for Composer interactions; shares Vite configuration. |
| jsdom | Component-test DOM. | Browser-like document for React unit checks without a real browser. |
| @testing-library/react | Component interaction verification. | Render/fireEvent/accessibility queries test user-visible editor behavior. |
| @testing-library/jest-dom | Optional assertion tooling. | Declared DOM matcher package; current Composer tests use ordinary Vitest assertions and do not import it. |
| @playwright/test | Browser acceptance. | Isolated backend/Chromium UI workflows and retained failure traces; not production runtime. |
| openapi-typescript | Contract generation. | Convert exported FastAPI schemas to checked-in generated.ts instead of hand-maintaining DTO duplicates. |
| prettier | Frontend/document syntax formatting tool. | Current npm format scripts target source/e2e/config/index files; Markdown documentation is not in their target list. |
| npm (external dependency manager) | Lock/install/scripts. | package-lock.json and npm ci preserve resolved browser/tool dependencies. |

## Configuration files with no functions

| File | Architectural role / behavior | Why it lives here |
| --- | --- | --- |
| [pyproject.toml](../../pyproject.toml) / [uv.lock](../../uv.lock) | Python metadata/entry point/dependencies, dev group, formatter/test rules; resolved dependency graph. | Reproducible backend environment and an explicit command surface. |
| [.env.example](../../.env.example) | Non-secret process/provider reference template. | Local credentials live in ignored .env; placeholders can be committed without embedding user values. |
| [alembic.ini](../../alembic.ini) | Migration script location and default database URL. | Startup/CLI share one app-schema upgrade path. |
| [web/package.json](../../web/package.json) / [package-lock.json](../../web/package-lock.json) | npm scripts/declarations/resolved graph. | Build/lint/typecheck/test/API generation are supported actual commands. |
| [vite.config.ts](../../web/vite.config.ts) | React plugin, /api proxy to port 8001, jsdom test inclusion. | Vite dev UI uses the same backend transport boundary as the production SPA. |
| [playwright.config.ts](../../web/playwright.config.ts) | Creates a unique var/e2e-* workspace/data root, copies safe fixture README and literal AGENTS.md, runs one fake-model server at 8765 and one worker. | Real browser checks cannot alter the user's workspace/history or accidentally call a model. |
| [eslint.config.js](../../web/eslint.config.js) | Flat recommended JS/TS/hook rules with generated/dist/dependency/report ignores. | Generated contracts and bundles are not hand-authored source lint targets. |
| [tsconfig.json](../../web/tsconfig.json) | One strict noEmit configuration for src, e2e, and root TypeScript config; browser/Node/Vitest types and unused-symbol checks. | The existing app/test/tooling files share explicit compiler rules without fabricated project-reference files. |
| [.prettierrc.json](../../web/.prettierrc.json) | Frontend formatter choices. | Consistent source/config output independent of backend formatters. |
| [.gitignore](../../.gitignore) | Excludes secrets/runtime/dependencies/local notes and generated intermediate schema. | Source/lockfiles/contracts can be committed while private operational data remains local. |

## Application migrations

[migrations/env.py](../../migrations/env.py) is an Alembic entry script with no named functions. It takes an explicit configured URL or resolves the default from Settings, creates the data directory when needed, and uses offline literal SQL or a synchronous engine transaction. Schema mutations are migration work, not HTTP request behavior.

## migrations/versions/0001_initial.py

**Layer: Engineering / application schema migration.** Creates the initial application tables/indexes using revision-local SQL rather than importing mutable current metadata.

**Connections and effects:** Executed by Alembic before Repository seeds data. The historical login table exists only in this revision; current head removes it in 0002.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`upgrade`](../../migrations/versions/0001_initial.py#L11) | Install the first application schema. | Execute fixed CREATE TABLE statements for settings/presets/sessions/previews/runs/events/calls/historical browser_sessions plus Run-session/call-Run indexes. | Historical migrations must remain stable when current code evolves; explicit FKs/unique keys preserve ownership/idempotency. |
| [`downgrade`](../../migrations/versions/0001_initial.py#L28) | Remove the first schema in dependency-safe order. | Drop dependent login/call/event/Run/preview/session/preset/settings tables. | Reverse migration describes schema rollback, not an application delete-conversation API or automatic data recovery. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def upgrade() -> None
def downgrade() -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## migrations/versions/0002_remove_browser_sessions.py

**Layer: Engineering / application schema migration.** Removes obsolete login persistence from the local single-user product.

**Connections and effects:** Applied after 0001; it touches neither conversations nor Runs. Current head contains no browser_sessions table.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`upgrade`](../../migrations/versions/0002_remove_browser_sessions.py#L11) | Drop obsolete browser login state. | Use op.drop_table(browser_sessions). | The product opens directly without accounts/access tokens; unused authentication storage should be removed rather than retained behind a switch. |
| [`downgrade`](../../migrations/versions/0002_remove_browser_sessions.py#L16) | Restore the prior version's expected empty table. | Execute fixed CREATE TABLE for id/csrf/expiry. | Historical schema compatibility is explicit; this does not enable current authentication code or reconstruct old session contents. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def upgrade() -> None
def downgrade() -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## migrations/versions/0003_conversation_management.py

**Layer: Engineering / application schema migration.** Adds flat organization, archiving/title policy, and copy provenance to existing histories.

**Connections and effects:** Uses in-place SQLite additions rather than rebuilding referenced sessions/Runs, preserving cascade-owned data during upgrade.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`upgrade`](../../migrations/versions/0003_conversation_management.py#L11) | Extend current conversations and Runs without copying their history tables. | Create folders, ALTER ADD folder/archive/title-lock/copy/source fields, and lock already explicit titles. | Rebuilding sessions under cascading FKs could discard owned Runs; in-place metadata changes preserve the user's execution evidence. |
| [`downgrade`](../../migrations/versions/0003_conversation_management.py#L29) | Remove added metadata while retaining core history. | Drop Run provenance, then session metadata columns, then folders using SQLite 3.35+ DROP COLUMN. | Rollback is limited to this revision's organization features, not all saved Runs/messages. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def upgrade() -> None
def downgrade() -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## Contract export and live verification scripts

[scripts/export_api.py](../../scripts/export_api.py) has no named functions: it constructs the FastAPI factory, obtains OpenAPI, explicitly adds RunEvent schema, and writes ignored web/src/api/schema.json. It does **not** enter lifespan, start a server, migrate data, or call a model. `npm --prefix web run generate:api` then writes checked-in generated.ts. Source-only schema generation differs from runtime application initialization.

## scripts/smoke_workspace.py

**Layer: Engineering / live local acceptance.** Checks the actual HTTP/SQLite/graph/native OS path in a disposable workspace with explicitly simulated model decisions.

**Connections and effects:** Owns port 8766 and temporary var data; actual commands/files/network probes are real. It is not a paid/live-model quality test and does not target the user's selected workspace.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`command`](../../scripts/smoke_workspace.py#L25) | Select the host's concrete command syntax. | Return supplied PowerShell text on Windows or Bash text elsewhere. | Acceptance must exercise native adapters without treating Bash as a Windows requirement or translating model code. |
| [`assert_windows_process_stopped`](../../scripts/smoke_workspace.py#L30) | Prove a spawned child is no longer running. | Open a synchronize handle, check zero-time wait; allow a genuinely nonexistent PID error, close the handle. | Pipe EOF/root exit cannot prove tree cleanup; the check independently validates the Job behavior. |
| [`exercise`](../../scripts/smoke_workspace.py#L52) | Verify native direct Runs and workspace capabilities through public HTTP. | Probe sandbox; run command/file/memory/skill calls; verify key reuse, hidden host paths/variables, local-network block, child kill, context sources, timeout/cancel, and read-only effects. | A vertical real service/tool check complements isolated unit contracts and exposes startup/sandbox integration failures. |
| [`exercise.api`](../../scripts/smoke_workspace.py#L55) | Send one HTTP command/query and validate success. | Use client.request with optional JSON/key header; assert status is successful and return JSON. | Every acceptance action travels through actual API validation and persistence rather than invoking private production methods. |
| [`exercise.start`](../../scripts/smoke_workspace.py#L67) | Submit a direct fake-decided task into fresh history. | Create session, attach current preset/config revisions/input/skills, POST twice with one key, assert same Run ID. | Live requests prove Run does not require Preview and submission retry identity is enforced end to end. |
| [`exercise.finish`](../../scripts/smoke_workspace.py#L84) | Await a Run's terminal projection with a deadline. | Poll every 80 ms for up to 25 seconds; require succeeded. | Acceptance cannot hang indefinitely or mistake queued/failed work for a completed tool workflow. |
| [`exercise.tool`](../../scripts/smoke_workspace.py#L92) | Execute one fake-selected real tool and decode evidence. | Format /tool task, call start/finish, find the tool message, JSON-decode its content, return Run/result. | Simple deterministic model decisions isolate actual OS/file/policy correctness. |
| [`main`](../../scripts/smoke_workspace.py#L244) | Own a disposable HTTP server/workspace/canary lifecycle. | Create temp root/files/settings with fake adapter/no .env loading, launch Uvicorn 8766, await readiness, use no-proxy HTTPX, finally stop server/restore canary/remove temp data. | Running the check does not use credentials or corrupt user history; cleanup includes the same application lifecycle as production. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def command(windows: str, linux: str) -> str
def assert_windows_process_stopped(pid: int) -> None
async def exercise(client: httpx.AsyncClient, workspace: Path) -> None
async def exercise.api(method: str, path: str, body=None, key=None)
async def exercise.start(text: str, skills=None)
async def exercise.finish(run)
async def exercise.tool(name: str, args: dict, skills=None)
async def main() -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## scripts/smoke_conversations.py

**Layer: Engineering / live local acceptance.** Checks revisioned conversation organization and independent copied evidence using a real local server.

**Connections and effects:** Owns port 8767 and a temporary fake-model workspace. No shell/model replay is required for copies; requests validate persisted behavior.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`exercise`](../../scripts/smoke_conversations.py#L15) | Check rename/folder/archive/duplicate/fork and copy independence. | Create two successful turns, test stale patch and prefix fork/full duplicate, compare IDs/traces/events, archive/restore, rename/delete folder, delete source, continue retained fork. | Saved evidence management must ship as a complete API/persistence workflow, with source deletion and fork-boundary semantics verified. |
| [`exercise.api`](../../scripts/smoke_conversations.py#L18) | Execute a request expecting an exact HTTP status. | Send JSON and random Idempotency-Key, assert status, decode JSON unless 204. | Error/empty response semantics are checked as part of the public API instead of bypassing validators. |
| [`exercise.turn`](../../scripts/smoke_conversations.py#L29) | Complete one direct turn and return updated session/Run. | Use current revision/preset/config, POST Run, poll with 10-second timeout, require succeeded, fetch session. | Copy/fork setup has real successful committed history and observed revisions, not hand-inserted fixture rows. |
| [`main`](../../scripts/smoke_conversations.py#L143) | Launch and close isolated conversation acceptance resources. | Create temporary workspace/data, fake Settings, Uvicorn 8767, bounded readiness wait, HTTPX no-proxy client, finally signal/await shutdown. | The user's conversations and service remain independent from repeatable acceptance. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
async def exercise(client: httpx.AsyncClient) -> None
async def exercise.api(method: str, path: str, body=None, status: int=200)
async def exercise.turn(session, text: str)
async def main() -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## tests/conftest.py

**Layer: Engineering / offline integration fixture.** Provides isolated app/HTTP resources for backend contract cases.

**Connections and effects:** HTTPX ASGITransport reaches the real app in-process under its lifespan, with temporary data/workspace and fake model. It is not a listening TCP/OS command acceptance test.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`studio_client`](../../tests/conftest.py#L11) | Yield an isolated AsyncClient and app instance. | Create safe README/workspace/settings, enter FastAPI lifespan, then ASGITransport client; close both contexts after yield. | Cases use actual use cases/migrations/runtime without provider keys or user-database effects, while resource ownership matches production. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
async def studio_client(tmp_path)
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## tests/test_api.py

**Layer: Engineering / transport-application contracts.** Tests exposed Run workflow, immutable context, approvals, browser boundary, persistence, and SSE replay.

**Connections and effects:** Uses studio_client and finite polling. Most model decisions are explicitly fake; native file calls are real guarded workspace operations.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`start`](../../tests/test_api.py#L52) | Create session, inspect Preview, and submit a Run for a case. | Read bootstrap revisions, POST session/preview, consume its hash with session ID as key, assert 200/202, return Run/body/session/Preview. | Cases share correct public setup while direct-submission behavior is tested separately. |
| [`wait_state`](../../tests/test_api.py#L79) | Await an expected Run state without silent failure. | Poll up to 150 times at 30 ms; return expected state or pytest.fail on unexpected failed/cancelled/timeout. | Async graph cases assert completed behavior instead of racing state reads. |
| [`test_draft_inspection_is_live_and_does_not_persist`](../../tests/test_api.py#L10) | Protect live unsaved compilation and file provenance. | Change inline block/variables/input/selected skill, check missing-variable failure, unchanged saved preset/session counts and zero preview rows. | Inspection must not autosave, create Runs, or lose the source of workspace instructions. |
| [`test_preview_run_trace_and_idempotency`](../../tests/test_api.py#L91) | Protect optional inspection/execution identity and saved events. | Resubmit same key, inspect exact prepared body/no fake wire body, committed single input/revision, event sequences/replay and ahead-cursor 410. | One accepted task must produce one Run and explainable saved evidence through reconnect. |
| [`test_native_file_tool_returns_to_model`](../../tests/test_api.py#L113) | Protect tool result pairing and second model step. | Submit /read, assert assistant/tool/assistant sequence, original call ID, real file content, and two model receipts. | File execution completes the vertical loop rather than stopping at a displayed tool intent. |
| [`test_direct_run_retries_and_frozen_memory`](../../tests/test_api.py#L125) | Protect direct task submission and immutable initial context. | Edit notes, run /echo through approval, change memory while waiting, reject stale hash/different-key input, retry unchanged task, inspect original note in every model receipt. | Active/final Run inputs cannot drift with subsequent file edits and Preview is optional. |
| [`test_approval_stops_before_execution`](../../tests/test_api.py#L175) | Protect both approve and reject boundaries. | Parameterize decision, wait for approval, assert no successful tool effect yet, submit choice, inspect real success or tool_denied paired message. | Prompt requests cannot execute ask tools before a human decision and denial still returns a protocol-valid result. |
| [`test_cancel_and_redacted_export`](../../tests/test_api.py#L197) | Protect Stop/history isolation and outward masking. | Cancel /slow, assert idle revision-0 session, execute synthetic secret task, inspect redacted export without token. | Cancelled text is not committed history; exported review copies do not expose common credential patterns. |
| [`test_access_boundary_and_stale_preview`](../../tests/test_api.py#L212) | Protect local browser boundary and versioned consumption. | Reject foreign Origin/cross-site Host, assert direct headerless access/no cookies, reject old preview after history change, delete idle conversation and verify Run removal. | Local simplicity retains concrete origin/ownership/version checks without a login mechanism. |
| [`test_approval_checkpoint_survives_runner_restart`](../../tests/test_api.py#L241) | Protect durable approval continuation. | Wait/checkpoint, close Runner, reopen same stores, approve through API, assert completed recorded result, close replacement. | User approval survives process-owned worker replacement without creating a new task or losing the paused graph position. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
async def test_draft_inspection_is_live_and_does_not_persist(studio_client)
async def start(client, text='Hello')
async def wait_state(client, run_id, expected)
async def test_preview_run_trace_and_idempotency(studio_client)
async def test_native_file_tool_returns_to_model(studio_client)
async def test_direct_run_retries_and_frozen_memory(studio_client)
async def test_approval_stops_before_execution(studio_client, decision)
async def test_cancel_and_redacted_export(studio_client)
async def test_access_boundary_and_stale_preview(studio_client)
async def test_approval_checkpoint_survives_runner_restart(studio_client)
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## tests/test_runtime.py

**Layer: Engineering / runtime effect-recovery contracts.** Exercises node reentry, multiple-call pairing, uncertain outcomes, and attempt/deadline bounds.

**Connections and effects:** Monkeypatches only the boundary needed to control interruption; application/checkpoint/receipt persistence remains real. Helpers call the actual API and reopen the actual checkpointer.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`allow_echo`](../../tests/test_runtime.py#L12) | Enable the harmless echo fixture and selected limits. | Read current config, set echo=allow/limits, PUT and assert success. | Controlled policy makes effect-recovery cases deterministic without bypassing server configuration. |
| [`replace_runner`](../../tests/test_runtime.py#L21) | Reopen graph ownership over existing stores. | Close old Runner, construct/open new one with same resources/path, replace Studio reference. | Recovery tests cross actual persisted checkpoints/receipts instead of simulating restart with in-memory flags. |
| [`test_multiple_calls_keep_ids_and_complete_before_next_model`](../../tests/test_runtime.py#L31) | Protect a full ordered tool batch. | Patch deterministic two-call provider, run through actual tools, assert two paired results before final assistant and summed known usage. | Model call IDs/order cannot be lost when more than one tool is requested. |
| [`test_multiple_calls_keep_ids_and_complete_before_next_model.scripted_model`](../../tests/test_runtime.py#L36) | Supply and validate a deterministic two-step stream. | First return read-id/echo-id calls; next assert both matching results/order/content, then finish with known usage. | A fixture models the provider boundary while testing the real bounded loop and pairing. |
| [`test_unknown_effect_requires_explicit_result_before_resume`](../../tests/test_runtime.py#L69) | Protect unknown-effect reconciliation without repeat dispatch. | Block tool after entry, replace Runner, reject missing resolution, record externally verified result, finish and assert one execution/attempt-history uncertainty. | Lost effect results require explicit choices and cannot be automatically repeated. |
| [`test_unknown_effect_requires_explicit_result_before_resume.uncertain_execute`](../../tests/test_runtime.py#L76) | Simulate dispatch with no durable completion. | Count entry, signal an Event, wait indefinitely until test cancellation. | The exact crash window between tool intent and receipt is reproducible without a real destructive external effect. |
| [`test_receipt_survives_interruption_before_checkpoint`](../../tests/test_runtime.py#L111) | Protect receipt reuse when graph progress is older. | Count tool effects, block tool.completed event after receipt, replace Runner, resume with no unknown resolutions, assert one execution/result. | Durable result evidence closes the replay window even before checkpoint catches up. |
| [`test_receipt_survives_interruption_before_checkpoint.counted_execute`](../../tests/test_runtime.py#L122) | Observe actual registry dispatch count. | Increment executions and await the original execute. | Receipt replay assertions measure side effects instead of merely inspecting final transcript duplication. |
| [`test_receipt_survives_interruption_before_checkpoint.interrupted_emit`](../../tests/test_runtime.py#L127) | Pause exactly after a durable tool receipt. | On first tool.completed signal/wait before delegating to original emit; otherwise emit normally. | An intentional failure window proves recovery does not depend on event/checkpoint atomicity with external tools. |
| [`test_tool_timeout_is_uncertain_and_can_be_cancelled`](../../tests/test_runtime.py#L150) | Protect generic uncertain timeouts and Stop. | Set 0.1-second tool deadline, patch slow effect, await interrupted/unknown receipt, cancel and assert terminal state. | Timeout does not falsely imply rollback, and interrupted Runs remain stoppable. |
| [`test_tool_timeout_is_uncertain_and_can_be_cancelled.slow_execute`](../../tests/test_runtime.py#L155) | Simulate a tool that outlasts its deadline. | Sleep ten seconds before returning late text. | Runtime timeout classification can be exercised offline without a paid/remote tool. |
| [`test_attempt_limits_stop_the_loop`](../../tests/test_runtime.py#L169) | Protect both model and tool ceilings. | Parameterize max_steps/max_tool_calls=1, use endlessly requesting provider, assert failed stop_reason matches limit. | A provider cannot keep the loop alive beyond explicit local budgets. |
| [`test_attempt_limits_stop_the_loop.looping_model`](../../tests/test_runtime.py#L174) | Supply perpetual harmless tool intent. | Record fake wire=None and yield one echo call with unknown usage every request. | The fixture stresses the actual policy/receipt counter path instead of manually raising a limit error. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
async def allow_echo(client, **limits)
async def replace_runner(app)
async def test_multiple_calls_keep_ids_and_complete_before_next_model(studio_client, monkeypatch)
async def test_multiple_calls_keep_ids_and_complete_before_next_model.scripted_model(config, compiled, tools, prepared, on_wire)
async def test_unknown_effect_requires_explicit_result_before_resume(studio_client, monkeypatch)
async def test_unknown_effect_requires_explicit_result_before_resume.uncertain_execute(*_args)
async def test_receipt_survives_interruption_before_checkpoint(studio_client, monkeypatch)
async def test_receipt_survives_interruption_before_checkpoint.counted_execute(*args)
async def test_receipt_survives_interruption_before_checkpoint.interrupted_emit(run_id, event_type, payload, **changes)
async def test_tool_timeout_is_uncertain_and_can_be_cancelled(studio_client, monkeypatch)
async def test_tool_timeout_is_uncertain_and_can_be_cancelled.slow_execute(*_args)
async def test_attempt_limits_stop_the_loop(studio_client, monkeypatch, limits, reason)
async def test_attempt_limits_stop_the_loop.looping_model(config, compiled, tools, prepared, on_wire)
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## tests/test_config.py

**Layer: Engineering / secret-reference and platform-default contracts.** Verifies configuration serialization boundaries and safe Windows migration.

**Connections and effects:** Constructs Pydantic settings/config values directly, without calling a model or changing live user settings.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`test_literal_credential_values_are_rejected`](../../tests/test_config.py#L15) | Protect provider/MCP credential-reference syntax. | Parameterize both config factories and assert normal sk- literal key is a ValidationError. | Common actual key strings must not enter stored serializable settings. |
| [`test_mcp_env_and_urls_do_not_embed_credentials`](../../tests/test_config.py#L21) | Protect child-reference and endpoint validation. | Reject literal environment values, URL user/password, and credential query parameters. | Authentication values belong to late-resolved references/headers rather than displayed config URLs. |
| [`test_windows_shell_upgrade_preserves_permissions_and_old_snapshots`](../../tests/test_config.py#L32) | Protect native shell upgrade without permission expansion. | On Windows compare all allow/ask/deny migrations, retain original values, and preserve explicit powershell deny over old bash allow. | Startup selects the correct current tool but cannot grant new authority or rewrite old Run configuration values. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def test_literal_credential_values_are_rejected(factory)
def test_mcp_env_and_urls_do_not_embed_credentials()
def test_windows_shell_upgrade_preserves_permissions_and_old_snapshots()
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## tests/test_prompts.py

**Layer: Engineering / pure compiler contracts.** Verifies deterministic ordering/source reporting and explicit malformed-input failures.

**Connections and effects:** Uses domain values only; all cases run without databases/network/SDK effects.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`compile_blocks`](../../tests/test_prompts.py#L7) | Build a concise pure compiler fixture. | Construct Test preset/fake config with empty history/current Task and optional variables, then call compile_prompt. | Cases focus on one compiler invariant rather than repeat unrelated setup. |
| [`test_compilation_is_deterministic_and_preserves_slots`](../../tests/test_prompts.py#L18) | Protect stable composition and source mapping. | Compile identical inputs twice, compare results and Rules/history/context/task sequence plus first source index. | Reproducible Preview/execution needs identical ordering independent of input block list order. |
| [`test_filtered_blocks_report_why`](../../tests/test_prompts.py#L45) | Protect explicit exclusion evidence. | Build disabled/provider/model/condition-filtered blocks and assert only current input remains with ordered reason values. | Filtering cannot silently drop instructions or lose its explanation. |
| [`test_invalid_compilation`](../../tests/test_prompts.py#L83) | Protect deliberate error vocabulary. | Parameterize duplicate order, undeclared/missing variable, and executable-looking template cases; assert AppError code. | Compiler errors must be actionable and cannot become hidden exclusions or arbitrary execution. |
| [`test_substitution_does_not_reinterpret_text`](../../tests/test_prompts.py#L89) | Protect single-pass literal substitution. | Supply a variable containing another placeholder and assert it remains literal. | Untrusted variable content cannot trigger recursive template expansion. |
| [`test_context_budget`](../../tests/test_prompts.py#L97) | Protect explicit overflow rejection. | Set context window below output reservation and expect a context error. | Requests do not silently truncate/summarize user instructions to fit. |
| [`test_condition_operators`](../../tests/test_prompts.py#L113) | Protect grammar truth semantics. | Parameterize empty all/any, not, and missing exists values against expected booleans. | The restricted expression language has predictable behavior without Python eval. |
| [`test_missing_condition_field_is_not_silently_false`](../../tests/test_prompts.py#L117) | Protect missing comparison-field diagnostics. | Compare absent variables.x with eq and require AppError. | A typo/missing value cannot silently disable a configured message. |
| [`test_malformed_operator_is_an_application_error`](../../tests/test_prompts.py#L122) | Protect shape checking before Python comparisons. | Supply list-valued op and assert an operator AppError. | Invalid condition JSON remains a safe compiler failure rather than an uncontrolled type exception. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def compile_blocks(blocks, **kwargs)
def test_compilation_is_deterministic_and_preserves_slots()
def test_filtered_blocks_report_why()
def test_invalid_compilation(blocks, code)
def test_substitution_does_not_reinterpret_text()
def test_context_budget()
def test_condition_operators(condition, expected)
def test_missing_condition_field_is_not_silently_false()
def test_malformed_operator_is_an_application_error()
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## tests/test_providers.py

**Layer: Engineering / SDK-HTTP serialization contract.** Checks real ChatOpenAI serialization against the HTTP client boundary with a mocked local transport.

**Connections and effects:** No external service is called. The actual SDK mapping/stream parser runs, and the fixture returns valid SSE chunks with known usage.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`test_openai_preview_matches_captured_outbound_request`](../../tests/test_providers.py#L10) | Protect Preview versus actual body consistency. | Compile/prepare, install HTTPX subclass with mock transport, collect generate events/wire callback, assert exact payload/final text/known usage. | Private SDK serialization remains localized and tested so an upgrade cannot silently invalidate inspection evidence. |
| [`test_openai_preview_matches_captured_outbound_request.transport`](../../tests/test_providers.py#L21) | Emulate streaming Chat Completions at the HTTP boundary. | Assert request JSON equals prepared, return content/final usage SSE plus DONE. | The test exercises serializer and stream normalization while remaining offline and uncharged. |
| [`test_openai_preview_matches_captured_outbound_request.TestClient.__init__`](../../tests/test_providers.py#L54) | Create an actual HTTPX subclass with fixture transport. | Delegate constructor with MockTransport and existing hook/timeout arguments. | SDK client type validation remains intact; replacing it with a duck-typed stub would miss boundary behavior. |
| [`test_openai_preview_matches_captured_outbound_request.on_wire`](../../tests/test_providers.py#L59) | Collect pre-transmission evidence for assertion. | Append supplied body to captured list. | Exactly one captured prepared body establishes the observed request contract independently of final model text. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`test_openai_preview_matches_captured_outbound_request.TestClient`](../../tests/test_providers.py#L51) | SDK-compatible offline HTTP client fixture. | Subclasses the original AsyncClient and changes only its transport. | The production capture hook and SDK type assumptions remain exercised in the contract test. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
async def test_openai_preview_matches_captured_outbound_request(monkeypatch)
async def test_openai_preview_matches_captured_outbound_request.transport(request)
def test_openai_preview_matches_captured_outbound_request.TestClient.__init__(self, **kwargs)
async def test_openai_preview_matches_captured_outbound_request.on_wire(body)
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## tests/test_storage.py

**Layer: Engineering / persistence contract.** Checks schema initialization, optimistic settings edits, and restart persistence.

**Connections and effects:** Creates/reopens a temporary SQLite database via the actual Repository and migrations.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`test_starter_upgrade_is_idempotent_and_preserves_user_text`](../../tests/test_storage.py#L28) | Protect safe starter upgrades. | Store recognized old text with normalized disabled order, restart twice, then restart after a user edit and compare content/revisions. | Better defaults cannot overwrite user instructions or generate a new revision on every startup. |
| [`test_migration_persistence_and_revision_conflict`](../../tests/test_storage.py#L7) | Protect durable schema/seed/data and stale-write rejection. | Initialize, advance config revision, reject old revision, verify seeded preset, save a session, close/reopen, and assert retained values. | Database lifecycle must preserve user edits rather than reseed/reset data on each startup. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
async def test_migration_persistence_and_revision_conflict(tmp_path)
async def test_starter_upgrade_is_idempotent_and_preserves_user_text(tmp_path)
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## tests/test_tools.py

**Layer: Engineering / file and MCP contracts.** Checks actual file exclusions and both local MCP transports with schema freshness.

**Connections and effects:** Uses temporary files/local fixture subprocesses/listeners. These are offline loopback contracts, not accepted integration with user MCP servers.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`test_file_tool_boundaries`](../../tests/test_tools.py#L19) | Protect native bounded path reads. | Read known text, reject uppercase .ENV/.DB, binary and oversized files, and traversal. | File helpers cannot bypass exclusions merely through casing or alternate caller paths. |
| [`test_mcp_discovery_and_execution`](../../tests/test_tools.py#L38) | Protect SDK session/discovery/call and stdio environment scoping. | Parameterize stdio/HTTP, launch local fixture, discover namespaced tools, call echo, verify explicit env reference present/ambient key absent, finally terminate listener. | Real transport contracts work without provider credentials, and configured references do not forward arbitrary server environment secrets. |
| [`test_changed_mcp_schema_is_rejected`](../../tests/test_tools.py#L95) | Protect frozen advertised schema across fresh connections. | Discover fixture, change referenced schema-mode variable, execute frozen tool, and require schema-changed AppError. | A reconnect cannot invoke an interface different from the Run's advertised tool contract. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def test_file_tool_boundaries(tmp_path)
async def test_mcp_discovery_and_execution(tmp_path, monkeypatch, transport)
async def test_changed_mcp_schema_is_rejected(tmp_path, monkeypatch)
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## tests/fixtures/mcp_server.py

**Layer: Engineering / stateless MCP fixture.** Provides deterministic harmless tools and a deliberately changed schema variant.

**Connections and effects:** Top-level argparse selects loopback HTTP or stdio; FastMCP stateless_http/json_response config supplies protocol transport. This module is a child executable, not imported into production dispatch.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`changed_echo`](../../tests/fixtures/mcp_server.py#L23) | Expose an intentionally different fixture_echo argument schema. | Under changed environment mode accept value instead of text and return it. | Schema-hash revalidation is proven against an actual server change rather than a fabricated local hash mismatch. |
| [`fixture_echo`](../../tests/fixtures/mcp_server.py#L30) | Return deterministic harmless tool text. | Prefix input text with MCP evidence. | Runtime/browser tests can identify real SDK invocation without destructive or remote work. |
| [`environment_check`](../../tests/fixtures/mcp_server.py#L36) | Report variable presence without values. | Return booleans for configured fixture secret and unrelated ambient key. | Tests verify environment scoping without printing synthetic or real credential values. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def changed_echo(value: str) -> str
def fixture_echo(text: str) -> str
def environment_check() -> dict[str, bool]
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## web/e2e/studio.spec.ts

**Layer: Engineering / browser acceptance.** Exercises operational UI/API/persistence workflows against the isolated built SPA.

**Connections and effects:** Playwright config owns server/workspace at 8765 with fake model. Named helpers are listed below; registered anonymous test callbacks are listed individually afterward and in Callback reference.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`openWorkspace`](../../web/e2e/studio.spec.ts#L6) | Open the app and create fresh conversation. | Navigate /, assert workspace attached, open responsive sidebar if needed, create history, and assert Preview disabled with no task. | Every case starts through real local UI without login or accidental prior task input. |
| [`startTask`](../../web/e2e/studio.spec.ts#L17) | Submit one task directly. | Fill Task and click Run, without clicking Preview. | Browser acceptance protects the direct operational workflow the product promises. |
| [`actions`](../../web/e2e/studio.spec.ts#L323) | Find current conversation's latest action trigger. | Return last session-menu-trigger locator within the history management case. | Menu actions follow created copies/forks rather than a stale original DOM row. |

### Registered frontend verification callbacks

Each row describes a concrete anonymous `test`/`it` function. Nested interaction/predicate callbacks appear in [Callback reference](callbacks.md). These rows explain existing cases; this documentation change does not claim to have rerun them.

| Source / case | Mechanism | Why / architectural boundary |
| --- | --- | --- |
| [E2E: edit/preview/file/trace/refresh](../../web/e2e/studio.spec.ts#L20) | Edit prompt marker, compile optional Preview, run actual read_text, inspect trace/export, reload and verify one saved turn. | Rendering/compilation/tool evidence and refresh identity are one complete workflow. |
| [E2E: approval after refresh](../../web/e2e/studio.spec.ts#L44) | Trigger ask, reload, approve once, verify one result/turn. | Browser lifetime does not own the durable approval/checkpoint. |
| [E2E: reject and cancel](../../web/e2e/studio.spec.ts#L58) | Reject echo and inspect tool_denied, then Stop /slow. | Permission and cancellation feedback remain operational in chat. |
| [E2E: provider references and Preview invalidation](../../web/e2e/studio.spec.ts#L70) | Save endpoint/model/reference fields, reopen Settings, check Run availability. | Configuration persistence never requires a browser key or stale Preview gate. |
| [E2E: stdio MCP](../../web/e2e/studio.spec.ts#L92) | Add fixture executable/args, save/discover, observe default deny, explicitly allow, run call. | Discovery is separate from authorization, with real local SDK effects. |
| [E2E: memory/skills direct Run](../../web/e2e/studio.spec.ts#L119) | Save note/procedure, select skill, direct Run, inspect both sources. | File editors, snapshots, and model input provenance work together. |
| [E2E: narrow viewport](../../web/e2e/studio.spec.ts#L149) | Use mobile-sized page, inspect Preview and visible Run; check layout bounds. | Optional panes cannot make task controls inaccessible. |
| [E2E: independent reversible drafts](../../web/e2e/studio.spec.ts#L168) | Duplicate/switch/edit/undo/redo/discard, verify original and draft state. | Preset selection stays usable with unsaved work; undo does not mutate saved data. |
| [E2E: conversation organization and fork](../../web/e2e/studio.spec.ts#L198) | Two turns, rename/folder/fork/continue/duplicate/archive/restore/delete folder/delete copy. | Menu state, source-prefix evidence, shared history independence, and fixed transcript UI are vertical features. |
| [Component: placement reordering](../../web/src/Composer.test.tsx#L31) | Render Composer, click move, inspect emitted aggregate/order/roles/original object. | UI order normalization preserves slot/role/source semantics. |
| [Component: malformed rule draft](../../web/src/Composer.test.tsx#L53) | Type invalid JSON, Apply rules, require no emitted aggregate and visible error. | Invalid advanced text cannot corrupt the parent's applied preset draft. |

## What these checks establish

Pure compiler cases establish deterministic local computation. Mocked provider transport establishes application-level serialization/normalization, not a remote server's behavior. ASGI cases establish HTTP/use-case/SQLite/checkpoint logic without a listening socket. Smoke scripts establish listening HTTP plus actual native OS tools and files. Browser cases establish visible workflows with the built SPA. All use explicit fake decisions or mocked/local endpoints. Substantial live-model coding/analysis and stateful specialized MCP acceptance remain required before later delivery stages can be declared complete.

## migrations/versions/0004_projects.py

**Layer: Engineering / application schema migration.** Adds named workspace roots and nullable conversation membership.

**Connections and effects:** Alembic changes application tables only; root snapshots and third-party checkpoints remain untouched.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`upgrade`](../../migrations/versions/0004_projects.py#L12) | Add project storage and conversation linkage. | Create projects with unique root, then ALTER TABLE sessions ADD COLUMN with an inline FK. | In-place alteration avoids rebuilding sessions and cascade-deleting owned Runs under SQLite foreign-key enforcement; existing sessions keep null/default membership. |
| [`downgrade`](../../migrations/versions/0004_projects.py#L25) | Remove project selection while preserving history. | Drop the nullable membership column in place, then the projects table. | Metadata rollback does not modify immutable Run evidence or checkpoint internals. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def upgrade() -> None
def downgrade() -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## scripts/acceptance_stage2.py

**Layer: Engineering / opt-in external acceptance.** Checks a real model working in a registered Windows project folder.

**Connections and effects:** Requires --live, reads the running service provider references, uses isolated HTTP port 8768/data/files, preserves evidence under ignored var, and restarts its owned server.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`api`](../../scripts/acceptance_stage2.py#L80) | Send one acceptance request. | Use actual HTTP with per-Run idempotency keys and fail on application errors. | Successful object construction alone cannot establish a working Web/API execution path. |
| [`start_server`](../../scripts/acceptance_stage2.py#L90) | Open an isolated acceptance lifecycle. | Start Uvicorn on 8768, wait for readiness with a bounded deadline, and return server/task ownership. | Acceptance cannot mutate the user database and can verify restart persistence. |
| [`run_task`](../../scripts/acceptance_stage2.py#L105) | Execute one real task and audit its evidence. | Create a project-bound session, submit directly, await success, check wire/prepared equality and canonical source/tool-ID pairing, and return safe summary plus local trace. | A fluent final answer or simulated tool decision is insufficient acceptance evidence. |
| [`verify_invoice`](../../scripts/acceptance_stage2.py#L174) | Independently check the fixed behavior. | Run immutable check.ps1 through the selected project registry and OS sandbox, asserting exit/output. | The model cannot pass by claiming success or weakening the acceptance check. |
| [`exercise`](../../scripts/acceptance_stage2.py#L189) | Accept coding, memory, Skill creation and analysis. | Ask for multi-file repairs and failing/passing checks, verify unchanged user fixtures, guarded memory writes, saved/reloaded Skill and a concrete path-boundary finding. | Representative work demonstrates a local agent beyond a single echo command. |
| [`main`](../../scripts/acceptance_stage2.py#L261) | Own opt-in setup, project registration, restart and final report. | Validate a real configured provider, create sibling default/project/data folders, run cases, restart and reuse memory/Skill in a fresh conversation, then close resources. | Saved ordinary files and registered roots must survive an application lifecycle without replaying old effects. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
async def api(client: httpx.AsyncClient, method: str, path: str, body: Any=None) -> Any
async def start_server(settings: Settings) -> tuple[uvicorn.Server, asyncio.Task]
async def run_task(client: httpx.AsyncClient, title: str, task: str, project_id: str | None=None) -> tuple[dict, dict]
async def verify_invoice(server: uvicorn.Server, project_id: str) -> None
async def exercise(client: httpx.AsyncClient, server: uvicorn.Server, workspace: Path, project_id: str) -> list[dict]
async def main() -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## scripts/smoke_projects.py

**Layer: Engineering / offline live HTTP acceptance.** Checks sibling workspace isolation and conversation moves with simulated model decisions and real tools.

**Connections and effects:** Owns temporary files/data and server port 8769; no external model requests or selected user workspace edits occur.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`exercise`](../../scripts/smoke_projects.py#L17) | Verify scoped data/tools and immutable historical roots. | Register two roots, run file/native commands, save distinct memory/Skills, inspect sources, move idle history, fork, and reject a busy move. | Project metadata must bind the full vertical workflow rather than only relabel a sidebar. |
| [`exercise.api`](../../scripts/smoke_projects.py#L20) | Submit a local acceptance request. | Send HTTP JSON/idempotency header and require a success status. | Each assertion covers the actual API path. |
| [`exercise.start`](../../scripts/smoke_projects.py#L45) | Start a current project-bound task. | Read its latest revision and post direct Run input. | No Preview prerequisite or browser-only workspace selection is accepted. |
| [`exercise.finish`](../../scripts/smoke_projects.py#L62) | Wait for real execution completion. | Poll within a 30-second limit and require succeeded. | Async creation is not evidence of completed tools. |
| [`main`](../../scripts/smoke_projects.py#L170) | Own isolated sibling roots and the server. | Create temporary default/project/data directories, start 8769, exercise, check default notes untouched and close the lifecycle. | Offline verification is repeatable without contaminating private project records. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
async def exercise(client: httpx.AsyncClient, roots: list[Path]) -> None
async def exercise.api(method: str, path: str, body=None)
async def exercise.start(session_id: str, text: str)
async def exercise.finish(run)
async def main() -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>
