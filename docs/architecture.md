# Architecture

Last updated 2026-10-08. This describes the implemented system. The [project guide](README.md) covers usage; the [development guide](development.md) covers conventions and commands.

## Overview

One local Python process serves the Web UI and API, runs the agent, and owns all persistent state. There are no other services.

```mermaid
flowchart LR
    UI[React UI] -->|HTTP, SSE| API[FastAPI]
    CLI[CLI / JSON-RPC / AgentClient] -->|HTTP| API
    API --> Studio[Studio use cases]
    Studio --> Compiler[Prompt compiler]
    Studio --> Runner[Runner: LangGraph loop]
    Runner --> Provider[Model adapter]
    Runner --> Tools[Tool registry]
    Tools --> Files[File tools]
    Tools --> Sandbox[Linux bubblewrap / Windows AppContainer]
    Tools --> MCP[MCP servers]
    Tools --> Web[Web search and pages]
    Runner --> Hooks[Shell hooks]
    Runner -->|delegate| Runner
    Studio --> DB[(app.db: SQLite)]
    Runner --> DB
    Runner --> CP[(checkpoints.db: LangGraph)]
```

The central idea: **a task becomes an immutable Run snapshot, and everything the agent does is recorded against it.** The snapshot freezes the preset, settings, tool schemas, conversation history, workspace context and the exact first request. The loop then alternates model calls and tool calls, persisting a receipt for each before and after its effect, so a page refresh, a restart or a crash can always show (and safely resume) what happened.

## Source layout

```text
src/rtagent/
  domain/            Values and pure logic: models, prompt compiler, patch parser, permission
                     and command-rule decisions, lifecycle sets, hook and execution contracts,
                     eval grading, net file changes for undo, slash command expansion, Git
                     status and diff parsing. No I/O.
  application/       Use cases over concrete adapters:
    studio.py          Submissions, previews, approvals and grants, recovery, projects,
                       packages, context and automatic compaction, undo, commands, Git,
                       attachments
    runner.py          The LangGraph model/tool loop, budgets, receipts, cancellation, shell
                       hooks and sub-agents
    context.py         Compaction requests and context usage views
    evaluation.py      Prompt evaluation views and grading of finished Runs
  infrastructure/    Everything that talks to something external:
    storage.py         SQLite schema access, transactions, events, history copies
    config.py          Process settings and .env provider seeding
    credentials.py     API keys saved from Settings (server-side only)
    tools.py           Tool catalog, schemas, dispatch, MCP sessions
    web.py             Web search services and reading public pages as text
    attachments.py     Attached images stored by hash
    shell_hooks.py     User-configured hook commands with bounded time and output
    providers/         OpenAI-compatible (chat.py), OpenAI Responses (responses.py),
                       Anthropic Messages (messages.py), model lists (catalog.py),
                       image references (attachments.py), errors
    workspace/         File tools and their undo journal, Skills, memory and commands
                       (files.py), downloads, Git status and diffs (git.py), trusted runtimes
    sandbox/           linux.py, windows.py, command output/stdin (command_io.py),
                       background processes (processes.py), Windows workspace copies (files.py)
    desktop/           Native folder picker
    packages.py        Preset and Skill package import/export
    tracing.py         Redacted views of evidence
  interfaces/        HTTP app and routes, AgentClient, CLI, JSON-RPC
  harness.py         Composition root: builds and owns Repository, ToolRegistry, Runner, Studio
  client.py, cli.py, main.py, __main__.py
                     Stable entry points that re-export the interfaces
  skills/            Bundled Skills (skill-creator, debugging, code-review)
migrations/          Alembic revisions for app.db
web/src/
  app/App.tsx        Workspace shell: selection, submission, panels, composer
  features/          chat, composer (model and access pickers), evaluation, history, projects,
                     prompts, inspection, workspace, settings, tools
  shared/            Json, Diff, StatusBadge, PackageActions, Popover, useDialogFocus
  api/               Generated types, HTTP client, SSE subscription
```

Dependencies point inward: `domain` imports nothing else from the project; `infrastructure` imports only `domain`; `application` imports `domain` and `infrastructure`; `interfaces` and `harness.py` may import everything. In the frontend, `shared` and `api` never import `features` or `app`, and features never import `app`. `scripts/check_layers.py` enforces both rules. Application code calls concrete infrastructure classes directly; interfaces are introduced only when a second implementation exists (providers are the one case).

## How a task runs

```mermaid
sequenceDiagram
    participant UI
    participant Studio
    participant Repo as Repository
    participant Runner
    participant Model
    participant Tool
    UI->>Studio: POST /runs (task, revisions, Idempotency-Key)
    Studio->>Repo: known key? return the original Run
    Studio->>Studio: read files, compile prompt, prepare request
    Studio->>Repo: create Run with snapshot, reserve conversation
    Studio->>Runner: start(run_id)
    Studio-->>UI: 202 RunView
    loop until the model requests no tools
        Runner->>Repo: save model receipt (executing)
        Runner->>Model: stream request
        Runner->>Repo: deltas, then receipt (succeeded)
        loop each requested tool
            Runner->>Runner: validate schema, decide allow / ask / deny
            Runner->>Repo: save tool receipt (executing)
            Runner->>Tool: execute
            Runner->>Repo: receipt with result, paired tool message
        end
    end
    Runner->>Repo: finish: append turn to history, release conversation
    UI->>Repo: SSE events, then GET /runs/{id}
```

