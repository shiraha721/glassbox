# Security Agent Studio

A local agent workspace for coding, reverse engineering, CTF challenges, debugging, and other authorized security work. Compose inspectable prompts, run a tool-using agent, and retain evidence for each model and tool call.

The target is an extensible everyday agent harness in the scope of [pi](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent), with prompt composition and inspection as core features. The original Web UI and tool loop are **Stage 1**, not the finished product. Develop working vertical features without speculative services or a generic plugin framework.

## Current status

As of 2026-10-05, Stages 1–4 are implemented; the Windows daily-agent and extensible-harness workflows are accepted:

- Direct **Run**, streaming replies, persistent conversations, cancellation, approval, and checkpoint recovery. Preview is optional.
- Prompt blocks with roles, ordering, conditions, variables, and source mapping; live draft inspection, including empty task input; immutable Run inputs.
- Continuous chat history, conversation rename/duplicate/fork/archive/delete, flat folders, and reversible preset drafts.
- OpenAI-compatible Chat Completions with configurable Base URL, model, and server-side API key. The real adapter is the default; a missing model produces a configuration error.
- Twenty native tools: file/search/read/instructions, guarded writes/patches, plans, native commands, memory/Skills, background process/poll/input/stop, controlled downloads and approval-check echo.
- Native Windows PowerShell execution in AppContainer, or Linux Bash in bubblewrap. Chat shows patch diffs and recorded task progress; Tools shows grouped schemas and effective permissions.
- Named Projects bind conversations to local workspace folders, with project-specific instructions, files, commands, memory, and Skills. Registration opens the native system folder dialog; manual entry appears only when it is unavailable. Bundled procedures include skill-creator, debugging, and code-review.
- Five editable presets: Security analysis, General agent, Coding, Debugging, and Code review. Recorded environment context tells the model its actual root, command language, tool exposure, and limits.
- Stateless stdio/Streamable HTTP MCP, per-tool permissions, captured requests, tool results, usage, and redacted exports.
- Explicit context compaction/accounting/restoration with original history preserved; native command output streams before completion and background commands accept bounded UTF-8 pipe input/EOF.
- Explicit trusted runtime folders and exact public HTTPS download hosts; commands keep their no-network boundary and dependencies run offline.
- Shared Python Harness and existing-service AgentClient, CLI/JSON-RPC, committed event observation/additional tool guards, trusted local tools, new-identity prompt/Skill data packages and an explicit Messages provider adapter.

Real-model acceptance completed multi-file coding, actual sandbox checks, Skill creation, evidence-based security analysis, and memory/Skill reuse after restart in a registered project. The final acceptance captured 35 model requests and 70 tool calls. Separate real HTTP checks verify native isolation, read-only mode, cancellation, project boundaries, and conversation management. See [Stage 2 acceptance](acceptance-stage2.md) for scenarios, evidence, commands, and limits.

The subsequent [Codex capability review](codex-gap-review.md) closes concrete tool/prompt gaps. Its separate real-model acceptance captured 14 requests and 26 tool calls for hash-guarded multi-file repair and a read-only vulnerability review. Stage 3/4 acceptance added **41 real model requests and 63 tool calls** for multi-file CSV repair, background worker input/cleanup, three-call context reduction, continued CLI implementation, restarted approval and a real embedded extension. See [Stage 3 acceptance](acceptance-stage3.md), [Stage 4 acceptance](acceptance-stage4.md) and [Harness interfaces](harness.md). External Claude and Linux-native acceptance remain separate unperformed checks.

The completed [code and architecture review](review.md) records the domain/application/infrastructure/interfaces package refactor, app/features/shared/api frontend structure, corrected dependency direction, native selection/lifecycle fixes and the terminal-polling file-context correction. Its closure evidence is separate from earlier real-model workloads.

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

Open **Tools** to search the catalog, inspect argument schemas, and see Allowed, Approval required, Disabled, or Blocked by read-only. **Edit permissions** opens the same versioned Settings editor. Selecting a prompt preset changes instructions; permissions remain controlled by Settings.

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
- Command network access is disabled, including local host services. Windows uses AppContainer enforcement; Linux uses a private network namespace. The separate allowlisted fetch_url tool downloads a bounded public HTTPS file without granting commands network access.
- **Settings → Workspace sandbox** selects workspace-write or read-only. Read-only also removes native file/memory/skill mutation tools from model exposure. Scratch storage remains writable.
- Command deadlines, bounded stdout/stderr, and process-tree cleanup apply. Timeout/cancellation can leave partial project edits; inspect those files before repeating a write.

