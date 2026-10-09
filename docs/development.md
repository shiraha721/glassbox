# Development guide

Conventions and constraints for everyone changing this repository, including coding agents (the root [AGENTS.md](../AGENTS.md) points here). Read the [project guide](README.md) and [architecture](architecture.md) first, then the modules you are changing; the code is the authority on what is implemented.

## Principles

- **Keep the single-user local workflow simple.** No accounts, login, access tokens, extra services, queues or plugin frameworks. Prefer removing code to adding configuration switches or abstractions. Introduce an interface only when a second implementation actually exists.
- **Run works directly from a task.** Preview is optional inspection through the same compiler, never a gate. Never fall back to simulation silently.
- **Evidence over convenience.** Keep Run snapshots immutable, message roles and sources intact, every tool call paired with one result, revision checks on edits, and provenance on copies. Copies never replay effects or clone checkpoints.
- **Real isolation.** Use bubblewrap with Bash on Linux and AppContainer with PowerShell on Windows. Never replace OS isolation with host execution or require WSL.
- **Program data stays out of projects and the checkout.** Databases, saved keys, memory, undo copies and images live in the data directory (a per-user folder by default); Glassbox writes into a workspace only what the user or agent asked for.
- **Secrets stay on the server.** Resolve credentials from environment variable names at the moment of use. Keep secret values out of snapshots, prompts, logs, browser storage and Git.
- **Finish one vertical workflow at a time:** UI, API, persistence, execution feedback and tests together. Check the real service behaves as intended before broadening tests.

## Behavioral invariants

These are relied on by recovery, inspection and the security model. Changing one is a design change: update the architecture document and tests with it.

**Prompts**

