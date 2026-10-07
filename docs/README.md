# Security Agent Studio

A local agent workspace for coding, debugging, reverse engineering, CTF challenges and other authorized security work. You give a task; one tool-using agent works in a project folder, runs commands in an OS sandbox without network access, and records the exact model input, every request and every tool result so you can inspect what happened.

What makes it different from a plain chat agent:

- **Inspectable prompts.** Instructions are composed from editable blocks. **Preview** shows the exact messages, sources and provider request before anything runs; every Run keeps an immutable snapshot of its input.
- **Recorded evidence.** Each model call stores the canonical messages, the prepared request and the actual outbound body. Each tool call stores its arguments, permission decision and result.
- **Explicit authority.** Tools are allowed, require approval, or are disabled in Settings. Prompts, files, memory and model output can never change those permissions.
- **Real isolation.** Commands run in bubblewrap namespaces on Linux or an AppContainer on Windows, never as unrestricted host processes.

Contents: [Install](#install-and-start) · [Configure a model](#configure-a-model) · [Use the studio](#use-the-studio) · [Projects, memory and Skills](#projects-memory-and-skills) · [Isolation and permissions](#isolation-and-permissions) · [Troubleshooting](#troubleshooting) · [Status and roadmap](#status-and-roadmap)

Further reading: [Architecture](architecture.md) for how it works, [Development guide](development.md) for contributing, [Harness interfaces](harness.md) for the CLI, JSON-RPC and Python APIs, and the [2026-10-06 review](review.md) for the latest assessment.

## Install and start

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 22.12+ with npm. Linux additionally needs Bash and bubblewrap:

```bash
sudo apt-get install bash bubblewrap util-linux   # Debian/Ubuntu
```

Windows needs nothing extra: commands use the built-in Windows PowerShell inside an AppContainer. WSL, a VM or administrator setup are not required.

From the repository root:

```bash
uv sync --dev --locked
npm --prefix web ci
npm --prefix web run build
cp .env.example .env          # optional; PowerShell: Copy-Item .env.example .env
uv run rtagent serve          # or: uv run python -m rtagent
```

Open **http://127.0.0.1:8001**. Use `uv run rtagent serve --port 8080` for another port. The service listens on loopback only and has no login; it is a single-user local tool, not a server to expose on a network.

Start-up applies database migrations. Runtime data lives in `STUDIO_DATA_DIR` (default `var/`), which Git ignores.

## Configure a model

The left sidebar's **Settings** holds global configuration; the controls under the message box choose the model and workspace access for each Run.

1. **Settings → Providers** lists the endpoints the studio can call: OpenAI-compatible (streaming Chat Completions with tool calls) and Anthropic Messages. Add one with **Add OpenAI-compatible** or **Add Anthropic**, set its base URL, paste the API key and press **Save key**. A local endpoint without a key works too.
2. **Settings → Models** lists each provider's models. **Refresh models** asks the provider for its model list (`GET /models`); a model ID can also be added by hand. Each provider also sets temperature, max output tokens and context window, and **Prices** takes each model's input, output and cache prices in US dollars per million tokens. The **Default model** applies to new conversations and to the CLI and API.
3. The **model button** under the message box picks the model for the next Run, sets its **reasoning effort** (Low/Medium/High for OpenAI-compatible models; up to Extra high and Max for Anthropic), refreshes all model lists, or opens **Manage models…**. A conversation reopens with the model and access its last Run used.

Keys are stored on this computer in the data directory (`credentials.json`; on Linux readable only by your account, on Windows protected by the folder's permissions like `.env`) and are never sent back to the browser; Settings shows only where a key comes from and its last four characters. Without a saved key, the server reads the environment variable named under **Key variable name** (for example `OPENAI_API_KEY`), from the process environment or `.env`. **Remove saved key** falls back to that variable.

`.env` can also seed providers at start-up, which is convenient for scripts:

```dotenv
STUDIO_PROVIDER=openai             # or anthropic, or fake for the simulated model
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5
OPENAI_API_KEY=sk-...
```

`ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL` and `ANTHROPIC_API_KEY` do the same for an `anthropic` provider. Values set in `.env` update their provider and the default model again at the next start.

Each Run records the provider, model, effort and access it used, so its snapshot stays reproducible after settings change. With Anthropic models the Run keeps the model's thinking blocks between its own tool calls, as the Messages API requires; later turns receive the conversation without them. Leave temperature empty for models that reject it, such as current Claude models. Requests to Claude models ask Anthropic to cache the prompt prefix, so the steps of a Run after the first are cheaper and faster; **Context** shows the resulting cache hit rate. OpenAI-compatible providers cache long prompts on their own.

If a request fails, the error states the provider's reason and HTTP status (with any credential removed). Nothing is retried automatically, and a missing model is a configuration error rather than a silent switch to simulation.

For offline development, choose **Simulated model** in the model picker or set `STUDIO_PROVIDER=fake`. It answers with canned text but runs real tools: `/read README.md`, `/echo hello` (which requires approval), `/tool bash {"command":"ls"}` (Linux) or `/tool powershell {"command":"Get-Location"}` (Windows), and `/slow` to try **Stop**.

## Use the studio

### Run a task

Type a task in the message box and press **Run** or **Ctrl+Enter** (**Cmd+Enter** on macOS). The empty conversation suggests a few starting tasks. The preset selector beside the box switches between the built-in presets:

| Preset | Focus |
| --- | --- |
| Security analysis (default) | Trace attacker-controlled input to sensitive operations; report evidence and impact. |
| General agent | Ordinary project work from inspection to a concrete result. |
| Coding | Implement within the existing architecture and verify with real commands. |
| Debugging | Reproduce first, change one cause at a time, re-run the reproduction. |
| Code review | Report actionable findings with locations; do not modify files unless asked. |

Presets change how the agent works, not what it is allowed to do. Mention `$skill-name` in a task to load a Skill (see below).

**Custom commands** are prompts you reuse. Save a Markdown file as `.agents/commands/<name>.md` in the workspace (or in `commands/` in the studio's data directory, for every project) and type `/name arguments` as the task. Typing `/` lists the available commands; **Tab** completes the first match. In the template, `$ARGUMENTS` stands for everything after the name and `$1` to `$9` for single words; arguments given to a template without placeholders are added at the end. Optional frontmatter sets what the list shows:

```markdown
---
description: Review one file for bugs
argument-hint: <path>
---
Review $1 for correctness and security. Report each finding with its line.
```

The turn shows the command as you typed it, with the text actually sent under **Sent as**; editing the file later does not change past Runs. A project command replaces a global one with the same name. Input that names no saved command is sent unchanged.

Beside the preset are the model button (see [Configure a model](#configure-a-model)) and **Workspace access**, which sets what the Run may change:

| Access | Effect |
| --- | --- |
| Read only | Commands see the workspace read-only and the model is not offered file-changing tools. |
| Ask before changes | Allowed tools that edit files, run commands or call MCP servers wait for your approval. Reading and searching stay allowed. |
| Workspace write | Tools run as permitted in **Settings → Tools**. |

Access only tightens permissions; a tool disabled in Settings stays disabled. Commands always run in the sandbox without network access.

While the agent works, its reply streams into the conversation. Each tool call appears as one line: the tool, what it acts on (a path, command or search), and its outcome (done, failed, exit code, needs approval). Expand a line to see the arguments and the result: command output, file contents, search matches or a colored diff. Long command output keeps its beginning and end, with an explicit marker for the omitted middle.

When a tool requires approval, the Run pauses with a card showing the exact arguments. **Approve once** covers only that call. **Allow … in this conversation** also lets later matching calls in the same conversation run without asking: for the shell tool it covers commands starting with the same program (for example `pytest`, as single commands only), for other tools every call. Standing approvals are listed under **Tools → Allowed in this conversation**, where **Revoke** removes them; copies of a conversation start without them. **Reject** returns an error to the model, which can continue another way. Approvals survive a page refresh or service restart.

**Stop run** cancels the Run and waits for its processes to be cleaned up. It does not undo files that were already written; use **Undo file changes** on the turn (see [File tools](#file-tools)).

With prices set, each Run's cost appears in **Inspector → Run trace**, **Context** shows what the conversation has spent so far, and evaluations report the average cost per case. A Run keeps the prices it started with; Runs without a price or reported usage are counted, not priced at zero. Compaction requests are not included.

**Notifications** in the sidebar asks the browser for permission and then notifies you when the open conversation's Run needs approval, finishes, fails or is interrupted while the tab is in the background. The setting is kept in this browser only.

### Inspect what happened

**Inspector → Run trace** lists every model request and tool call of a turn in execution order. For a model request it shows the canonical messages with their sources, the prepared request, the actual outbound body, usage and provider metadata. The view is redacted by default; **Show original trace** reveals the local original, and **Export JSON** downloads a labelled, redacted copy.

**Preview** (or **Inspector → Prompt preview**) compiles the current draft without running anything: final message sequence, block activation, prepared request and the tool schemas the model would receive. It updates as you edit and never blocks **Run**.

### Edit prompts

**Prompt** opens the preset editor. Blocks have a role (system, user or assistant), a position (before the conversation history or just before your message), optional `{{variables}}` and activation rules. Workspace instructions, memory and Skills appear in their real position between the blocks. Edits are drafts with **Undo**, **Redo** and **Discard** until you **Save preset**; saving creates a new revision, and earlier Runs keep the version they used. **Export**/**Import** moves presets between installations.

### Evaluate prompts

**Evaluations** (sidebar) compares prompts on the same tasks. An evaluation has:

- **Cases:** a task plus the condition that marks it passed: the final reply contains, does not contain, equals or matches a regular expression, or simply that the Run succeeds. Checks are plain text comparisons (with an **Ignore case** option), not a model judge, so the same reply always gets the same verdict.
- **Variants:** the prompts under comparison. Each picks a preset and can override the model and the workspace access.

**Run comparison** saves the evaluation and runs every case once under every variant, one after another, in the selected workspace. Each run is an ordinary Run with its own snapshot and trace, kept in a hidden session so it does not appear among your conversations. The results show each variant's pass rate, its average input and output tokens and active time, and a case × variant grid; select a cell to see its verdict, reason and final reply. Earlier comparisons stay available from the selector beside **Results**.

Comparison runs are unattended. A Run that stops for approval is cancelled and counts as failed, so variants offer only **Read only** or **Workspace write** access. Runs use the selected models and may cost money; **Stop comparison** cancels the remaining runs and keeps the finished ones. Deleting an evaluation also deletes its runs. Case input variables can be set through the API (`variables` on a case) but not yet in the dialog.

### Manage conversations

The sidebar's **Conversations** section lists the selected workspace's conversations, with **New conversation** to start another there, search, an **Archived** filter and flat folders. A conversation's **…** menu can rename, move to a folder, change its workspace, duplicate, fork from an earlier turn, archive or delete it. **Fork here** on a turn does the same from that point. Copies share no state with the original and never repeat model or tool calls.

**Context** reports how the conversation uses the model. After the first request it shows the provider's accounting for the latest one: the prompt size against the model's context window (with a fill bar), the share of that prompt the provider served from its cache (cache hit rate), and the output tokens; a provider that reports no usage, including the simulated model, says so rather than showing zeros. Below that, estimated history size, and **Compact older turns** asks the model for a summary of all but the most recent turns. The original messages are kept, and **Restore full context** switches back. To compact without asking, turn on **Settings › Context › Compact automatically**: after a run, if the history the next run would send is over the size you set, older turns are summarized with that run's model, keeping the recent turns you chose. Automatic summaries cost model calls, are listed in Context as *Automatic*, and are restored the same way.

**Processes** lists background commands the agent started, with their output, and lets you send input lines, close stdin or stop them.

## Projects, memory and Skills

A **project** is a registered folder. **Projects → + (Add project) → Choose folder** opens the folder picker of the computer running the service: Windows Explorer on Windows and under WSL, zenity or kdialog on a Linux desktop. **Enter a path** types a path instead (also the fallback when no picker is available); tick **Create the folder if it does not exist** to start a project in a new folder. A path that does not exist is otherwise reported rather than created, so a typo does not become an empty project.

Under WSL, a folder chosen in Explorer is translated to its Linux path: `\\wsl.localhost\<distro>\home\you\app` becomes `/home/you/app`, and `C:\work\app` becomes `/mnt/c/work/app`. Prefer folders inside the distribution; Windows drives are much slower to read from WSL. Folders in another distribution or on a network share cannot be used.

Each conversation belongs to one project, or to the default workspace (`STUDIO_WORKSPACE_ROOT`, the current directory by default). Selecting a project starts a new conversation there; an idle conversation can be moved with **Change workspace**. Each Run records the folder it used.

At the start of each Run, the agent receives:

- **Instructions:** the first non-empty `AGENTS.override.md` or `AGENTS.md` at the project root. The `read_instructions` tool loads the chain for deeper directories, where deeper files take precedence.
- **Memory:** `.rtagent/MEMORY.md`, editable in **Workspace → Memory** and through the agent's `read_memory`/`save_memory` tools. It is plain Markdown; there is no hidden summarization.
- **Skill catalog:** the name and description of each Skill. The agent loads a Skill's full text with `load_skill` when it matches the task, or you select it in **Workspace → Skills** or mention `$name`.

Skills are folders with a `SKILL.md`:

```markdown
---
name: evidence-review
description: Review workspace evidence and prepare a report with file references.
---

Read the evidence, distinguish facts from assumptions, and cite file paths.
```

Skills come from three places, and a name refers to exactly one Skill:

- **Bundled:** **skill-creator** (writes and verifies a new Skill), **debugging** and **code-review**. Read-only.
- **Global:** created in **Settings → Skills** and stored in the data directory (`skills/<name>/`); every project can use them.
- **Project:** `.agents/skills/<name>/` in the project folder, managed in **Workspace → Skills** or saved by the agent with `save_skill`.

A project Skill cannot reuse the name of a bundled or global Skill; the conflict is reported instead of one silently replacing the other. Skills are text; they cannot grant permissions or change the sandbox.

## Isolation and permissions

### Tool permissions

**Tools** lists every tool with its argument schema and its status for the workspace access currently selected: Allowed, Approval required, Disabled, or Blocked by read-only. **Settings → Tools** sets each tool's default permission, and **Command rules** give shell commands that start with a prefix their own permission (allow, ask or deny; the longest prefix wins). Allow and ask rules apply only to a single command without `;`, `|`, `&&`, redirects or `$( )`, so a rule for `npm test` never covers `npm test; rm -rf data`; deny rules match any command that starts with the prefix. Under **Ask before changes**, allow rules ask instead. Rules and standing approvals only decide whether to ask; commands still run in the sandbox. New MCP tools start disabled; starting background processes, sending them input and downloading files start at "ask".

The native tools are: `list_files`, `search_files`, `search_text`, `read_text`, `read_instructions`, `write_text`, `edit_text`, `apply_patch`, `update_plan`, `bash` (Linux) or `powershell` (Windows), `start_process`, `poll_process`, `send_process_input`, `stop_process`, `fetch_url`, `read_memory`, `save_memory`, `load_skill`, `save_skill` and `echo`.

**Workspace access** under the message box tightens these per Run (see [Run a task](#run-a-task)). **Read only** mounts the workspace read-only for commands and removes every file-changing tool from what the model is offered.

### File tools

File tools take relative paths, reject `..`, links and absolute paths, and never touch credentials (`.env`, `.env.*` except `.env.example`), Git metadata, databases (`.db`, `.sqlite`), the studio's data directory, or dependency folders (`.venv`, `node_modules`, `__pycache__`). Replacing a file requires the hash from the last read, so a concurrent change is detected instead of overwritten. `apply_patch` validates every file before changing any; each file is replaced atomically, but a multi-file patch is not a transaction, and a failure reports which files were already changed.

A turn whose file tools changed files lists them under **Changed N files**. **Undo file changes** puts each file back as it was before that run, including removing files it created and restoring files it deleted, but only while the file is still as the run left it: a file changed since, by a later run, a command or you, is left alone and named. Changes made by shell commands and background processes are not tracked and cannot be undone this way; use Git for those.

### Commands

| | Linux | Windows |
| --- | --- | --- |
| Shell | Bash in bubblewrap namespaces | Windows PowerShell in a fresh AppContainer |
| Workspace | Mounted at `/workspace`; edits apply directly | Private copy; edits copied back after the command exits, with conflict checks |
| Hidden | Credentials, `.git`, databases and the data directory are masked | Same, and dependency folders are not copied |
| System | `/usr` and `/etc` read-only; private `/tmp` and home | Normal AppContainer access to shared Windows resources |
| Network | None (private network namespace) | None (no AppContainer capabilities) |
| Cleanup | Process group killed on exit, timeout or Stop | Job Object (max 32 processes) terminated |

The command tool is `bash` on Linux and `powershell` on Windows. A data directory moved between the two (for example from Windows to WSL) keeps its shell permission: at start-up it is carried over to the native tool.

Commands also have a deadline, bounded output and no access to server credentials or the host `PATH`. If the sandbox is unavailable, commands fail; they never fall back to running on the host. **Workspace** shows whether the sandbox works.

On Linux, dependency folders such as `.venv` and `node_modules` stay visible so a project's tests can run offline. A virtual environment's interpreter usually links outside the project; add that interpreter's folder (the parent of `home` in `.venv/pyvenv.cfg`) under **Settings → Sandbox → Trusted runtime directories** and it is mounted read-only at the same path. On Windows, trusted runtimes are copied into the sandbox (up to 20,000 files and 256 MiB). At most three can be registered.

### Downloads and MCP

`fetch_url` downloads a public HTTPS file (up to 16 MiB, optionally checked against a SHA-256) from hosts you list in **Settings → Sandbox → Allowed HTTPS download hosts**. It refuses redirects, proxies, credentials in URLs and private or local addresses, and writes only new files. It does not give commands network access.

MCP servers (stdio commands or Streamable HTTP URLs) are configured in **Settings → MCP servers** and run **outside** the sandbox, with their own permissions. **Save & discover** lists each server's tools on the same page, where you set their permissions; after a restart they are discovered again automatically the first time they are needed.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| "No model is selected…" when running | Choose a model under the message box. If the list is empty, add the provider's models in **Settings → Models**. |
| "Model request failed (HTTP 401): …" | The provider rejected the key, or none was found. **Settings → Providers** shows where the key comes from; paste and save it there. |
| "… does not accept reasoning effort …" | The selected effort is not supported by that provider's API. Pick another level or Default. |
| A Claude model rejects temperature | Leave **Temperature** empty in **Settings → Models**. |
| "The model reached its N-token output limit…" | A tool call (often a large file write) was cut off. Raise **Max output tokens**. |
| Workspace shows the sandbox as unavailable on Linux | Install `bubblewrap`; check that unprivileged user namespaces are enabled. |
| "Excluded paths cannot be links…" | A credential-like name (such as `.env`) is a symlink; replace it with a regular file. |
| A command cannot find `python`, `node` or a project venv | Register the runtime under **Settings → Sandbox → Trusted runtime directories**. |
| Run stays "queued" | Four Runs from other conversations are active; it starts when one finishes. |
| A Run is "interrupted" | The service restarted, or a call's outcome is unknown. Review each uncertain call and choose **Retry** or record a verified result before resuming. |

## Status and roadmap

As of 2026-10-07:

- Stages 1–4 below are implemented, and stage 6 in part (prompt evaluations). Real-model acceptance ran on Windows ([records](history/README.md)); the Linux sandbox is covered by automated tests against real bubblewrap, but a live-model Linux acceptance and a check against Anthropic's hosted API have not been run. The Messages adapter, thinking replay and reasoning effort are tested against recorded protocol fixtures.
- Not implemented: full terminal emulation (PTY/ConPTY), command network access, disk quotas, stateful MCP sessions, specialized reverse-engineering integrations.

| Stage | Scope | Status |
| --- | --- | --- |
| 1 — Inspectable execution | Web workspace, prompt blocks, provider boundary, agent loop, MCP, traces, approvals, recovery | Done |
| 2 — Working local agent | Direct Run, projects, native file tools and commands with OS isolation, instructions, memory, Skills | Done |
| 3 — Daily agent use | Context compaction, conversation branching, diffs, streamed command output, background processes, dependency/download policy | Done |
| 4 — Extensible harness | CLI, JSON-RPC, Python client and embedding, hooks, trusted local tools, preset/Skill packages, Messages provider | Done |
| 5 — Security analysis workflows | Stateful MCP, IDA/Ghidra/debugger/browser integrations, evidence artifacts, analysis Skills | Planned |
| 6 — Evaluation | Prompt comparisons over task sets with deterministic checks and token/time reporting (done); replay of recorded Runs, model-judged grading, cost in currency (planned) | Partial |

Later stages should add capabilities with a real workload and acceptance criteria each, while keeping the single-user local path simple.
