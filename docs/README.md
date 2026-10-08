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

Start-up applies database migrations. The studio keeps its data (conversations, settings, saved keys, memory, global Skills and commands, undo history, images) in a folder of your own, never in a project or in this checkout: `~/.local/share/rtagent` on Linux and WSL (`$XDG_DATA_HOME/rtagent` if that is set), `%LOCALAPPDATA%\rtagent` on Windows, `~/Library/Application Support/rtagent` on macOS. Set `STUDIO_DATA_DIR` to use another folder.

Earlier versions kept this data in `var/` under the folder the studio was started from. The first start with the default folder moves the studio's own files from there (other files in `var/` stay, and `var/MOVED.txt` says where the data went). If the old data is still open, for example because another studio is running from that folder, start-up stops with a message instead; stop the other studio and start again, or set `STUDIO_DATA_DIR` to the old folder to keep using it. A `.env` copied from an older `.env.example` sets `STUDIO_DATA_DIR=var`; remove that line to move.

## Configure a model

The left sidebar's **Settings** holds global configuration; the controls under the message box choose the model and workspace access for each Run.

1. **Settings → Providers** lists the endpoints the studio can call: OpenAI-compatible (streaming Chat Completions with tool calls), the OpenAI Responses API, and Anthropic Messages. Add one with **Add OpenAI-compatible**, **Add OpenAI Responses** or **Add Anthropic**, set its base URL, paste the API key and press **Save key**. A local endpoint without a key works too.
2. **Settings → Models** lists each provider's models. **Refresh models** asks the provider for its model list (`GET /models`); a model ID can also be added by hand. Each provider also sets temperature, max output tokens and context window, and **Prices** takes each model's input, output and cache prices in US dollars per million tokens. The **Default model** applies to new conversations and to the CLI and API.
3. The **model button** under the message box picks the model for the next Run, sets its **reasoning effort** (Low/Medium/High for OpenAI models; up to Extra high and Max for Anthropic), refreshes all model lists, or opens **Manage models…**. A conversation reopens with the model and access its last Run used.

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

**Thinking.** When a model's reply includes readable reasoning, the turn shows it in a collapsed **Thinking** section above the answer. Current Claude models leave their thinking empty unless asked, and OpenAI returns reasoning summaries only on request, so turn on **Show the model's thinking** for the provider in **Settings → Models**. For Anthropic this asks for summarized thinking and needs a model with adaptive thinking (Claude 4.6 or later); for the Responses API it asks for reasoning summaries, which OpenAI may restrict to verified organizations. Either way an unsupported request is rejected by the provider with its reason. Chat Completions providers do not return reasoning through this adapter. The simulated model's `/think` command shows how it looks.

The **Responses API** provider talks to OpenAI's `/v1/responses` endpoint, which current OpenAI reasoning models are built for. Responses are not stored by OpenAI (`store: false`). With a reasoning effort chosen, the Run keeps the model's encrypted reasoning between its own tool calls, like Claude's thinking blocks; later turns receive the conversation without it.

If a request fails, the error states the provider's reason and HTTP status (with any credential removed), or what happened to the connection: it could not be opened, it closed before the response was complete, or the provider did not answer in time. A Run never retries a model request by itself; an interrupted Run waits for you to choose **Retry** (see [Troubleshooting](#troubleshooting)). Compaction requests are the exception, since they change nothing but cost (see [Context and compaction](#context-and-compaction)). A missing model is a configuration error rather than a silent switch to simulation.

For offline development, choose **Simulated model** in the model picker or set `STUDIO_PROVIDER=fake`. It answers with canned text but runs real tools: `/read README.md`, `/echo hello` (which requires approval), `/tool bash {"command":"ls"}` (Linux) or `/tool powershell {"command":"Get-Location"}` (Windows), and `/slow` to try **Stop**.

## Use the studio

### Run a task

