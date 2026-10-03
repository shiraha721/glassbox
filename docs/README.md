# Security Agent Studio

A local agent workspace for coding, reverse engineering, CTF challenges, debugging, and other authorized security work. Compose inspectable prompts, run a tool-using agent, and retain evidence for each model and tool call.

The target is an extensible everyday agent harness in the scope of [pi](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent), with prompt composition and inspection as core features. The original Web UI and tool loop are **Stage 1**, not the finished product. Develop working vertical features without speculative services or a generic plugin framework.

## Current status

As of 2026-10-04, Stage 1 and the Windows Stage 2 local-agent workflow are implemented and accepted:

- Direct **Run**, streaming replies, persistent conversations, cancellation, approval, and checkpoint recovery. Preview is optional.
- Prompt blocks with roles, ordering, conditions, variables, and source mapping; live draft inspection, including empty task input; immutable Run inputs.
- Continuous chat history, conversation rename/duplicate/fork/archive/delete, flat folders, and reversible preset drafts.
- OpenAI-compatible Chat Completions with configurable Base URL, model, and server-side API key. The real adapter is the default; a missing model produces a configuration error.
- Native Windows PowerShell execution in AppContainer, or Linux Bash in bubblewrap, plus list/read/write/edit file tools.
- Named Projects bind conversations to local workspace folders, with project-specific instructions, file tools, native commands, memory, Skills, and a bundled skill-creator.
- Stateless stdio/Streamable HTTP MCP, per-tool permissions, captured requests, tool results, usage, and redacted exports.

Real-model acceptance completed multi-file coding, actual sandbox checks, Skill creation, evidence-based security analysis, and memory/Skill reuse after restart in a registered project. The final acceptance captured 35 model requests and 70 tool calls. Separate real HTTP checks verify native isolation, read-only mode, cancellation, project boundaries, and conversation management. See [Stage 2 acceptance](acceptance-stage2.md) for scenarios, evidence, commands, and limits.

See [Architecture](architecture.md) for the whole-system design and the [Architecture Reference](reference/README.md) for every module, class, function, callback, and dependency role. The [Development Guide](development.md) defines repository conventions. [AGENTS.md](../AGENTS.md) is the root entry point for coding agents.

## Start the application

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 22.12+ and npm. From the repository root:

```powershell
uv sync --dev --locked
npm --prefix web ci
npm --prefix web run build
Copy-Item .env.example .env   # Only if .env does not already exist.
```

Configure the endpoint and model in .env:

```dotenv
STUDIO_DATA_DIR=var
STUDIO_WORKSPACE_ROOT=.
STUDIO_PROVIDER=openai
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=
OPENAI_API_KEY=
```

Start with `uv run python -m rtagent.main` and open **http://127.0.0.1:8001**. Enter a task and click **Run**, or press **Ctrl+Enter / Cmd+Enter**. Open **Prompt** to edit the preset and **Inspector** to examine requests. **Preview** inspects the current draft without calling a model. It updates after a 300 ms pause when input, variables, preset blocks, or selected Skills change. Empty input can inspect system instructions. The last valid result stays visible during an update/error; Run captures current input and is never gated by Preview. Inspection does not save preset edits or create history. The `rtagent` console command also works; launching the module avoids holding that executable open during Windows dependency updates.

The app opens directly without login or a workspace access token. Startup applies migrations. Use one server worker on loopback. Changing the workspace root or .env provider options requires a restart. Explicit .env provider values override saved settings on startup; edits in **Settings** apply immediately to new Runs until the next restart. API keys stay on the server.

For a keyless local endpoint, leave the credential reference in Settings empty. The adapter requires streaming Chat Completions and tool calls. It does not silently rewrite roles, retry requests, or fall back to simulation.

## Commands and workspace tools

**Windows uses native AppContainer isolation and Windows PowerShell**, included in Windows 10/11. It does not require WSL, a Linux distribution, a VM, an administrator setup, or a separate local account. The model receives a `powershell` tool with PowerShell syntax; Bash scripts are not silently translated. Git Bash's MSYS runtime is not supported inside this AppContainer.

