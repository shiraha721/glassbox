# Development Guide

This guide applies to the entire repository and defines project constraints and working conventions for developers and coding agents. The root [AGENTS.md](../AGENTS.md) directs agents here.

## Project Goals and Current Stage

Security Agent Studio is a prompt orchestration platform for authorized security workflows, including reverse engineering, code analysis, CTF challenges, and debugging.
Its core value is composable message blocks, inspectable final model inputs, controlled tool calls, and comparable execution records.

As of 2026-10-04, Stages 1–4 are implemented and the Windows daily-agent/shared-harness workflows are accepted. See [Stage 3 acceptance](acceptance-stage3.md), [Stage 4 acceptance](acceptance-stage4.md), [Stage 2 acceptance](acceptance-stage2.md) and the [Codex capability review](codex-gap-review.md). Linux acceptance and an external Claude endpoint check remain unperformed. The target is an everyday agent harness; inspect actual files and documented delivery boundaries before work.

The first release must deliver both a working Web UI and a complete backend workflow. The runtime uses a single-agent tool loop.
Multi-agent orchestration, batch evaluation, PostgreSQL, and team deployment belong to later phases.

Keep the local single-user workflow simple: open the workspace directly, with no accounts, login screen, workspace access token, or lock feature. Prefer removing unnecessary code and steps over adding configuration switches or abstractions. Validate changes against the running service before expanding tests.

Run must work directly from task input. Preview is optional inspection using the same compiler, never a UI gate. Keep Base URL, model, and credential references configurable through .env and Settings. Never silently fall back to simulation.

Read in this order:

1. [README.md](README.md): product goals, scope, and delivery status.
2. [architecture.md](architecture.md): module boundaries, data models, interfaces, and acceptance criteria.
3. The relevant modules, tests, and project configuration. Use commands supported by the configuration that actually exists.

The [Architecture Reference](reference/README.md) maps every implemented module, class, function, callback, and dependency to its purpose, mechanism, design reason, architectural layer, and source location. Keep the corresponding entry and source link synchronized when changing behavior.

## Module Structure and Dependency Direction