Type a task in the message box and press **Enter** or **Run**; **Shift+Enter** starts a new line. If you write long multi-line tasks, **Settings → Composer** switches this browser to **Ctrl+Enter** (**Cmd+Enter** on macOS) for running, so Enter adds lines. The preset selector beside the box switches between the built-in presets:

| Preset | Focus |
| --- | --- |
| Security analysis (default) | Trace attacker-controlled input to sensitive operations; report evidence and impact. |
| General agent | Ordinary project work from inspection to a concrete result. |
| Coding | Implement within the existing architecture and verify with real commands. |
| Debugging | Reproduce first, change one cause at a time, re-run the reproduction. |
| Code review | Report actionable findings with locations; do not modify files unless asked. |

Presets change how the agent works, not what it is allowed to do. Mention `$skill-name` in a task to load a Skill (see below): typing `$` at the start of a word lists the workspace's Skills. Typing `/` at the start of the task lists custom commands. In either list the arrow keys choose, **Enter** or **Tab** inserts the name and **Esc** closes the list.

**Images** can go with a task: paste them into the message box, drop them on it, or use the image button beside the preset. PNG, JPEG, GIF and WebP images up to 5 MiB each are accepted, at most ten per task; the turn shows them with your message, and later turns of the conversation keep them in context. The model must accept images (most current Claude and GPT models do); a text-only model rejects the request with the provider's explanation. Images are stored once in the studio's data directory (`attachments/`) and stay there when a conversation is deleted.