**Submission.** `Studio.start` first resolves the idempotency key: a retry with the same key and body returns the original Run even if revisions changed since; the same key with a different body is a conflict. A new submission checks the conversation, preset and settings revisions the client observed, applies the Run's choices (below), captures workspace context, compiles, and prepares the provider request. `Repository.create_run` stores the snapshot and reserves the conversation in one transaction. Preview is optional: `POST /prompts/preview` saves the same compiled input for 15 minutes so a client can run exactly what it inspected, and `POST /prompts/inspect` compiles an unsaved draft without storing anything.

**Run choices.** A submission, preview, inspection or compaction may name a `model` (`provider_id`, `model`, `reasoning_effort`) and a workspace `access` (`read-only`, `ask`, `workspace-write`); omitted values use the saved default model and sandbox mode. `resolve_model` copies the provider's endpoint, key name and request options into a complete `ProviderConfig`, and `Studio._effective_config` applies the access to the saved permissions: read-only mounts the workspace read-only and removes tools that change files; ask turns every allowed tool that changes files, runs code or calls MCP into an approval. The result is frozen in the snapshot (`config.provider`, `access`), so later settings edits never change a Run, and Run views report the provider, model, effort and access that were used. Access only tightens: a tool denied in Settings stays denied.

**The loop.** `Runner` builds one LangGraph `StateGraph` with a `model` node and a `tools` node, checkpointed with `thread_id = run_id`. The graph state holds only this Run's new messages, the step number and the pending tool calls.

| Step | What happens | Why |
| --- | --- | --- |
| Rebuild input | Compile the snapshot plus this Run's messages; prepare the provider body. The first body must equal the snapshot's. | The model sees exactly what the snapshot promised. |
| Record intent | Save the model receipt with canonical messages and prepared body before sending. | A crash during the call leaves evidence of what was sent. |
| Capture the wire body | The HTTP client hook compares the outgoing JSON with the prepared body and refuses to send on mismatch. | The recorded request is the real one. |
| Stream | Deltas append to the Run's partial output with numbered events. | The UI follows progress; reconnects replay events. |
| Decide | Validate arguments against the frozen schema; look up allow/ask/deny; on ask, persist an approval and interrupt. | Authorization is data in the snapshot, never model text. |
| Execute | Save the tool receipt (executing), run it with the Run's frozen workspace root, save the result. | A replayed node reuses the receipt instead of repeating the effect. |
| Pair | Append a tool message with the original `tool_call_id`. | Every call gets exactly one result before the next model call. |
| Finish | When the model asks for no tools, append the task and messages to history and release the conversation in one transaction. | Only successful turns become future context. |

Budgets: model steps, total tool calls, a per-tool timeout and an active-time limit per Run (Settings → Run limits). Exceeding one fails the Run with a stated reason.

### Run states

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> running: worker slot free
    running --> waiting_approval: tool needs approval
    waiting_approval --> queued: approve or reject
    running --> succeeded: no more tool calls
    running --> failed: error or budget
    running --> interrupted: restart or unknown outcome
    interrupted --> queued: every uncertain call resolved
    queued --> cancelled: Stop
    running --> cancelling: Stop
    cancelling --> cancelled
    waiting_approval --> cancelled: Stop
    interrupted --> cancelled: Stop