On Windows, changed files are copied back after all command processes stop, with checks against concurrent host edits. Edits appear after the call completes. The copy is limited to 64 MiB and 10,000 entries in each direction, excluding dependencies and runtime data. Links, aliases, unsupported files, and conflicting edits fail explicitly. Copy-back is not an atomic multi-file transaction. Linux uses a direct workspace mount.

Windows commands can use built-in PowerShell and explicitly registered trusted runtimes; arbitrary host development environments are not imported. Settings accepts up to three specific runtime folders, staged read-only without modifying original ACLs (20,000 entries/256 MiB total). Python finds offline workspace packages at .rtagent/packages. **Public download hosts** accepts up to twenty exact HTTPS hostnames. fetch_url rejects private/non-global DNS answers, credentials/query/fragment, redirects/proxies and existing destinations; downloads cap at 16 MiB and can require SHA-256. General shell networking, full PTY/ConPTY terminals and disk quotas remain outside these stages. The boundary assumes the workspace is not concurrently modified by a hostile host process. See [Microsoft's AppContainer documentation](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer).

For a Python runtime, `uv run python -c "import sys; from pathlib import Path; print(Path(sys.base_prefix).resolve())"` prints its real folder. Enter that folder in Settings, rather than a drive/home/workspace ancestor or a linked alias. Installing dependencies remains an explicit task: download a known artifact with an expected digest, inspect it, then use an offline install/extraction in the sandbox.

**Processes** shows owned background commands with bounded output, PID/status, **Send line**, **Close stdin** and **Stop process**. The model uses start_process/poll_process/send_process_input/stop_process under ordinary permissions. At most four processes run; each has an absolute deadline capped by the original Run budget. Input is a UTF-8 pipe (8 KiB/frame), not terminal emulation. Successful Runs may leave a command active for later turns. Stop, timeout, Run failure/cancellation and service exit clean up its process tree. Restart retains interrupted evidence and never relaunches it. Windows files copy back after exit. Archive/delete/workspace moves require stopping active writers; conversation copies never clone processes.

Native file tools use relative paths and reject traversal/links. `read_text` reads a line range from UTF-8 files up to 2 MiB and returns the full-file hash, range/total/next-line metadata, and explicit truncation. Request another range instead of assuming a truncated result is complete. `search_files` matches relative globs; `search_text` searches literal text with line numbers, optional case sensitivity, and bounded scan/results. Neither launches an unrestricted host search process.

`write_text` directly writes up to 64 KiB and requires the last-read hash for replacement. `edit_text` requires exactly one literal match. `apply_patch` supports Add/Update/Delete with exact line contexts and hashes for existing files. It preflights all paths/contexts/hashes before changing up to twenty files, returns bounded diffs, and preserves unrelated line endings. Each replacement is atomic; the whole patch is not. A later failure reports completed paths so the agent can inspect effects before retrying. File tools use application path checks; command tools use OS isolation.

`update_plan` records a bounded plan with pending/in_progress/completed items and at most one active step. Chat derives the latest plan from paired tool receipts; it survives refresh, restart and conversation copies. Plan completion is the agent's progress report, while tool results establish whether verification actually passed.

MCP servers run **outside the command sandbox**, under their configured transports and permissions. Add a stdio executable and arguments or Streamable HTTP URL in Settings, select **Save & discover**, then enable individual tools. New MCP tools default to Disabled. Connections are stateless; IDA/Ghidra workflows need separate integration acceptance.

## Projects and workspace selection

Use **Projects → Add project → Choose folder** in the sidebar. Windows opens Explorer's system folder dialog; a Linux desktop uses installed zenity or kdialog. The selected path suggests a project name that you can edit before adding it. Cancellation preserves the existing field. If desktop selection is unavailable, enter an existing absolute path manually. Each Project has one workspace root. Selecting a different Project opens a new draft; choose an existing conversation explicitly to continue its history. New conversations use that folder for file tools, sandbox commands, AGENTS.md, .rtagent/MEMORY.md, and .agents/skills. Tool permissions, model settings, and limits remain shared in Settings; workspace-write permits native writes, and read-only removes model mutation tools.

**Default workspace** uses STUDIO_WORKSPACE_ROOT for existing conversations without a Project. The conversation menu's **Change workspace** moves an idle conversation to a saved Project or back to the default root. Future Runs use the new root; historical Runs and copied evidence keep their original roots. Finish/cancel active work before moving it. Duplicate/fork preserves the selected Project; workspace files are shared, not copied.

The workspace path appears in the conversation header, Workspace, and each saved Run's Inspector. Ordinary history folders organize conversations separately from Project filesystem roots. Registering a Project does not copy files, create directories, or expose application runtime data as a workspace. Project root paths are fixed registrations; richer project management is later work.

## Memory and Skills

**Workspace → Memory** edits .rtagent/MEMORY.md. The agent uses read_memory/save_memory for the same file. There is no vector store or hidden automatic summarization. Conversation compaction is a separate explicit action, not an automatic memory update.

Prompt composer shows the active project's selected root instruction document, memory, catalog, and selected Skill sources in their actual insertion position. At each directory, the first nonempty AGENTS.override.md takes priority over AGENTS.md. Root instructions are captured initially; `read_instructions` loads the chain from the workspace root to a requested directory or file parent, with deeper rules taking precedence and a 32 KiB combined cap. Linked Markdown documents are read only when the agent requests them.

At submission, root instructions, memory, and Skill metadata are captured with explicit sources in the Run snapshot alongside `runtime:environment`. They are not repeatedly appended to conversation history. Later edits affect new Runs. Memory updates reject stale hashes. Local memory is ignored by this repository's Git configuration; never put credentials in it.

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

**$debugging** guides reproduction, evidence-based diagnosis, a focused repair, and the same check after the fix. **$code-review** guides source/flow inspection, confirmed findings, and read-only review. These are small procedure files; select read-only in Settings when the execution boundary must prohibit native mutations.

## Conversations and prompt editing

The sidebar separates **Active** and **Archived** conversations, supports title search, and groups history in flat folders. Use a conversation's **…** menu to rename it, move it, duplicate it, choose a fork point, archive/restore it, or delete it. Folder menus rename or delete a folder; deleting a folder moves its conversations to **Unfiled** and preserves their records.

**Duplicate** copies the full saved conversation and its completed runs/traces. **Fork** copies history through a selected successful turn; **Fork here** performs the same operation from a turn header. Copies have independent IDs and a **Copied history** label. They never repeat model/tool calls, clone checkpoints, or copy workspace files. Copies keep their source conversation’s Project for future work; saved Run roots remain immutable. Only successful turns become input history for subsequent runs. Finish or cancel an active Run before copying, archiving, or deleting its conversation.

Chat displays all turns together. The conversation toolbar and message input stay fixed while history scrolls. Archived conversations can be inspected; restore one to send another message.

In **Prompt**, the **Active preset** selector stays usable with unsaved changes. **New**, **Duplicate**, and **Rename** have separate controls. Each preset keeps its draft while switching; **Undo**, **Redo**, and **Discard changes** make edits reversible. Blocks are grouped into **Before conversation** and **Before your message** positions. Saving advances the preset revision; draft edits are local to the current page and are lost on reload.

The five built-in presets share six editable blocks for identity, boundaries, practical work, native tools, memory/Skills, and reporting, followed by a seventh task-focus block. They now include background/input/deadline, trusted-runtime/download and derived-summary instructions. Coding emphasizes implementation and verification; Debugging emphasizes reproduction and root cause; Code review emphasizes evidence and findings; General agent handles ordinary project work; Security analysis remains the default. Exact recognized unchanged shipped presets upgrade automatically; user edits and existing Run snapshots remain unchanged. New start/input/download permissions begin at Ask; poll/stop are Allowed. Explicit choices are preserved.

Open **Context** to inspect original/effective history estimates and **Compact older turns**, retaining one to ten recent successful turns intact. Compaction calls the selected model with no tools and records its requests/usage. The source history and previous Run snapshots remain unchanged. New Runs use a labeled derived summary plus recent history; **Restore full context** restores all source messages. Failed/cancelled/stale reductions never activate. Estimates are history-only byte heuristics, distinct from full Prompt inspection and actual token usage. A budget error requires an explicit user action; there is no silent trimming.

Prompt and Workspace Skills have bounded JSON **Import/Export** controls. Presets import with new preset/block IDs; Skills publish only new validated names with their text resources. Packages exclude settings and execution state. [Harness interfaces](harness.md) covers CLI/JSON-RPC, HTTP/Python clients, trusted tool/hooks integration, reusable formats and Messages provider setup.

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
uv run python scripts/smoke_agent_tools.py
uv run python scripts/smoke_stage3.py
uv run python scripts/smoke_stage4.py
uv run python scripts/smoke_desktop.py --native
```

It temporarily binds port 8766 and uses a disposable workspace with an explicitly simulated model. It exercises Windows AppContainer/PowerShell or Linux bubblewrap/Bash. It verifies direct Run retry identity, actual command/file operations, hidden host paths, network isolation, memory/Skills, context provenance, read-only enforcement, and cancellation cleanup. It does not call a model service or modify the selected user workspace.

The conversation check uses port 8767 with separate temporary data. It verifies fork boundaries, duplicate traces and provenance, stale-edit rejection, archive/restore, folder rename/delete, and continuing a fork after deleting its source.

The project check uses port 8769 and sibling temporary folders to verify project-bound files, commands, instructions, memory, Skills, idle moves, frozen Run roots, and busy-session rejection. The desktop check uses port 8776 and isolated folders. On Windows, `--native` opens and operates only its owned system dialogs to verify selection, cancellation, disconnect and shutdown; omit that option on a headless machine. It also verifies typed errors and explicit Skill authoring under agent read-only settings.

The agent-tool check uses port 8770. It verifies actual search exclusions, ranged reads and full hashes, nested instruction priority, Add/Update/Delete patches, stale/ambiguous rejection, line-ending preservation, read-only enforcement, native commands, and plan persistence/copies/restart.

For explicit real-model acceptance on Windows with the main service running:

```powershell
uv run python scripts/acceptance_stage2.py --live
uv run python scripts/acceptance_agent_tools.py --live
```

Both use the configured real provider and may incur model usage. Each owns port 8768, a registered project, and separate data; run them separately. Local results stay under ignored var. The agent-tool acceptance uses Coding and Code review presets, guarded multi-file patches, scoped instructions, recorded plans and actual literal search, with independent sandbox checks. No real service is called by the default offline tests.

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
uv run python scripts/check_docs.py
uv run python scripts/check_layers.py
```

Browser checks use a separate backend and temporary data. Build first, then run `npx playwright install chromium` and `npm run test:e2e` from web/. Offline checks do not replace acceptance against a real model.

## Delivery stages

The previous foundation/compiler/conversation/tools/recovery milestones are all subdivisions of **Stage 1**. These stages describe delivery boundaries, not instructions to build speculative infrastructure now.

| Stage | Scope | Exit criteria / status |
| --- | --- | --- |
| **1 — Inspectable execution foundation** | Web workspace, prompt blocks, provider boundary, single-agent loop, MCP, traces, persistence, approvals, recovery | Implemented locally; a basic live-model/native-tool loop has passed on Windows. |
| **2 — Working local agent** | Direct Run, live composition, Projects/workspace binding, native commands/read/write/edit, OS isolation, instructions, memory, Skills and skill creation | Complete on Windows; real-model coding/analysis, restart reuse, and native/project boundary checks passed. See [acceptance](acceptance-stage2.md). Linux requires acceptance on its own host. |
| **3 — Daily agent use** | Explicit context compaction, context accounting, session branching, diff review, command output streaming, interactive/background process lifecycle, practical dependency/network policies | Complete on Windows; substantial real-model work, preserved history, native stream/Unicode pipes/tree cleanup and explicit dependency policy passed. See [acceptance](acceptance-stage3.md). Full terminal emulation and Linux-host acceptance are outside this delivery claim. |
| **4 — Extensible harness** | Stable programmatic run interface, CLI/JSON/RPC, focused lifecycle/tool hooks, reusable prompt/skill packages, more providers | Implemented and accepted with the shared loop, actual CLI/RPC, trusted extensions and package/provider HTTP contracts. See [acceptance](acceptance-stage4.md); external Claude remains unverified. |
| **5 — Security analysis workflows** | Stateful MCP, accepted IDA/Ghidra/debugger/browser integrations, evidence/artifacts, reusable analysis skills | Complete representative authorized reverse-engineering, debugging, and CTF tasks with real tools and inspectable evidence. |
| **6 — Evaluation and justified scale** | Reproducible task sets, replay/comparison, A/B and ablation, usage/cost reporting; bounded delegation, more workers, PostgreSQL or teams only when needed | Give each capability a real workload and its own acceptance criteria. Preserve the simple local path. |