**Linux uses Bash and bubblewrap.** On Debian/Ubuntu, install the dependencies:

```bash
sudo apt-get update
sudo apt-get install bash bubblewrap util-linux
```

**Workspace** checks the actual backend by executing a command in the sandbox. An unavailable sandbox reports an error; commands never fall back to unrestricted host execution.

- On Windows, commands run in a unique AppContainer with no network capabilities. A private workspace copy has read/write or read-only ACLs. PowerShell starts at `Workspace:\`; `$env:RTAGENT_WORKSPACE` gives the native directory. A Job Object limits the process tree to 32 processes and terminates descendants on completion, timeout, Stop, or server exit. Original project and system ACLs are not modified.
- On Linux, private mount/process/network namespaces expose the project at /workspace, read-only system binaries/libraries, and scratch storage. The host home and network are not mounted into that environment.
- Server credential variables and the host PATH are not forwarded. Existing .env files, Git metadata, runtime databases, the configured data directory, and dependency/cache directories are excluded. .env.example remains readable. AppContainer retains access to Windows resources normally readable by sandboxed applications; it is not a VM or a claim that every public host file is hidden.
- Network access is disabled, including local host services and downloads. Windows uses AppContainer enforcement; Linux uses a private network namespace.
- **Settings → Workspace sandbox** selects workspace-write or read-only. Read-only also removes native file/memory/skill mutation tools from model exposure. Scratch storage remains writable.
- Command deadlines, bounded stdout/stderr, and process-tree cleanup apply. Timeout/cancellation can leave partial project edits; inspect those files before repeating a write.

On Windows, changed files are copied back after all command processes stop, with checks against concurrent host edits. Edits appear after the call completes. The copy is limited to 64 MiB and 10,000 entries in each direction, excluding dependencies and runtime data. Links, aliases, unsupported files, and conflicting edits fail explicitly. Copy-back is not an atomic multi-file transaction. Linux uses a direct workspace mount.

Windows commands can use built-in PowerShell and permitted executables; arbitrary host development environments are not imported. Network allowlists, interactive terminals, persistent background jobs, and disk quotas remain future work. The boundary assumes the workspace is not concurrently modified by a hostile host process. See [Microsoft's AppContainer documentation](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer) for the Windows security model.

Native file tools use relative paths, reject traversal/links, and bound UTF-8 files to 64 KiB. read_text returns content and a hash; write_text requires that hash before replacing a file. edit_text requires exactly one literal match. File tools use application path checks; command tools use OS isolation.

MCP servers run **outside the command sandbox**, under their configured transports and permissions. Add a stdio executable and arguments or Streamable HTTP URL in Settings, select **Save & discover**, then enable individual tools. New MCP tools default to Disabled. Connections are stateless; IDA/Ghidra workflows need separate integration acceptance.

## Projects and workspace selection

Use **Projects → Add project** in the sidebar, enter a name and an existing absolute folder path, then select it. Each Project has one workspace root. New conversations use that folder for file tools, sandbox commands, AGENTS.md, .rtagent/MEMORY.md, and .agents/skills. Tool permissions, model settings, and limits remain shared in Settings; workspace-write permits native writes, and read-only removes model mutation tools.

**Default workspace** uses STUDIO_WORKSPACE_ROOT for existing conversations without a Project. The conversation menu's **Change workspace** moves an idle conversation to a saved Project or back to the default root. Future Runs use the new root; historical Runs and copied evidence keep their original roots. Finish/cancel active work before moving it. Duplicate/fork preserves the selected Project; workspace files are shared, not copied.

The workspace path appears in the conversation header, Workspace, and each saved Run's Inspector. Ordinary history folders organize conversations separately from Project filesystem roots. Registering a Project does not copy files, create directories, or expose application runtime data as a workspace. Project root paths are fixed registrations; richer project management is later work.

## Memory and Skills

**Workspace → Memory** edits .rtagent/MEMORY.md. The agent uses read_memory/save_memory for the same file. There is no vector store or hidden automatic summarization.

Prompt composer shows the active project’s AGENTS.md, memory, catalog, and selected Skill sources in their actual insertion position. At submission, root AGENTS.md, memory, and skill metadata are captured with explicit sources in the Run snapshot. They are not repeatedly appended to conversation history. Later edits affect new Runs. Memory updates reject stale hashes. Local memory is ignored by this repository's Git configuration; never put credentials in it.

Workspace skills live at `.agents/skills/<name>/SKILL.md`:

```markdown
---
name: evidence-review
description: Review workspace evidence and prepare a report with file references.
---