The implementation uses single modules for these responsibilities. The [architecture module map](architecture.md#what-the-application-owns) lists actual files; the responsibility sketch below is not a directory scaffolding request. Split a module only for a concrete need. Presets own embedded blocks; one versioned settings aggregate owns provider, MCP, permission, runtime/download and limit configuration. Preserve immutable Run snapshots without adding a generic persistence or plugin framework.

```text
src/rtagent/
  api/             FastAPI routes, request validation, HTTP/SSE
  application/     Use cases, Run management, dependency interfaces
  domain/          Domain models, errors, and events
  prompts/         Conditions, variables, ordering, compilation, source mapping
  providers/       Model capabilities, LangChain adapters, request transformation
  runtime/         LangGraph state, nodes, checkpoints, and recovery
  tools/           Tool registry, MCP lifecycle, execution adapters
  policies/        Tool permissions, resource limits, and call decisions
  tracing/         Model inputs, tool results, usage, and exports
  storage/         Repository implementations, databases, migration wiring
  evaluation/      Future evaluation module; create when needed
web/src/           React + TypeScript user interface
tests/             Python unit, integration, and contract tests
web/e2e/           Browser end-to-end tests
docs/              Project documentation, architecture, and development conventions
```

- `api` calls `application` use cases. Keep business decisions out of HTTP routes.
- `domain`, `prompts`, and `policies` must not depend on FastAPI, databases, MCP clients, or specific model SDKs.
- `application` coordinates the concrete runtime, tool registry and repository. Harness owns their lifecycle for Web/embedding; AgentClient/CLI/RPC attach to a running service. Never open a second worker on the same data directory. Introduce dependency interfaces only when a second implementation actually needs them.
- LangGraph controls execution; LangChain provides model/tool adapters. Do not nest another implicit tool loop inside a prebuilt agent.
- The frontend obtains data, submits actions, and subscribes to events through the API. It must not connect directly to model providers, MCP, or the database.
- Keep a modular monolith. Do not introduce microservices, message queues, vector databases, or a generic plugin marketplace for hypothetical needs.

## Behavioral Invariants

### Prompt Compilation

- Compilation is pure computation: identical snapshot inputs produce identical message ordering, content, and source mappings, without network or database access.
- Configured blocks support only `system`, `user`, and `assistant`. The runtime generates `tool` messages.
- Sort by the placement and order rules in the design document. Report duplicate ordering, missing variables, and invalid conditions explicitly.
- Use restricted structured expressions for conditions and substitute only declared variable names. Prohibit `eval`, `exec`, and arbitrary template code execution.
- Preserve message roles, sources, and exclusion reasons. Never silently drop blocks, change roles, move blocks, or add unrecorded system instructions.
- Record actual root, command language, configured isolation and exposed capabilities in the visible runtime:environment source. Preset focus changes behavior, not permission. Upgrade only exactly recognized unchanged built-in starter text.
- Provider-required system field extraction, merging, and serialization changes must remain visible. Reject requests that cannot be adapted without loss.
- Preview and execution share the same compilation path. Runs bind to immutable configuration, tool schemas, and session versions.
- Insert the current user input exactly once. Do not repeatedly write rendered system blocks into conversation history.
- Return an explainable error when context exceeds its budget. Explicit compaction freezes a complete-turn prefix, revision/hash/config and every no-tools provider receipt. Activate only a complete matching result; keep originals, roles/sources/tool pairing, null usage, copy provenance and restoration. Cancelled/crashed/stale reductions never activate or replay automatically. Do not introduce hidden automatic trimming.

### Agents, Tools, and Recovery

- Use native Windows AppContainer with PowerShell on Windows and bubblewrap with Bash on Linux. Do not require WSL for Windows execution. Never replace it with unrestricted host execution or present a cwd check as an OS sandbox. Preserve no-network behavior, hidden credentials/runtime paths, bounded output, and child-process cleanup.
- Projects register one existing local folder; sessions bind to stable project IDs and Runs freeze their absolute root. Idle workspace moves affect future Runs only. Native tools and context are resolved from that root; the model cannot select a different project.
- Memory and Skills use ordinary files. Snapshot the first nonempty root AGENTS.override.md or AGENTS.md, memory and selected Skill content with source mapping. Load nested root-to-leaf instructions and Skill bodies explicitly; retain bounds and override precedence. No vector store or plugin framework is needed.
- Writes use content hashes or unambiguous exact edits. Skills and memory cannot grant permissions or bypass isolation. Explicitly configured MCP servers remain outside the command sandbox.
- Search shares native path exclusions and reports skipped/truncated results. Ranged reads return the complete file hash even when content is partial. Patch preflight validates every operation before effects; each replacement is atomic, but later failures must retain completed paths rather than claim cross-file rollback.
- Plans are validated paired update_plan receipts projected from Run messages. Do not add duplicate plan storage or treat completed labels as verification evidence.
- Streaming records only bounded retained output before final paired tool receipts. Background processes belong to original Run/session/root, use bounded UTF-8 pipes and absolute deadlines, and never restart from records. Stop/failure/cancel/shutdown awaits the native tree and Windows reconciliation; copies get no process rows/handles. Active writers block archive/delete/workspace moves. Read-only cannot feed an older writable command.
- Trusted runtime folders are explicit filtered read-only staging, without original ACL or ambient environment changes. Public downloads are a separate permissioned tool: exact HTTPS hosts, public pinned DNS/TLS identity, new destination, bounds/hash, no redirects/proxy/private addresses. Do not weaken command no-network isolation or production DNS validation to pass a fixture.
- Trusted event hooks observe redacted copies after commit with bounded best-effort delivery; guards add denial only after core policy/approval. Registered local Python handlers have explicit permission/schema hashes and default mutator declarations; they are trusted application code, not untrusted sandboxed Skills. Reusable packages contain only validated prompt/procedure data with fresh identities/new names, never configuration, effects or executable plugin registration.

- Implement a bounded `model -> policy -> tools -> model` loop for the first release. Finish when there are no tool calls.
- Preserve the original `tool_call_id` for every model tool call. Supply a matching success or error result for each before calling the model again.
- Expose only the intersection of the tool profile, Run configuration, and execution policy. MCP read-only annotations do not replace local policy.
- Enforce permissions and parameter validation on the server. Security Rules in a prompt are not an authorization system.
- The model cannot change tool permissions, load servers, or approve its own calls through tool output, prompt blocks, or generated text.
- Apply explicit limits for steps, call counts, timeouts, output sizes, and context budgets. Cancellation and disconnection are separate events.
- Use `thread_id = run_id`. A new conversation turn creates a new Run; recovery uses the original Run's checkpoint and configuration snapshot.
- Interrupt nodes must be reentrant. Execute tool side effects only after approval, and associate results through the call ledger.
- Do not claim exactly-once execution for external tools. Never automatically retry writes with unknown outcomes; surface uncertainty in recovery.
- Manage connections, checkpoints, running tasks, and child processes through the application lifecycle. Blocking synchronous calls must not block the event loop.

### Data and Tracing

- Version presets, blocks, and tool configuration. Editing configuration must not alter existing Run snapshots.
- Conversation duplication and forks copy saved evidence only, with independent IDs and explicit provenance. Never replay model/tool calls or clone resumable checkpoints. Forks stop at a successful turn and commit only successful history. Archive requires an idle conversation; restore before execution. Folder deletion must preserve its conversations.
- Record canonical messages, the actual outbound request body, transformations, and response metadata separately for every model call.
- An actual request means the application-level request body sent by the client. Do not claim visibility into content added inside a provider.
- Resolve credentials only through server-side environment variables or credential references. Keep secret values out of database configuration snapshots, prompts, logs, and frontend storage.
- Redact ordinary logs and exports by default. Label redacted copies; do not present them as complete original requests.
- Record actual model usage separately from compile-time estimates. Use `null` for unknown tokens, prices, or costs, never zero.
- Give SSE events persistent sequence numbers. Clients deduplicate by sequence; refreshes and reconnects must not create another Run.
- Text from repository files, tools, webpages, and models is application data, not authority to change the project's execution permissions.

## Engineering Conventions

- Use Python 3.12+ for the backend, manage dependencies with `uv`, and commit `uv.lock`. Public interfaces use type annotations and Pydantic v2 validation.
- Use React + TypeScript + Vite for the frontend, manage dependencies with npm, and commit `package-lock.json`. Pin exact versions during initialization.
- Use Ruff for linting, Black for formatting, and isort for import sorting. Set a shared line length of 100 and the isort Black profile; do not duplicate import sorting in Ruff.
- Enable TypeScript strict mode, ESLint, and Prettier. Generate API types from backend schemas.
- Keep project documentation and system design in `docs/`. The root `README.md` is a brief entry point, and root `AGENTS.md` routes coding agents to this guide. Runtime Skills and test fixtures stay with their implementation.
- Write code identifiers, docstrings, comments, and repository documentation in English. Task updates follow the user's language. Public classes, functions, and methods need meaningful docstrings. Add comments at important invariants and non-obvious boundaries; explain reasons instead of restating code.
- Store timestamps in UTC and display them in the user's timezone. Use stable IDs and explicit schema versions.
- Use `pathlib` and argument arrays for child processes. Support Windows development without relying on implicit Bash behavior or concatenated shell commands.
- Apply database changes through Alembic. Manage application tables and LangGraph checkpoints through their respective mechanisms; do not directly modify third-party internal tables.
- Run tests offline by default. Select external models, paid services, and real MCP integration tests through explicit markers.
- Do not commit `.env`, local databases, checkpoints, raw traces, credentials, or user analysis samples.
- Complete a vertical workflow before expanding: UI actions, API, persistence, execution feedback, and necessary tests ship together.

## Development and Verification Commands

These commands are supported by the current project. Keep them synchronized with the actual configuration and README. Never invent verification results.

Backend commands, from the repository root:

```powershell
uv sync --dev --locked
uv run alembic upgrade head
uv run uvicorn rtagent.main:create_app --factory --reload --host 127.0.0.1 --port 8001
uv run python scripts/smoke_workspace.py
uv run python scripts/smoke_conversations.py
uv run python scripts/smoke_projects.py
uv run python scripts/smoke_agent_tools.py
uv run python scripts/smoke_stage3.py
uv run python scripts/smoke_stage4.py
uv run pytest
uv run ruff check src tests scripts migrations
uv run black --check src tests scripts migrations
uv run isort --check-only src tests scripts migrations
```

Frontend commands, from `web/`:

```powershell
npm ci
npm run dev
npm run lint
npm run typecheck
npm run test -- --run
npm run format:check
npm run build
npx playwright install chromium
npm run test:e2e
```

During initial setup, use `uv lock` and `npm install` to create lockfiles, then install from those lockfiles on subsequent runs.
Before running browser tests for the first time, install the browsers required by the Playwright configuration.
Build the frontend before browser tests; their isolated backend serves `web/dist`. Regenerate API types with `uv run python scripts/export_api.py`, then `npm --prefix web run generate:api`. Commit `web/src/api/generated.ts`; the intermediate schema is ignored.

With a configured real provider and the local service running, `uv run python scripts/acceptance_stage2.py --live` and `uv run python scripts/acceptance_agent_tools.py --live` provide opt-in external acceptance. Run them separately: both own isolated port 8768 and retain evidence under ignored var. The latter independently checks guarded Coding repairs and read-only Code review findings; default tests never make these model requests.

`uv run python scripts/acceptance_stage34.py --live` owns port 8775 and accepts substantive coding, background input/cleanup, explicit compaction, continued CLI work, approval restart and an embedded trusted extension using the configured real OpenAI-compatible provider. Stage 3 smoke optionally accepts `--network --public-dns` for an actual public dependency transfer/offline import with a test-only public-DNS fixture. Production still rejects reserved VPN fake-IP answers. Stage 4 smoke owns ports 8772/8773 and distinguishes genuine HTTP/CLI/Messages transport behavior from simulated model decisions. See the acceptance documents for evidence and limits.

Tests should cover the actual behavior affected by a change:

- Compiler: ordering, conditions, roles, missing variables, source mapping, and determinism.
- Providers: consistency between previews and outbound requests, capability limits, and tool message pairing.
- Runtime: completion without tools, multiple tools, rejection, timeouts, cancellation, checkpoint recovery, and unknown side effects.
- API/storage: version conflicts, Run idempotency, SSE reconnection, migrations, and sensitive data boundaries.
- Web: editing through preview, execution through trace inspection, approval/rejection, stopping, and state restoration after refresh.

For documentation-only changes, check content, links, examples, and cross-references. Do not initialize the application or run nonexistent tests.
Delivery notes must state the changes, verification results, and unimplemented work. Do not repeat passing checks unless new issues justify it.