**Custom commands** are prompts you reuse. Save a Markdown file as `.agents/commands/<name>.md` in the workspace (or in `commands/` in the studio's data directory, for every project) and type `/name arguments` as the task. Typing `/` lists the available commands with their descriptions. In the template, `$ARGUMENTS` stands for everything after the name and `$1` to `$9` for single words; arguments given to a template without placeholders are added at the end. Optional frontmatter sets what the list shows:

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

**Sub-agents.** The agent can hand a self-contained task to a sub-agent with the `delegate` tool: a separate Run with the same model, preset and workspace that starts without this conversation's history and returns its final answer as the tool result. Expand the `delegate` step to follow the sub-agent's own tool calls and answer while it works. A sub-agent cannot ask you anything: tools that would need your approval are unavailable to it, and it cannot delegate further. Stopping the Run stops its sub-agent too. Its cost counts toward the conversation in **Context**, and deleting the conversation deletes it. A duplicate or fork keeps the sub-agent's answer, but its `delegate` step still opens the original conversation's sub-agent (unavailable once that conversation is deleted), and the copy's **Context** does not count that cost. Settings saved before this tool existed show it as disabled in **Settings → Tools**.

**Stop run** cancels the Run and waits for its processes to be cleaned up. It does not undo files that were already written; use **Undo file changes** on the turn (see [File tools](#file-tools)).

With prices set, each Run's cost appears in **Inspector → Run trace**, **Context** shows what the conversation has spent so far, and evaluations report the average cost per case. A Run keeps the prices it started with; Runs without a price or reported usage are counted, not priced at zero. Compaction requests are not included.

**Notifications** in the sidebar asks the browser for permission and then notifies you when the open conversation's Run needs approval, finishes, fails or is interrupted while the tab is in the background. The setting is kept in this browser only.

### Inspect what happened

**Inspector → Run trace** lists every model request and tool call of a turn in execution order. For a model request it shows the canonical messages with their sources, the prepared request, the actual outbound body, usage and provider metadata. The view is redacted by default; **Show original trace** reveals the local original, and **Export JSON** downloads a labelled, redacted copy.

**Inspector → Prompt preview** compiles the current draft whenever it is open, without running anything: final message sequence, block activation, prepared request and the tool schemas the model would receive. It updates as you edit and never blocks **Run**; **Preview** beside **Run** is a shortcut that opens it. While a Run is active the preview waits for it to end.

**Changes** shows the workspace's uncommitted Git changes: branch and upstream (with commits ahead ↑ and behind ↓), each changed file with its status letters, and a file's diff against the last commit when you select it. **Review changes** switches to the Code review preset and puts a review task with the whole diff (up to about 90,000 characters) in the message box for you to check and send, because the agent itself cannot run Git. The studio runs Git on this computer read-only, with your Git configuration but with repository-provided programs (hooks, fsmonitor, external diff and textconv drivers) disabled. Private files such as `.env` are listed but their content is never shown or included.

### Edit prompts

**Prompt** opens the preset editor. Blocks have a role (system, user or assistant), a position (before the conversation history or just before your message), optional `{{variables}}` and activation rules. Workspace instructions, memory and Skills appear in their real position between the blocks. Edits are drafts with **Undo**, **Redo** and **Discard** until you **Save preset**; saving creates a new revision, and earlier Runs keep the version they used. **Export**/**Import** moves presets between installations.

### Evaluate prompts

**Evaluations** (sidebar) runs the same tasks under different setups and shows which one gets more of them right. The dialog has three steps:

1. **Tasks:** what the agent is asked, and how its final reply is checked: the reply contains, does not contain, is exactly, or matches a pattern (regular expression), with an **Ignore case** option; or it only has to exist (the run finishes). Checks are plain text rules, not a model judge, so the same reply always gets the same verdict.
2. **Setups to compare:** each setup is a prompt preset, optionally with its own model and workspace access. A new evaluation starts with two setups on different presets. A setup is named after what it runs (for example *Coding · gpt-5 · read only*) until you rename it, and two identical setups are pointed out, since their results could differ only by chance.
3. **Results:** a ranking of the setups by how many tasks they passed (with average tokens, time and cost per task) and a grid of every task under every setup. Select a result for the reason it passed or failed, the final reply and a link to its recorded trace.

The footer says how many runs the evaluation makes (tasks × setups); **Run N runs** saves the evaluation and starts them one after another in the selected workspace, switching to Results to follow them. Each is an ordinary Run with its own snapshot and trace, kept in a hidden conversation so it does not appear among yours. Earlier comparisons stay available from the selector in Results.

Comparison runs are unattended. A Run that stops for approval is cancelled and counts as failed, so setups offer only **Read only** or **Workspace write** access. Runs use the selected models and may cost money; **Stop** cancels the remaining runs and keeps the finished ones. Deleting an evaluation also deletes its runs. Task input variables can be set through the API (`variables` on a case) but not yet in the dialog.

### Manage conversations

The sidebar's **Conversations** section lists the selected workspace's conversations, with **New conversation** to start another there, search, an **Archived** filter and flat folders. A conversation's **…** menu can rename, move to a folder, change its workspace, duplicate, fork from an earlier turn, archive or delete it. **Fork here** on a turn does the same from that point. Copies share no state with the original and never repeat model or tool calls.

**Processes** lists background commands the agent started, with their output, and lets you send input lines, close stdin or stop them.

### Context and compaction

**Context** shows how the conversation uses the model, from the provider's own accounting, top to bottom:

- **This conversation:** every model request so far, sub-agents' included: how many, the cache hit rate (the share of all prompt tokens the provider served from its cache), prompt and output tokens, and dollars spent.
- **Context window now:** how full the window is (the latest request's prompt against the model's window), the peak so far, and a column per request split into the part served from cache and the part sent fresh. Point at a column for its numbers, or open the table under the chart.
- **History sent to the next run:** the conversation's messages and how many the next Run sends (fewer while a summary is in use), as a byte estimate.
- **Compaction:** choose how many recent turns to keep and press **Compact older turns** to have the model selected below the message box summarize the rest. While it runs, **Stop compaction** cancels it; while a summary is in use, **Restore full context** switches back.
- **Compaction history:** each summary with its status, the summary text and a link to its recorded requests.

A provider that reports no usage, including the simulated model, says so rather than showing zeros.

Compaction never deletes anything: every turn stays in the conversation and in its history, and new Runs send the summary plus the recent turns instead. Each compaction, manual or automatic, also appears in the conversation as a marker after the last turn it summarizes: while it runs, once it is in use (with its summary), and quieter once it has been replaced, restored or has failed. A summary request whose connection fails or breaks is sent again, up to three times in all, and the attempts are kept in its record.

To compact without asking, turn on **Settings › Context › Compact automatically**: after a Run, if the history the next Run would send is over the size you set, older turns are summarized with that Run's model, keeping the recent turns you chose. Automatic summaries cost model calls, are marked *Automatic*, and are restored the same way.

Side panels such as Context sit beside the conversation in windows at least 961 pixels wide; in narrower windows they cover it until closed.

## Projects, memory and Skills

A **project** is a registered folder. **Projects → + (Add project) → Choose folder** opens the folder picker of the computer running the service: Windows Explorer on Windows and under WSL, zenity or kdialog on a Linux desktop. **Enter a path** types a path instead (also the fallback when no picker is available); tick **Create the folder if it does not exist** to start a project in a new folder. A path that does not exist is otherwise reported rather than created, so a typo does not become an empty project.

Under WSL, a folder chosen in Explorer is translated to its Linux path: `\\wsl.localhost\<distro>\home\you\app` becomes `/home/you/app`, and `C:\work\app` becomes `/mnt/c/work/app`. Prefer folders inside the distribution; Windows drives are much slower to read from WSL. Folders in another distribution or on a network share cannot be used.

Each conversation belongs to one project, or to the default workspace (`STUDIO_WORKSPACE_ROOT`, the current directory by default). Selecting a project starts a new conversation there; an idle conversation can be moved with **Change workspace**. Each Run records the folder it used.

At the start of each Run, the agent receives:

- **Instructions:** the first non-empty `AGENTS.override.md` or `AGENTS.md` at the project root. The `read_instructions` tool loads the chain for deeper directories, where deeper files take precedence.
- **Memory:** notes kept per workspace, editable in **Workspace → Memory** and through the agent's `read_memory`/`save_memory` tools. It is plain Markdown; there is no hidden summarization. Memory is studio data, stored in the data folder (`memory/<id>/MEMORY.md`, with `workspace.txt` naming the folder it belongs to), not in the project; a `.rtagent/MEMORY.md` left by an earlier version moves there the first time the workspace is used. Commands cannot read it, and **Undo file changes** does not cover it.
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

**Tools** lists every tool with its argument schema and its status for the workspace access currently selected: Allowed, Approval required, Disabled, or Blocked by read-only. **Settings → Tools** sets each tool's default permission, and **Command rules** give shell commands that start with a prefix their own permission (allow, ask or deny; the longest prefix wins). Allow and ask rules apply only to a single command without `;`, `|`, `&&`, redirects or `$( )`, so a rule for `npm test` never covers `npm test; rm -rf data`; deny rules match any command that starts with the prefix. Under **Ask before changes**, allow rules ask instead. Rules and standing approvals only decide whether to ask; commands still run in the sandbox. New MCP tools start disabled; starting background processes, sending them input, downloading files, searching the web and reading web pages start at "ask".

The native tools are: `list_files`, `search_files`, `search_text`, `read_text`, `read_instructions`, `write_text`, `edit_text`, `apply_patch`, `update_plan`, `bash` (Linux) or `powershell` (Windows), `start_process`, `poll_process`, `send_process_input`, `stop_process`, `fetch_url`, `web_search`, `read_url`, `read_memory`, `save_memory`, `load_skill`, `save_skill`, `delegate` and `echo`.

**Workspace access** under the message box tightens these per Run (see [Run a task](#run-a-task)). **Read only** mounts the workspace read-only for commands and removes every file-changing tool from what the model is offered.

### File tools

File tools take paths relative to the workspace. An absolute path is accepted only when it points inside the workspace, by its real location or as `/workspace/…` (where Linux commands see it); anything else, `..` and links are refused, with the workspace root in the message. File tools never touch credentials (`.env`, `.env.*` except `.env.example`, `access-token`), Git metadata, databases (`.db`, `.sqlite`, `.sqlite3`), the studio's data directory, or dependency folders (`.venv`, `node_modules`, `__pycache__`). Replacing a file requires the hash from the last read, so a concurrent change is detected instead of overwritten; the error tells the model whether the file already existed, changed since it was read, or disappeared, and how to continue. `apply_patch` validates every file before changing any; each file is replaced atomically, but a multi-file patch is not a transaction, and a failure reports which files were already changed.

A turn whose file tools changed files lists them under **Changed N files**. **Undo file changes** puts each file back as it was before that run, including removing files it created and restoring files it deleted, but only while the file is still as the run left it: a file changed since, by a later run, a command or you, is left alone and named. Changes made by shell commands and background processes are not tracked and cannot be undone this way; use Git for those. The earlier versions are kept in the studio's data directory (`file-history/`), also after the conversation is deleted.

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

### Web, downloads and MCP

`web_search` asks the search service chosen in **Settings → Tools → Web search**: the Brave Search API or Tavily (with an API key saved there, or set as an environment variable, like provider keys) or a SearXNG instance you run (its URL). It returns titles, URLs and snippets, and is offered to the model only once a service is set. `read_url` reads a public HTTPS page as text: HTML is converted to plain text and cut at 48,000 characters, up to 3 redirects are followed, each to a public address, and nothing is saved. Both run in the studio, not in the sandbox, and send what the model wrote (a query or a URL) off this computer, which is why they ask first by default. Settings saved before these tools existed show them as disabled; set their permission in **Settings → Tools** to use them.


`fetch_url` downloads a public HTTPS file (up to 16 MiB, optionally checked against a SHA-256) from hosts listed in **Settings → Sandbox → Allowed HTTPS download hosts**, and asks before each download unless you change its permission. New installations list GitHub, npm, PyPI, Hugging Face, Google downloads (`dl.google.com`, `storage.googleapis.com`), crates.io, Go modules and Maven Central, with the hosts they redirect downloads to; **Add common hosts** adds the same list to existing settings. Redirects are followed only to listed hosts (at most five), each checked like the first request. It refuses proxies, credentials in URLs and private or local addresses, and writes only new files. It does not give commands network access.

MCP servers (stdio commands or Streamable HTTP URLs) are configured in **Settings → MCP servers** and run **outside** the sandbox, with their own permissions. **Save & discover** lists each server's tools on the same page, where you set their permissions; after a restart they are discovered again automatically the first time they are needed.

### Hooks

**Settings → Hooks** runs your own commands at points in a run, for example to log tool calls, run a formatter after edits, or send a desktop notification when a run ends. Each hook gets the event as JSON on standard input (`event`, `run_id`, `session_id`, `workspace_root`, plus `tool` with name and arguments, the tool's `status` and `result`, or the run's `status`, `stop_reason`, `error` and `input`) and runs in the workspace folder.

| When | Effect of the hook's result |
| --- | --- |
| Before a tool runs (optionally only for tools matching a pattern such as `bash` or `mcp_*`) | Exit code 0 lets the call run. Exit code 2 blocks it and gives the model the hook's standard error as the reason. Any other exit code, or a timeout, also blocks it. |
| After a tool runs | Recorded with the call; never changes the run. |
| When a run ends | Recorded as an event on the run; never changes it. |

Hooks run **outside** the sandbox, on this computer with your permissions, using the system shell and only basic environment variables (`PATH`, `HOME`, the display session), so keys saved in the studio never reach them. Tool arguments come from the model: treat them as untrusted data and never paste them into a command line. Do not put keys in a hook command; commands are saved with the settings and in each run. A run keeps the hooks saved when it started. Processes a hook leaves holding its output are stopped a second after it exits.

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
| "Context compaction is running…" | A summary (possibly an automatic one) is in progress for this conversation. Wait for it, or stop it in **Context**. |
| `web_search`, `read_url` or `delegate` is disabled, or never offered to the model | Settings saved before these tools existed have no permission for them; set one in **Settings → Tools**. `web_search` is also left out until a service is chosen under **Web search**. |
| A request fails after turning on **Show the model's thinking** | The model or account does not support it (Claude models before 4.6; OpenAI organizations not verified for reasoning summaries). Turn the option off for that provider. |
| A task with an image fails | The model does not accept images; the error carries the provider's reason. Choose a model with image input or remove the image. |
| **Undo file changes** skips a file | The file changed after the Run (a later Run, a command or you), so it is left alone. Use Git for those changes. |
| "Model request failed: the connection to the provider closed before the response was complete" | The network or the provider dropped the connection mid-response; the other variants say the connection could not be opened or the provider did not answer in time. Run the task again (an interrupted Run offers **Retry**). Compaction already retried twice. |
| A side panel covers the conversation | The window is narrower than 960 pixels; close the panel or widen the window. Wider windows keep the conversation beside the panel. |
| "Studio data in … could not be moved" at start-up | The old `var/` data is still open, usually by another studio started from the same folder. Stop it and start again, or set `STUDIO_DATA_DIR` to the old folder. |
| "… is outside this workspace (…)" from a file tool | The agent asked for a file in another folder. Add that folder as a project (**Projects → +**) and work there, or copy what it needs into this workspace. |
| "The download redirected to …, which is not an allowed download host" | The download host sends files from another host. Add that host under **Settings → Sandbox → Allowed HTTPS download hosts** if you trust it. |
| A Run is "interrupted" | The service restarted, or a call's outcome is unknown. Review each uncertain call and choose **Retry** or record a verified result before resuming. |

## Status and roadmap

As of 2026-10-08:

- Stages 1–4 below are implemented, stage 6 in part (prompt evaluations), and the agent features row. Real-model acceptance ran on Windows ([records](history/README.md)); the Linux sandbox is covered by automated tests against real bubblewrap, but a live-model Linux acceptance and checks against Anthropic's and OpenAI's hosted APIs have not been run. The Messages and Responses adapters, thinking replay and display, reasoning effort, prompt caching and images are tested against mocked HTTP transports and local protocol fixtures.
- Not implemented: full terminal emulation (PTY/ConPTY), command network access, disk quotas, stateful MCP sessions, specialized reverse-engineering integrations, undo of changes made by commands, approvals inside sub-agents, live streaming of model thinking.
- Not planned: signing in with a ChatGPT or Claude subscription, IDE extensions, a terminal UI.

| Stage | Scope | Status |
| --- | --- | --- |
| 1 — Inspectable execution | Web workspace, prompt blocks, provider boundary, agent loop, MCP, traces, approvals, recovery | Done |
| 2 — Working local agent | Direct Run, projects, native file tools and commands with OS isolation, instructions, memory, Skills | Done |
| 3 — Daily agent use | Context compaction, conversation branching, diffs, streamed command output, background processes, dependency/download policy | Done |
| 4 — Extensible harness | CLI, JSON-RPC, Python client and embedding, hooks, trusted local tools, preset/Skill packages, Messages provider | Done |
| 5 — Security analysis workflows | Stateful MCP, IDA/Ghidra/debugger/browser integrations, evidence artifacts, analysis Skills | Planned |
| 6 — Evaluation | Prompt comparisons over task sets with deterministic checks and token, time and cost reporting (done); replay of recorded Runs, model-judged grading (planned) | Partial |
| Agent features | Prompt caching, cost in dollars, conversation approvals and command rules, automatic compaction, undo of file-tool changes, custom slash commands, shell hooks, Git changes panel, web search and page reading, images, Responses API, thinking display, sub-agents | Done |

Later stages should add capabilities with a real workload and acceptance criteria each, while keeping the single-user local path simple.