Read the evidence, distinguish facts from assumptions, and cite file paths.
```

Only name, description, and location enter the default catalog. The model calls load_skill when a description matches the task. To load a skill explicitly, select it in **Prompt → Workspace context → Skill catalog** or **Workspace → Skills** or mention **$evidence-review** in your task. Supporting files can live beside SKILL.md and be read with file tools. Invalid entries are shown in Workspace.

The bundled **$skill-creator** guides the agent to inspect existing procedures, write one with save_skill, and load it again to validate the result. For example: “$skill-creator create a skill for reviewing this project's Python changes.” You can also create/edit skills directly in Workspace. Bundled skills are read-only; project skills can be committed after review. Skill text cannot grant permissions or alter the sandbox.

## Conversations and prompt editing

The sidebar separates **Active** and **Archived** conversations, supports title search, and groups history in flat folders. Use a conversation's **…** menu to rename it, move it, duplicate it, choose a fork point, archive/restore it, or delete it. Folder menus rename or delete a folder; deleting a folder moves its conversations to **Unfiled** and preserves their records.

**Duplicate** copies the full saved conversation and its completed runs/traces. **Fork** copies history through a selected successful turn; **Fork here** performs the same operation from a turn header. Copies have independent IDs and a **Copied history** label. They never repeat model/tool calls, clone checkpoints, or copy workspace files. Copies keep their source conversation’s Project for future work; saved Run roots remain immutable. Only successful turns become input history for subsequent runs. Finish or cancel an active Run before copying, archiving, or deleting its conversation.

Chat displays all turns together. The conversation toolbar and message input stay fixed while history scrolls. Archived conversations can be inspected; restore one to send another message.

In **Prompt**, the **Active preset** selector stays usable with unsaved changes. **New**, **Duplicate**, and **Rename** have separate controls. Each preset keeps its draft while switching; **Undo**, **Redo**, and **Discard changes** make edits reversible. Blocks are grouped into **Before conversation** and **Before your message** positions. Saving advances the preset revision; draft edits are local to the current page and are lost on reload. The default preset contains six editable system blocks covering identity, boundaries, practical work, native tools, memory/Skills, and reporting. Recognized unchanged old starter text upgrades automatically; user edits and existing Run snapshots remain unchanged.

## Execution records

STUDIO_DATA_DIR contains app.db (settings, presets, sessions, snapshots, events, receipts) and checkpoints.db (LangGraph state). Runtime files, .env, raw traces, and local memory are excluded from Git.

Each model call records canonical messages, prepared and captured application-level requests, transformations, metadata, and returned usage. Missing usage/cost data stays null. Token estimates use a byte heuristic; configured context overflow fails explicitly without hidden trimming.

Approvals survive restarts. Unknown outcomes require a recovery decision rather than automatic retry. Stop awaits local cleanup but does not undo completed edits or remote effects. Only successful turns enter session history. Refresh/SSE reconnect reload saved state without creating another Run. Deleting an idle conversation removes its associated records and checkpoints. Original traces remain local; exports are labeled redacted copies.

## Development and live verification

Use `npm --prefix web run dev` alongside the backend for Vite at http://127.0.0.1:5173. Regenerate API types after contract changes:

```powershell
uv run python scripts/export_api.py
npm --prefix web run generate:api
```

Validate changed behavior against the running service first. A repeatable check with a real HTTP server and OS tools is available:

```powershell
uv run python scripts/smoke_workspace.py
uv run python scripts/smoke_conversations.py
uv run python scripts/smoke_projects.py
```

It temporarily binds port 8766 and uses a disposable workspace with an explicitly simulated model. It exercises Windows AppContainer/PowerShell or Linux bubblewrap/Bash. It verifies direct Run retry identity, actual command/file operations, hidden host paths, network isolation, memory/Skills, context provenance, read-only enforcement, and cancellation cleanup. It does not call a model service or modify the selected user workspace.

The conversation check uses port 8767 with separate temporary data. It verifies fork boundaries, duplicate traces and provenance, stale-edit rejection, archive/restore, folder rename/delete, and continuing a fork after deleting its source.

The project check uses port 8769 and sibling temporary folders to verify project-bound files, commands, instructions, memory, Skills, idle moves, frozen Run roots, and busy-session rejection.

For explicit real-model acceptance on Windows with the main service running:

```powershell
uv run python scripts/acceptance_stage2.py --live
```

It uses the configured real provider and may incur model usage. It owns port 8768, a registered project, and separate data; local results stay under ignored var. No real service is called by the default offline tests.

For manual offline checks, explicitly select **Simulated model** in Settings or set STUDIO_PROVIDER=fake. Commands such as `/read README.md`, `/echo hello`, and `/tool powershell {"command":"Get-Location"}` (Windows) or `/tool bash {"command":"pwd"}` (Linux) use simulated decisions with real tools. `/slow` exercises Stop. Simulation is a development aid, not the default provider.

```powershell
uv run pytest
uv run ruff check src tests scripts migrations
uv run black --check src tests scripts migrations
uv run isort --check-only src tests scripts migrations
npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web run test -- --run
npm --prefix web run format:check
npm --prefix web run build
```

Browser checks use a separate backend and temporary data. Build first, then run `npx playwright install chromium` and `npm run test:e2e` from web/. Offline checks do not replace acceptance against a real model.

## Delivery stages

The previous foundation/compiler/conversation/tools/recovery milestones are all subdivisions of **Stage 1**. These stages describe delivery boundaries, not instructions to build speculative infrastructure now.

| Stage | Scope | Exit criteria / status |
| --- | --- | --- |
| **1 — Inspectable execution foundation** | Web workspace, prompt blocks, provider boundary, single-agent loop, MCP, traces, persistence, approvals, recovery | Implemented locally; a basic live-model/native-tool loop has passed on Windows. |
| **2 — Working local agent** | Direct Run, live composition, Projects/workspace binding, native commands/read/write/edit, OS isolation, instructions, memory, Skills and skill creation | Complete on Windows; real-model coding/analysis, restart reuse, and native/project boundary checks passed. See [acceptance](acceptance-stage2.md). Linux requires acceptance on its own host. |
| **3 — Daily agent use** | Explicit context compaction, context accounting, session branching, diff review, command output streaming, interactive/background process lifecycle, practical dependency/network policies | Conversation organization and branching are implemented. Remaining scope must complete substantial multi-file tasks across stop/restart/resume with reliable context and no stray processes. |
| **4 — Extensible harness** | Stable programmatic run interface, CLI/JSON/RPC, focused lifecycle/tool hooks, reusable prompt/skill packages, more providers | Use the same loop through Web, CLI, and embedding; add integrations without patching the core. Derive hooks from real use cases. |
| **5 — Security analysis workflows** | Stateful MCP, accepted IDA/Ghidra/debugger/browser integrations, evidence/artifacts, reusable analysis skills | Complete representative authorized reverse-engineering, debugging, and CTF tasks with real tools and inspectable evidence. |
| **6 — Evaluation and justified scale** | Reproducible task sets, replay/comparison, A/B and ablation, usage/cost reporting; bounded delegation, more workers, PostgreSQL or teams only when needed | Give each capability a real workload and its own acceptance criteria. Preserve the simple local path. |
