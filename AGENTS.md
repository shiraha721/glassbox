# AGENTS.md

This file applies to the entire repository and defines project constraints and working conventions for developers and coding agents.

## Project Goals and Current Stage

Security Agent Studio is a prompt orchestration platform for authorized security workflows, including reverse engineering, code analysis, CTF challenges, and debugging.
Its core value is composable message blocks, inspectable final model inputs, controlled tool calls, and comparable execution records.

As of 2026-10-03, a local MVP and Web UI are implemented. Consult the README and actual source files for current delivery status; the architecture document distinguishes this baseline from the target product.
Inspect the actual files before starting work. Do not report the target directories, commands, or APIs below as implemented capabilities.

The first release must deliver both a working Web UI and a complete backend workflow. The runtime uses a single-agent tool loop.
Multi-agent orchestration, batch evaluation, PostgreSQL, and team deployment belong to later phases.

Read in this order:

1. [README.md](README.md): product goals, scope, and delivery status.
2. [docs/architecture.md](docs/architecture.md): module boundaries, data models, interfaces, and acceptance criteria.
3. The relevant modules, tests, and project configuration. Use commands supported by the configuration that actually exists.

## Target Structure and Dependency Direction

The baseline uses single modules (`api.py`, `application.py`, `runtime.py`, and so on) for these responsibilities. Split a module only when its size or a concrete extension warrants it. Presets own embedded blocks; one versioned settings aggregate owns provider, MCP, permission, and limit configuration. Preserve immutable Run snapshots without adding a generic persistence or plugin framework.

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
docs/              Architecture and necessary decision records
```

- `api` calls `application` use cases. Keep business decisions out of HTTP routes.
- `domain`, `prompts`, and `policies` must not depend on FastAPI, databases, MCP clients, or specific model SDKs.
- `application` coordinates the concrete runtime, tool registry, and repository. The entry point owns their lifecycle. Introduce dependency interfaces only when a second implementation actually needs them.
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
- Provider-required system field extraction, merging, and serialization changes must remain visible. Reject requests that cannot be adapted without loss.
- Preview and execution share the same compilation path. Runs bind to immutable configuration, tool schemas, and session versions.
- Insert the current user input exactly once. Do not repeatedly write rendered system blocks into conversation history.
- Return an explainable error when context exceeds its budget. Automatic trimming and summarization require separate design and tests; they are not implicit fallbacks.

### Agents, Tools, and Recovery

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

Tests should cover the actual behavior affected by a change:

- Compiler: ordering, conditions, roles, missing variables, source mapping, and determinism.
- Providers: consistency between previews and outbound requests, capability limits, and tool message pairing.
- Runtime: completion without tools, multiple tools, rejection, timeouts, cancellation, checkpoint recovery, and unknown side effects.
- API/storage: version conflicts, Run idempotency, SSE reconnection, migrations, and sensitive data boundaries.
- Web: editing through preview, execution through trace inspection, approval/rejection, stopping, and state restoration after refresh.

For documentation-only changes, check content, links, examples, and cross-references. Do not initialize the application or run nonexistent tests.
Delivery notes must state the changes, verification results, and unimplemented work. Do not repeat passing checks unless new issues justify it.