- Compilation is pure and deterministic: same inputs, same messages, sources and hash.
- Blocks use only system, user and assistant roles; the runtime creates tool messages. Ordering follows [prompt compilation](architecture.md#prompt-compilation). The current input appears exactly once.
- Duplicate IDs or orders, undeclared or missing variables and malformed conditions are errors. Blocks are never silently dropped, re-roled or moved; each has an activation reason.
- Conditions use the restricted structured grammar. No `eval`, attribute access or template code.
- Provider adaptation that changes structure (such as extracting system messages) must be listed in the request's transformations; adaptation that would lose information is rejected.
- Over-budget context fails with a reason. Compaction is a recorded, reversible and source-checked job, started by the user or by the opt-in automatic setting; there is no hidden trimming.

**Execution**

- The loop is `model → tools → model` in one LangGraph graph with `thread_id = run_id`. Do not add another agent loop: a sub-agent is a separate child Run with its own snapshot and thread, driven by the same graph inside the parent's `delegate` call.
- Every model tool call keeps its `tool_call_id` and receives exactly one result before the next model call.
- The model sees only tools that are permitted in the frozen settings (and non-mutating ones in read-only mode). Permissions, schema validation and approvals are enforced on the server; prompts, files, tool output and model text cannot change them.
- Persist intent before an effect and the outcome after it. A call interrupted mid-effect is `outcome_unknown` and is never retried without an explicit decision. Do not claim exactly-once execution for external effects. The one automatic retry is a compaction summary request after a transient connection failure: it has no effect beyond cost, and every attempt is recorded.
- Budgets bound steps, tool calls, time and output. Cancellation awaits local cleanup; it does not claim to undo effects.
- Each Run binds to one frozen workspace root. Background processes belong to their original Run, conversation and root, never restart from records, and block archive, delete and workspace moves while running.

**Data**

- Edits carry the revision they were based on. Editing settings or presets never alters existing Runs.
- Record canonical messages, the prepared request and the actual outbound body separately for every model call. Records hold image references; the bytes are filled in only after the outbound body is recorded. Unknown usage and cost are `null`, never zero.
- Event sequence numbers are persistent; reconnecting reads state and never starts work. A pending submission keeps its body and idempotency key together across retries.
- Duplicates and forks copy saved evidence with new IDs and provenance; forks stop at a successful turn; folder deletion keeps its conversations.
- Exports and default trace views are redacted and labelled as such.

## Engineering conventions

- **Python 3.12+**, dependencies with `uv` (commit `uv.lock`), Pydantic v2 models at boundaries, type annotations on public interfaces. Ruff for linting, Black and isort (Black profile) for formatting, line length 100.
- **Frontend:** React, TypeScript (strict), Vite, TanStack Query; npm with `package-lock.json`; ESLint and Prettier. API types are generated from the backend schema, never written by hand. Styles use the design tokens at the top of `web/src/style.css`; add colors as tokens with light and dark values. The icon `web/public/favicon.svg` repeats the accent colors because an image cannot read the tokens; change both together.
- **Schema changes** go through an Alembic migration in `migrations/versions`. Never modify LangGraph's checkpoint tables directly.
- **Child processes** use `pathlib` and argument lists; never concatenate shell strings on the host side. Blocking file or Win32 work runs off the event loop.
- **Timestamps** are stored in UTC ISO format and displayed in local time.
- **Language:** identifiers, docstrings, comments and documentation in English. Public classes and functions have docstrings that say what they guarantee; comments explain constraints and non-obvious decisions rather than restating code.
- **Keep files at their functional paths:** documentation in `docs/`, bundled Skills in `src/glassbox/skills/`, test fixtures in `tests/fixtures/`. Never commit `.env`, databases, checkpoints, traces or credentials.

## Commands

From the repository root:

```bash
uv sync --dev --locked                 # install backend dependencies
npm --prefix web ci                    # install frontend dependencies
npm --prefix web run build             # build the UI served by the backend
uv run glassbox serve                   # run the app at http://127.0.0.1:8001
npm --prefix web run dev               # Vite dev server at :5173, proxying the API to :8001
```

Checks:

```bash
uv run pytest                                       # backend tests (offline)
uv run ruff check src tests scripts migrations
uv run black --check src tests scripts migrations
uv run isort --check-only src tests scripts migrations
uv run python scripts/check_layers.py               # dependency direction, Python and TS
uv run python scripts/check_docs.py                 # Markdown links, images and anchors
npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web run format:check
npm --prefix web run test -- --run                  # component tests
npm --prefix web run build
(cd web && npx playwright install chromium && npm run test:e2e)   # browser tests
```

The browser tests start their own server on port 8765 with the simulated model and temporary data under `var/`, and it serves the built UI, so run `npm --prefix web run build` after frontend changes and before `test:e2e`.

After changing an API model, regenerate the client types and commit `web/src/api/generated.ts` (the intermediate `schema.json` is ignored):

```bash
uv run python scripts/export_api.py
npm --prefix web run generate:api
```

## Testing strategy

| Layer | Where | What it should prove |
| --- | --- | --- |
| Pure logic | `tests/test_prompts.py`, parts of `test_tools.py`, `test_evaluation.py`, `test_permissions.py` and `test_reasoning.py` | Compiler ordering, conditions, variables, budgets; patch parsing and hunks; output buffering; eval grading and aggregation; command rules; reading reasoning text. |
| Runtime and API | `tests/test_api.py`, `test_runtime.py`, `test_harness.py`, `test_evaluation.py`, `test_permissions.py`, `test_review_regressions.py`, `test_file_undo.py`, `test_commands.py`, `test_subagents.py`, `test_attachments.py` | The real app over ASGI with the simulated or a mocked provider: idempotency, approvals and grants, cancellation, checkpoint recovery, uncertain effects, manual and automatic compaction, concurrency, origin checks, undo, slash commands, sub-agents, images. |
| Adapters | `tests/test_providers.py`, `test_responses.py`, `test_tools.py`, `test_sandbox.py`, `test_storage.py`, `test_config.py`, `test_data_location.py`, `test_downloads.py`, `test_shell_hooks.py`, `test_git.py`, `test_web.py`, `test_desktop.py` | Captured provider requests and errors for all three APIs, MCP over stdio and HTTP, real bubblewrap isolation (skipped without it), migrations and revisions, the default data folder, download redirects and hosts, memory outside the project, hook processes, host Git, web search and page reading over mocked transports, the WSL folder dialog. |
| UI components | `web/src/**/*.test.ts(x)` | Rendering and pure UI decisions: tool step pairing, preset editing, model choices, notifications, `/` and `$` completion and the send key, compaction placement, evaluation drafts, image helpers, the review task. |
| Browser | `web/e2e/glassbox.spec.ts` | End-to-end workflows against a real backend with temporary data. |
| Live service | `scripts/smoke_*.py` | Start a real HTTP server on a fixed port with disposable data and exercise actual OS effects. |
| Real model | `scripts/acceptance_*.py --live` | Opt-in work against the configured provider; may cost money. |

Tests run offline by default. Each test should protect a behavior someone relies on; avoid tests that only restate implementation details, and do not repeat passing checks without a reason.

Smoke scripts (each owns its port and a temporary directory, and calls no model service):

| Script | Port | Covers |
| --- | --- | --- |
| `smoke_workspace.py` | 8766 | Direct Run, retry identity, native commands and files, isolation, memory and Skills, read-only mode, cancellation |
| `smoke_conversations.py` | 8767 | Duplicate, fork, provenance, stale edits, archive, folders |
| `smoke_projects.py` | 8769 | Project-bound tools and context, workspace moves, frozen Run roots |
| `smoke_agent_tools.py` | 8770 | Search, ranged reads, instruction chains, patches, plans across restart |
| `smoke_context_processes.py` | 8771 | Compaction, copies, streamed commands, trusted runtimes, background processes, download policy (`--network --public-dns` adds a real download) |
| `smoke_harness.py` | 8772–8773 | CLI, JSON-RPC, embedding, hooks, packages, the Messages adapter over real HTTP |
| `smoke_desktop.py` | 8776 | Error envelopes, folder picker fallback (`--native` drives real Windows dialogs) |

Live acceptance (`acceptance_local_agent.py`, `acceptance_agent_tools.py` on port 8768, `acceptance_daily_harness.py` on 8775) needs a configured real provider and `--live`; run them one at a time. Evidence is kept under `var/`.

## Documentation

Keep `docs/` accurate when behavior changes: the [project guide](README.md) for users, [architecture](architecture.md) for design, this guide for conventions, [harness](harness.md) for programmatic interfaces. Dated records go in [history](history/README.md) and are not rewritten. `scripts/check_docs.py` validates every relative link and heading anchor. Source code carries the function-level detail in docstrings.

For documentation-only changes, check content, links and examples; application builds and test suites are unnecessary.

## Delivering changes

State what changed, how it was verified (with the actual results) and what remains unimplemented or unverified. Never report a check that was not run.