```

A conversation has at most one active Run. Up to four Runs from different conversations execute at once (`MAX_ACTIVE_RUNS` in `runner.py`); further Runs wait as queued. A waiting approval releases its slot.

### Uncertain effects and recovery

If the process stops or a call times out while a model or tool call is executing, its outcome is unknown: the request may have reached the provider, the command may have changed files. Such receipts become `outcome_unknown` and the Run becomes `interrupted`. Resuming requires an explicit decision for every uncertain call: retry it, or (for tools) record a verified result. Nothing is retried automatically, and earlier attempts stay in the receipt's `attempt_history`. Stop and shutdown wait for local cleanup but cannot undo completed effects.

## Prompt compilation

Before compiling, `Studio._prepare` expands a custom slash command: input of the form `/name arguments` whose name matches a template in `.agents/commands/` (project) or `<data dir>/commands/` (global; a project file of the same name wins) becomes the template with `$ARGUMENTS` and `$1`…`$9` filled (`domain/commands.py`). The expansion is the task everywhere after that (inspection, preview, the snapshot's `input`, history); the snapshot also records `command` (`name`, `arguments`, `scope` and the template's hash), which Run views return and the conversation title uses. Input that names no saved command is unchanged.

`domain/prompts.compile_prompt` is pure: identical inputs give identical messages, sources and hash. Message order:

1. Preset blocks placed *before history*, by order.
2. Workspace context captured at submission: `runtime:environment` (root, sandbox, exposed tools, limits), root instructions, memory, Skill catalog, selected Skill bodies.
3. Committed conversation history (or a compaction summary plus recent turns).
4. Preset blocks placed *before input*, by order.
5. The task, exactly once.
6. This Run's assistant and tool messages.

Blocks have a role (system, user, assistant), optional provider/model allowlists and a condition (`all`/`any`/`not` over `field`/`op`/`value` with `eq`, `in`, `exists` on provider, model, task_type, tools or a variable). Templates substitute only declared `{{variables}}` of type string, number or boolean; there is no expression evaluation. Disabled or filtered blocks stay in the activation report with their reason. Duplicate block IDs or orders, missing variables and malformed conditions are errors, never silent omissions.

Token estimates are bytes ÷ 4. If Settings declare a context window, an estimate plus reserved output above it fails the compile; nothing is trimmed automatically. Actual usage comes from the provider and is `null` when unreported. The context view (`Studio.context`) also reports provider usage. `Repository.model_usage` reads every succeeded model call of the conversation and of its sub-agent conversations, in execution order, with each Run's model, adapter and context window from its snapshot. `context.session_usage` totals them (`session_usage`: requests, prompt, cache and output tokens, peak prompt, and a cache hit rate over the requests that reported cache figures) and lists the conversation's own requests (at most the last 200) for the per-request chart. Since every request resends the whole prefix, the latest request's prompt size is the current context fill (`live_usage`); totals of prompt tokens measure what was sent, not the size of the context. Every adapter surfaces cached-prompt tokens at the top level of `usage` (`cache_read_input_tokens`/`cache_creation_input_tokens`); the Messages API reports `input_tokens` as the uncached part, so the full prompt is their sum, while Chat Completions and Responses already count the cache inside `input_tokens`.

Built-in presets are seeded on start. A stored built-in preset is upgraded only if its content exactly matches a previously shipped version (`PREVIOUS_BUILTIN_HASHES`), so user edits are never overwritten.

## Data model

`app.db` (SQLite, WAL mode, managed by Alembic) holds application data; `checkpoints.db` belongs to LangGraph and is never edited directly.

| Table | Contents |
| --- | --- |
| `settings` | One versioned settings aggregate: providers (endpoint, adapter, key name, model list, temperature, output and context limits, per-model prices, the thinking option), the default model, tool permissions, command rules, default sandbox mode, runtimes, download hosts, MCP servers, automatic compaction, shell hooks, the web search service, limits. Stores credential *names* only. |
| `presets` | Versioned presets with embedded blocks. |
| `projects` | Registered workspace folders. |
| `conversation_folders` | Flat folders for organizing conversations. |
| `sessions` | Conversations: committed message history, revision, active Run, project, folder, archive flag, compaction pointers, standing approvals (`grants`), `kind` (`chat`, or the hidden `eval` and `subagent` sessions that host evaluation and sub-agent Runs) and, for sub-agents, `parent_session_id`. |
| `evals` | Evaluation definitions (cases with checks, variants with preset, model and access), versioned by revision. |
| `eval_batches` | Comparison runs: the frozen definition, status and each case/variant result with its Run ID, verdict and usage. |
| `previews` | Saved compiled inputs, deleted when consumed by a Run or expired. |
| `runs` | Immutable snapshot (including the expanded slash `command`, image `attachments` and, for a sub-agent, `parent_run_id`) plus the live projection (status, messages, partial output, approval, usage). |
| `events` | Numbered events per Run for SSE replay. |
| `calls` | Model and tool receipts: intent, prepared and wire bodies, decision, result, attempts, `granted_by`, shell `hooks` outcomes, for file tools the `file_changes` they made, and for `delegate` the `child_run_id`. |
| `compactions` | Summary jobs with source hash, provider calls, result and `trigger` (`manual` or `auto`). |
| `processes` | Background command records with bounded output. |

Outside the database, the data directory holds `credentials.json` (keys saved from Settings), `skills/` (global Skills), `commands/` (global slash commands), `file-history/` (earlier versions of files that Runs changed, for undo) and `attachments/` (images attached to tasks, named by their SHA-256). All are hidden from file tools and commands like the rest of the data directory.

Rules that keep this consistent:

- **Revisions.** Settings, presets, conversations and folders carry a revision; writes must name the revision they were based on, so a stale browser tab gets a conflict instead of overwriting.
- **One writer.** All writes go through `Repository` methods holding an asyncio lock for short transactions. A state change and its event commit together.
- **Snapshots are immutable.** Editing settings or presets never changes an existing Run. Run views read a few snapshot fields with `json_extract` instead of loading whole snapshots.
- **Copies are independent.** Duplicate and fork copy conversations, Runs and receipts with new IDs and `source_run_id` provenance, but never checkpoints, processes, grants or sub-agent conversations, and never re-execute anything. A copied `delegate` result still names the original sub-agent Run.
- **Deletion is idle-only** and removes the conversation's Runs, receipts, events and checkpoints, and its sub-agent conversations with theirs. Attached images stay in `attachments/`, since other conversations may reference the same file.

## Tools and permissions

`ToolRegistry` holds the catalog: twenty-three native tools (`delegate` is dispatched by the Runner), discovered MCP tools (namespaced `mcp_<server>_<name>_<hash>`) and trusted local tools registered by embedding code. At submission, the tools whose effective permission (after the Run's workspace access) is not `deny`, and in read-only mode that do not write, are frozen into the snapshot with their schema hashes. At dispatch:

1. The call is validated against the frozen JSON schema.
2. The frozen permission decides: allow, ask (persist approval, interrupt) or deny (return an error result). For the shell tool, the frozen `command_rules` can replace the tool default (`domain/policies.command_permission`; allow and ask rules only for commands without chaining, pipes, redirects or substitution). Before asking, the runner checks the conversation's grants (`sessions.grants`, added by an `approve_conversation` decision without a revision change, revoked by a revision-checked edit); a matching grant allows the call and is recorded in the receipt as `granted_by`. Grants only turn ask into allow.
3. The frozen `before_tool` shell hooks (`StudioConfig.hooks`, matched by a glob on the tool name) run in order, then an optional trusted `before_tool` guard. Either may add a denial, never grant one: a hook exiting 2 denies with its stderr as the reason, and any other non-zero exit, start failure or timeout denies as `hook_failed`.
4. The tool runs. MCP tools re-list the server's tools first and refuse to call if the schema hash changed; local tools are checked the same way.

**Undo file changes.** While a file tool runs inside a recorded call, `Workspace` journals each change to a workspace file: before replacing or deleting it, it copies the old bytes to `<data dir>/file-history/<sha256>` (content-addressed, so a copy is stored once), and it appends `{path, before, after}` (byte hashes, `null` for absent) to `ToolExecution.changes`. The runner saves that list in the call receipt as `file_changes`, also when the call fails part way. `domain/changes.net_changes` folds a Run's receipts into each path's state before the Run and the state it left; Run views list those paths as `changed_files`. `POST /runs/{id}/files/revert` (Studio `revert_files`, under the command lock, only for an ended Run in an idle conversation) restores a file only if its current bytes still hash to what the Run left, writing the saved copy atomically or removing a file the Run created; any other file is skipped and reported. The result is recorded as a `files.reverted` event. Commands, processes, MCP tools and Skills saved to the global folder are not journaled.

**Shell hooks.** `infrastructure/shell_hooks.run_hook` runs one user-configured command with the system shell in the Run's workspace, with event JSON on stdin, a minimal environment (`HOOK_ENVIRONMENT`: paths, user, locale, display session; never the service's other variables) plus `RTAGENT_HOOK_EVENT`, a timeout, and 16 KiB of kept output per stream. On POSIX it starts a new session, so a timeout, or output still held open a second after the hook exits, kills its whole process group. Outcomes are recorded in the call receipt (`hooks`: exit code, seconds, error, bounded stderr). `after_tool` hooks run only for dispatched calls, after the receipt is saved. `run_end` hooks run in a Runner task started by the terminal event (`run.completed`, `run.failed`, `run.cancelled`, each emitted once) and record `hook.ran` or `hook.failed` events. Hooks come from the Run's snapshot, never from current settings.

**Web tools.** `web_search` and `read_url` (`infrastructure/web.py`) run in the service with an HTTP client that ignores proxy variables. `web_search` calls the service in `StudioConfig.search` (Brave or Tavily with a key resolved by `CredentialStore`, or a SearXNG URL) and returns at most ten titles, URLs and tag-free snippets; it is left out of the offered tools while no service is set. `read_url` accepts HTTPS URLs without credentials or ports, resolves the host, requires every address to be public, and connects to that address with the original host name for TLS (as `fetch_url` does); it handles each redirect itself (at most three), applying the same checks to every hop. Bodies are read up to 2 MiB after decompression; text and JSON pass through, HTML is reduced to text by a stdlib parser that drops scripts, styles and other non-text elements; the result is cut at 48,000 characters.

MCP discovery runs when the user saves a server and automatically, once per server configuration, the first time prompts are prepared after a restart. Each MCP call opens a fresh session; servers run outside the sandbox under their own configuration.

## Workspace context

Each Run binds to one absolute workspace root, resolved from its conversation's project (or the default root) at submission and frozen in the snapshot. Moving an idle conversation affects only later Runs; historical Runs keep their root.

`Workspace` (`infrastructure/workspace/files.py`) owns file access under that root: path resolution that rejects traversal, links and private names; bounded reads (2 MiB files, 500 lines per call, 64 KiB results); hash-guarded writes and edits; literal search bounded to 10,000 entries, 1,000 files and 8 MiB; the patch format (`domain/patches.py` parses and applies hunks, the workspace preflights every file before writing any); memory; Skill discovery and validation across three scopes (bundled, global in the data directory, project in `.agents/skills`), where a name may exist in only one scope and a conflict is reported instead of shadowed; instruction chains with `AGENTS.override.md` taking precedence over `AGENTS.md` at each level.

Exclusions come from two lists: **private names** (`.env`, `.env.*` except `.env.example`, `.git`, `access-token`, database files) are hidden from everything, including commands; **dependency folders** (`.venv`, `node_modules`, `__pycache__`) are skipped by file tools and Windows copies but stay visible to Linux commands. The studio's own data directory is always hidden.

**Git status and diffs** (`infrastructure/workspace/git.py`) are for the user: the sandbox hides `.git`, so the agent never runs Git, and the Changes panel's review task carries the diff in its input. The studio runs `git` on the host with `-c core.fsmonitor=false -c core.hooksPath=<null device>`, `--no-ext-diff --no-textconv`, `GIT_OPTIONAL_LOCKS=0`, a minimal environment (paths, home and locale, so the user's global configuration still applies) and a 15-second timeout; it never writes. Status uses `--porcelain=v2 --branch -z --untracked-files=all` limited to the workspace folder (`domain/git.parse_status` strips the repository prefix; at most 2,000 files). Diffs compare the worktree with `HEAD` (the empty tree before the first commit), add untracked text files as new-file diffs, and are cut at 512 KB. Sections for private names (`private_name` on any path component), and for headers Git had to quote, are dropped and their paths listed as `omitted`; a single-file diff takes only a path the file tools could open.

## Command sandboxes

Both sandboxes take the same inputs (command, working directory, mode, timeout, trusted runtimes) and return the same JSON result: stdout, stderr, exit code, timeout and truncation flags.

**Linux (`sandbox/linux.py`).** Each command starts `bwrap --unshare-all --die-with-parent --new-session` with: read-only `/usr` and `/etc` plus the host's `/bin`, `/lib*` and `/sbin` layout; fresh `/proc`, `/dev`, `/tmp` and home; the workspace bound at `/workspace` (read-only in read-only mode); private files and the data directory masked by empty files and read-only tmpfs; trusted runtimes bound read-only at their host paths and at `/toolchains/N`; a cleared environment. The network namespace has no interfaces. A small `setsid` driver reports the process group ID before the untrusted command starts, so timeout and Stop can kill the whole group. Links in the workspace are allowed: they resolve inside the sandbox's own mount namespace, where masked paths stay masked and unmounted host paths do not exist.

**Windows (`sandbox/windows.py`).** Each command creates a new AppContainer profile with no capabilities (so no network), copies the workspace minus excluded paths into a private directory (64 MiB / 10,000 entries), grants the container access to that copy only, and starts Windows PowerShell suspended inside a kill-on-close Job Object limited to 32 processes. After all processes exit, changed files are copied back, failing if the host copy changed meanwhile. Original ACLs are never modified.

**Output.** `command_io.OutputBuffer` keeps the first 8,000 and last 16,000 characters of each stream and marks how much was dropped in between; the final result is trimmed further only if JSON escaping would exceed the 64 KiB tool limit. While a command runs, `CommandOutput` publishes snapshots of the bounded text to the Run (every 0.1 s) or process record (every 0.5 s); `tool.output` events only signal progress.

**Background processes (`sandbox/processes.py`).** `start_process` runs the same sandbox with a stdin pipe and returns once the process has started. At most four run at a time. Each belongs to its original Run, conversation and workspace root, has an absolute deadline no later than the Run time limit, and accepts stdin writes of up to 8 KiB each through a bounded queue. Stop, deadline, Run failure or cancellation, and service shutdown kill the process tree. A restart marks unfinished processes interrupted; they are never relaunched. Archiving, deleting or moving a conversation with a running process is refused.

## Sub-agents

The `delegate` tool is executed by the Runner, not the registry (`Runner.delegate`). From the parent Run's frozen snapshot it builds a child snapshot: the same preset, model, variables, workspace and captured context; no history; the task plus a note that the reply goes back to another agent; and a policy where every `ask` permission or command rule becomes `deny` and `delegate` is removed, so the child never pauses for approval and cannot nest. `Repository.create_child_run` creates the child Run in a new hidden conversation (`kind = "subagent"`, `parent_session_id` set; migration 0009), idempotent by the parent call ID, without the preview and revision checks of a user submission. The parent's receipt records `child_run_id` before the child starts, and Run views expose it on the active tool so the page can follow the child.

The child runs as `drive(..., nested=True)` inside the parent's tool call: it uses the parent's worker slot (a separate slot could deadlock when every slot holds a waiting parent), and it runs as its own asyncio task with an empty context, so the parent's LangGraph configuration cannot make it a subgraph. Cancelling the parent cancels the awaited child task; the child records `cancelled` and passes the cancellation on. The tool's deadline is the Run budget plus a minute; the child enforces its own budget. The result is JSON with the child's ID, status, final answer (cut at 48,000 characters), error and usage. On restart, queued sub-agent Runs are marked interrupted rather than started without their parent, whose call becomes `outcome_unknown` like any other. Context counts finished child Runs in the conversation's cost, and deleting a conversation deletes its sub-agent conversations.

## Image attachments

`POST /attachments` takes an image as the raw request body (at most 5 MiB); `AttachmentStore` identifies PNG, JPEG, GIF or WebP from the first bytes (never from the client's content type, and never SVG) and stores it as `attachments/<sha256>`. A submission names up to ten hashes; `_prepare` resolves them to `Attachment` records (`hash`, `media_type`, `size`) on the task message, the snapshot keeps them as `attachments`, and the committed user message carries them into history. The compile estimate adds 1,600 tokens per image.

Image bytes never enter messages, snapshots, previews or receipts. Adapters render references: Chat Completions `image_url` parts with `attachment-data-url:<hash>`, Messages `image` blocks (before the text) with `attachment-base64:<hash>`. The Runner loads the base64 data of the images in the request it is about to send into a context variable; after the request hook has checked the body against the prepared request and recorded it, `fill_attachments` replaces exactly those whole-string references in the outgoing body. Other strings, and hashes of images not in that request, are left as they are. `GET /attachments/{hash}` serves an image for the conversation view with its verified type, `nosniff` and a `default-src 'none'` policy.

## Context compaction

Compaction (`application/context.py`) summarizes all but the last N successful turns. It reserves the idle conversation, feeds the older messages to the configured model in UTF-8-safe chunks with no tools, and records every request like a Run's model calls. The summary activates only if the conversation's revision and the hash of the covered messages still match; it becomes a single user-role message labelled as derived, followed by the untouched recent messages. The originals remain in history, previous Runs are unchanged, and restoring full context just clears the pointer. Failed, cancelled or interrupted jobs never activate.

Automatic compaction is off unless enabled in `StudioConfig.auto_compact` (Settings › Context). When on, the Runner calls `Studio.schedule_auto_compaction` after a Run succeeds and commits; the check runs in its own task, so cancelling a Run never waits on it. If the conversation is idle and the estimated effective history (what the next Run would send) is at least `threshold_tokens`, it starts the same `compact` job with the finished Run's model and `keep_recent_turns`, recording `trigger: "auto"` on the job. Any reason not to compact (a new Run already started, a settings or revision change, too few turns, nothing new to cover) skips it; the next successful Run checks again. A Run submitted while a job runs is refused with a message to wait or stop it. Activation advances the conversation revision in the background, so the context view returns the `revision` it was read at and the Context panel's actions send that one; while automatic compaction is on, the page also refreshes the conversation 1 and 3 seconds after a Run ends, to see a job that started after the end-of-run refresh.

## Prompt evaluation

An evaluation compares prompts by running the same cases under each variant (the dialog calls them tasks and setups). It adds no execution path of its own: `Studio.start_batch` freezes the definition into an `eval_batches` row and starts a background task that, for each variant and case in turn, creates a hidden `kind="eval"` session, submits an ordinary `PreviewRequest` through `Studio.start` (so each item has a normal snapshot, receipts, trace and budgets), and polls the Run to a terminal state. Every item gets a fresh session, so no case sees another's history.

`domain/evaluation.grade` judges the Run's last assistant text against the case check (`succeeds`, `contains`, `not_contains`, `equals`, `regex`); it is pure and deterministic. A Run that did not succeed never passes. Items are saved as they finish, so polling `GET /batches/{id}` shows progress; per-variant aggregates (pass rate over graded items, average input/output tokens and active seconds, leaving unreported usage out rather than counting it as zero) are computed when a batch is read.

Batches are unattended: a Run that reaches `waiting_approval`, or outlives the Run time limit plus 30 seconds, is cancelled and graded as failed. An item whose Run cannot start (an unknown model, a missing variable, settings saved meanwhile) fails with that reason, and the remaining items still run; settings are read per item. Stopping a batch cancels its task and keeps finished items. Hidden sessions are excluded from `list_sessions` but keep their Runs inspectable by ID; deleting an evaluation deletes them with its batches. A batch for an evaluation already running is refused.

## Providers

A **provider** (`ProviderProfile`) is one endpoint: adapter, base URL, key variable name, known model IDs, request options (temperature, max output tokens, context window) and per-model prices (`ModelPrice`, US dollars per million tokens). `resolve_model` copies the chosen model's price into the Run's `ProviderConfig`, so cost is frozen with the snapshot. The runner stores each model call's `cost` (`call_cost`: uncached input, cache reads and writes, and output, each at its price; unset cache prices use the input price) and sums it into the Run's usage like tokens, `null` when any call lacks usage or a price. Settings hold any number of them plus a default model; `.env` values can seed the `openai` and `anthropic` providers at start-up (`Settings.apply_provider`). `catalog.list_models` reads a provider's `GET /models` (following the Messages API's pagination) to fill the model list. A default model that names a provider takes its connection from that provider; a `PUT /config` that changes the adapter under the same provider ID is rejected rather than silently rewritten.

**Keys.** `CredentialStore` resolves a key name to its value on the server only: a key saved in Settings (`credentials.json`, written atomically, mode 0600 on POSIX) first, then the process environment, then `.env`. The API accepts keys write-only and reports just their source and last four characters. Snapshots, traces, logs and the browser receive the name, never the value; provider errors redact the value before they are stored.

`providers/chat.py` prepares the request body with the same code for Preview and execution:

- **OpenAI-compatible:** LangChain's `ChatOpenAI` serializes Chat Completions with streaming and tool calls; retries are disabled. Reasoning effort is sent as `reasoning_effort` (low, medium, high). A request hook compares the real body with the prepared one before sending.
- **Anthropic Messages** (`providers/messages.py`): leading system messages become the `system` array, tool calls become `tool_use` blocks and results `tool_result` blocks. Effort is sent as `output_config.effort` (low through max); temperature is omitted unless set, because current Claude models reject non-default sampling. Two prompt-cache breakpoints (`cache_control: ephemeral`) mark the last system block, which also covers the tools rendered before it, and the last block of the newest message, so each step of a Run reads the unchanged prefix from Anthropic's cache; the marker moves forward with the conversation. A Run created before this change and resumed after it fails its first-request check rather than sending a different body. System messages inside the history, unsupported content blocks and incomplete streams are errors, and a classifier refusal (`stop_reason: "refusal"`) is reported as a declined request with its category.
- **OpenAI Responses** (`providers/responses.py`): messages become `input` items with their roles unchanged (user images as `input_image` parts), tool calls `function_call` items with the same `call_id` and JSON-encoded arguments, tool results `function_call_output` items; tools are `function` tools. Requests set `store: false`. With an effort set, `reasoning.effort` is sent and `include: ["reasoning.encrypted_content"]` requested; a response containing reasoning keeps its output items, without provider IDs (an unstored response cannot resolve them), in `Message.blocks`, sent back unchanged within the Run like Claude's thinking blocks. The stream supplies text deltas; the result comes from `response.completed` (or `response.incomplete`). Refusal parts, content filtering, `response.failed`, truncated function arguments and streams without a final response are distinct errors. Usage maps `input_tokens_details.cached_tokens` to cache reads; as with Chat Completions, input tokens already include them.
- **Simulated:** offline canned replies that drive real tools from `/read`, `/echo` and `/tool` commands; `/think` returns a thinking block, `/slow` streams slowly and `/error` fails like a provider. Explicitly selected only.

**Thinking blocks.** Current Claude models think by default and return signed `thinking`/`redacted_thinking` blocks. The adapter keeps an assistant turn's exact content array (`Message.blocks`) and sends it back unchanged for the tool continuations of the same Run, as the API requires. When the turn becomes conversation history, the blocks are dropped and only text and tool calls remain: a signature is bound to the conversation prefix and model that produced it, and each Run recompiles its system prompt (memory, instructions, Skills may have changed) and may use another model, so replaying older blocks would be rejected. Stripping every earlier block keeps each request valid; within a Run the history is append-only. Browser views omit the blocks but return their readable reasoning (`reasoning_text`: thinking text, Responses summary and reasoning text; never signatures or encrypted content) as `MessageView.reasoning`. Current Claude models return empty thinking text unless the request sets `thinking: {type: "adaptive", display: "summarized"}`, and the Responses API returns summaries only for `reasoning.summary`; the provider option `show_reasoning` (frozen into `ProviderConfig`) adds those parameters. A Responses reasoning item without encrypted content (no effort set) is kept for its summary but not echoed, since an unstored request cannot resolve it.

Failures report the provider's message and HTTP status with credentials redacted (`providers/errors.py`). Tool arguments cut off by the output token limit get a specific error.

## HTTP API

All routes live under `/api/v1` (`interfaces/http/api.py`); `/openapi.json` is the exact contract and `web/src/api/generated.ts` is generated from it.

| Area | Routes |
| --- | --- |
| Startup | `GET /health`, `GET /bootstrap` (settings, presets, conversation summaries, folders, projects, evaluation summaries, tool catalog) |
| Settings and tools | `PUT /config`, `POST /tools/refresh`, `PUT /presets/{id}`, preset export/import |
| Models and keys | `POST /providers/models` (list a provider's models), `GET /credentials?name=…` (status only), `PUT/DELETE /credentials/{name}` |
| Global Skills | `GET /skills`, `GET/DELETE /skills/{name}`, `PUT /skills` |
| Attachments | `POST /attachments` (raw image body), `GET /attachments/{hash}` |
| Workspace | `GET /workspace`, `PUT /workspace/memory`, Skill get/save/export/import, `GET /workspace/commands`, `GET /workspace/git`, `GET /workspace/git/diff` (optional `path`), all with optional `project_id` |
| Projects | `POST /projects`, `POST /desktop/folder-picker` |
| Conversations | `POST /sessions`, `GET/PATCH/DELETE /sessions/{id}`, `/copy`, `/runs`, `GET /context` (`PUT` restores full context), `/compact`, `GET /compactions/{id}` and `/cancel`, `GET /processes`, `/processes/{id}/input` and `/stop` |
| Folders | `POST /folders`, `PATCH/DELETE /folders/{id}` |
| Prompts | `POST /prompts/inspect` (unsaved draft), `POST /prompts/preview` (saved, 15 minutes); both accept `model` and `access` |
| Runs | `POST /runs` (with optional `model` and `access`), `GET /runs/{id}`, `/cancel`, `/approvals/{id}`, `/resume`, `/trace`, `/export`, `/events` (SSE), `/event-log` (pages), `GET /files` and `POST /files/revert` (undo file changes) |
| Evaluations | `GET/POST /evals`, `GET/PUT/DELETE /evals/{id}`, `GET/POST /evals/{id}/batches` (`?project_id=`), `GET /batches/{id}`, `POST /batches/{id}/cancel` |

Errors share one envelope: `{"error": {"code", "message", "details", "request_id"}}`. Validation errors list field locations without echoing submitted values; unexpected errors log their stack locations under the request ID.

**Events.** Each Run's events have consecutive sequence numbers. `GET /runs/{id}/events` replays from a cursor (`after` or `Last-Event-ID`) and follows until the Run ends; reconnecting never starts work. Event payloads are redacted; command output is read from the Run view rather than carried in events.

**Local access boundary.** The app binds to loopback. `TrustedHostMiddleware` accepts only loopback `Host` headers (defeating DNS rebinding), and browser requests to the API must come from the page's own origin or a configured development origin (`STUDIO_ALLOWED_ORIGINS`, Vite's port by default); cross-site fetches are rejected. There is no authentication: anything that can reach loopback as the user can use the API.

## Frontend

React with TanStack Query; `api/generated.ts` types every request. `App.tsx` owns the selection (project and conversation), the composer, the pending submission and which side panel is open. Server state is cached per query: `bootstrap`, `runs/<conversation>`, `trace/<run>`, `workspace/<project>`, `context/<conversation>`, `processes/<conversation>`, `git/<project>` and `git-diff`, `run-files/<run>`, `commands/<project>`, `subagent/<run>` (a delegated Run followed from its step), plus Settings and evaluation queries.

While a Run is active, `useRunEvents` follows its SSE stream and refetches only that Run's projection (at most one request in flight, coalesced to 100 ms); broader queries refresh at lifecycle boundaries (tool completion, approvals) and when the Run ends. A slower poll covers a lost event stream. The Inspector loads the trace, which can be large, only while it is visible; the prompt preview compiles (debounced, through `POST /prompts/inspect`) only while its tab is visible and no Run is active.

The conversation view also reads the context view while a conversation has turns, to draw each compaction as a marker (`CompactionMarker`) after the turn where its covered history ends: every succeeded Run adds its task plus its messages to history, so the running count of those reaches the summary's `covered_messages` at that turn (`compactions.compactionsAfter`). The query polls while a summary runs and refreshes when the conversation's compaction pointers change.

The task box (`TaskInput`) completes a leading `/command` from `GET /workspace/commands` and a `$skill` mention from the workspace view's Skill catalog, as a listbox driven by the arrow keys, Enter or Tab and Escape. Otherwise the send key runs the task; a key press that confirms an input method composition never does.

Settings (sidebar) edit global configuration in pages: Providers and keys, Models, Tools (with command rules and the web search service), MCP servers, Skills, Sandbox, Context, Hooks and Run limits. Configuration pages share one versioned draft saved together; keys and global Skills save immediately through their own routes. The Composer page holds per-browser preferences (the send key, `studio.sendKey`), which apply at once and are not part of the settings record. The composer's model and access pickers hold the choice for the next Run in `localStorage` (`studio.choice`, IDs only) and adopt the last Run's choice when a conversation opens; the Tools panel shows permissions with the selected access applied, mirroring the server's transformation. The notification preference (`studio.notify`) and the open conversation and project (`studio.session`, `studio.project`) are kept in `localStorage` too; notifications fire only for status changes observed after the page loaded, and only while the page is hidden.

A lost submission response is retried with the same body and idempotency key, even if the conversation advanced meanwhile, so it cannot create a second Run. Prompt edits are page-local drafts with undo history until saved. Styles use CSS custom properties with light and dark themes following the system setting.

## Clients and extensions

`AgentClient`, the CLI and JSON-RPC attach to a running service over HTTP; they never open its databases. `Harness` lets a Python program own the full stack instead (with its own data directory), register trusted local tools, and observe events or add tool denials through `Hooks`. See [Harness interfaces](harness.md).

## Design decisions and limits

- **One process, SQLite, no queue.** Sufficient for a single local user; LangGraph checkpoints plus receipts make restarts safe without a broker.
- **No hidden context management.** Over-budget prompts fail with a reason; compaction is a recorded, reversible action, started from the Context panel or, when the user enables it, automatically after a Run, never by silently trimming a prompt.
- **No claimed atomicity across systems.** A model call, a file write, `app.db` and `checkpoints.db` cannot commit together; receipts and explicit uncertainty handle the gaps.
- **Text is data.** Files, tool output, memory, Skills and model replies cannot change permissions or settings.
- **Not a security boundary against the local user.** The sandbox protects the host from agent commands; it does not authenticate local API clients. MCP servers, trusted local tools and shell hooks run with the user's own authority. Hooks are commands the user typed in Settings; the model can supply only their stdin data, never their command line.
- **Not implemented:** terminal emulation, command networking, disk quotas, stateful MCP sessions, multi-user deployment, undo of command changes, approvals inside sub-agents, streaming of thinking while it is generated.
