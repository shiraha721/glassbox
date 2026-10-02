# Security Agent Studio

A local agent workspace for coding, reverse engineering, CTF challenges, debugging, and other authorized security work. Compose inspectable prompts, run a tool-using agent, and retain evidence for each model and tool call.

The target is an extensible everyday agent harness in the scope of [pi](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent), with prompt composition and inspection as core features. The original Web UI and tool loop are **Stage 1**, not the finished product. Develop working vertical features without speculative services or a generic plugin framework.

## Current status

As of 2026-10-03, Stage 1 is implemented and the first Stage 2 workspace capabilities are available:

- Direct **Run**, streaming replies, persistent conversations, cancellation, approval, and checkpoint recovery. Preview is optional.
- Prompt blocks with roles, ordering, conditions, variables, and source mapping; immutable Run inputs.
- OpenAI-compatible Chat Completions with configurable Base URL, model, and server-side API key. The real adapter is the default; a missing model produces a configuration error.
- Actual Bash execution in bubblewrap (through WSL on Windows), plus list/read/write/edit file tools.
- Workspace instructions, persistent Markdown memory, on-demand Skills, and a bundled skill-creator that saves reusable procedures.
- Stateless stdio/Streamable HTTP MCP, per-tool permissions, captured requests, tool results, usage, and redacted exports.

Local HTTP checks exercise actual tools, sandbox boundaries, memory, Skills, and cancellation. The OpenAI-compatible request boundary is covered with an HTTP fixture. **Live model acceptance is pending user endpoint configuration**; simulated decisions do not establish real model quality.

See [Architecture](docs/architecture.md) for the implemented design and [AGENTS.md](AGENTS.md) for development conventions.

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
STUDIO_WSL_DISTRO=
```

Start with `uv run rtagent` and open **http://127.0.0.1:8001**. Enter a task and click **Run**, or press **Ctrl+Enter / Cmd+Enter**. Open **Prompt** to edit the preset and **Inspector** to examine requests. **Preview** inspects the frozen first request without calling a model; it is never a prerequisite for Run.

The app opens directly without login or a workspace access token. Startup applies migrations. Use one server worker on loopback. Changing the workspace root or .env provider options requires a restart. Explicit .env provider values override saved settings on startup; edits in **Settings** apply immediately to new Runs until the next restart. API keys stay on the server.

For a keyless local endpoint, leave the credential reference in Settings empty. The adapter requires streaming Chat Completions and tool calls. It does not silently rewrite roles, retry requests, or fall back to simulation.

## Bash and workspace tools

On Linux, install Bash, bubblewrap, and util-linux. On Windows, install them in a WSL distribution and set STUDIO_WSL_DISTRO if it is not the default distribution. In Debian/Ubuntu or its WSL shell:

```bash
sudo apt-get update
sudo apt-get install bash bubblewrap util-linux
```

**Workspace** shows whether an actual sandbox probe succeeds. The app can start without these dependencies; Bash reports an actionable error if isolation is unavailable. There is no unrestricted fallback to Git Bash, PowerShell, or the host shell.

The boundary follows the separation between OS isolation and approval policy described in [Codex sandboxing](https://learn.chatgpt.com/docs/sandboxing):

- Commands see the project at /workspace, read-only system binaries/libraries, a private process namespace, and temporary scratch space. Host home directories and Windows drives are not mounted.
- The environment is cleared. Server credential variables are not forwarded. Existing .env files, Git metadata, runtime databases, the configured data directory, and dependency/cache directories are masked. .env.example remains readable.
- Network access is disabled, including host services and downloads. WSL linked paths/junctions cause a clear refusal instead of traversal through DrvFS.
- **Settings → Bash sandbox** selects workspace-write or read-only. Read-only also removes native file/memory/skill mutation tools from model exposure. Scratch storage remains writable.
- Tool deadlines, bounded stdout/stderr, and process-tree cleanup apply. Timeout/cancellation can leave partial project edits; inspect those files before repeating a write.

On Windows/WSL, each Bash call uses a filtered copy on a private Linux filesystem. This prevents case aliases such as .ENV from bypassing a .env mask on a Windows drive. Changed files are copied back after the command stops, with checks for concurrent host edits; live edits appear after the call completes. The copy is limited to 64 MiB of files and 10,000 entries in each direction, excluding dependencies and runtime data. Unsupported links, case collisions, or conflicting edits fail explicitly. Native Linux uses a direct workspace mount.

This is Linux/WSL isolation, not Codex's native Windows sandbox. Network allowlists, interactive terminals, persistent background jobs, and disk quotas are not implemented. The boundary assumes the workspace is not concurrently modified by a hostile host process.

Native file tools use relative paths, reject traversal/links, and bound UTF-8 files to 64 KiB. read_text returns content and a hash; write_text requires that hash before replacing a file. edit_text requires exactly one literal match. File tools use application path checks; Bash uses OS isolation.

MCP servers run **outside the Bash sandbox**, under their configured transports and permissions. Add a stdio executable and arguments or Streamable HTTP URL in Settings, select **Save & discover**, then enable individual tools. New MCP tools default to Disabled. Connections are stateless; IDA/Ghidra workflows need separate integration acceptance.

## Memory and Skills

**Workspace → Memory** edits .rtagent/MEMORY.md. The agent uses read_memory/save_memory for the same file. There is no vector store or hidden automatic summarization.

At submission, root AGENTS.md, memory, and skill metadata are captured with explicit sources in the Run snapshot. They are not repeatedly appended to conversation history. Later edits affect new Runs. Memory updates reject stale hashes. Local memory is ignored by this repository's Git configuration; never put credentials in it.

Workspace skills live at `.agents/skills/<name>/SKILL.md`:

```markdown
---
name: evidence-review
description: Review workspace evidence and prepare a report with file references.
---

Read the evidence, distinguish facts from assumptions, and cite file paths.
```

Only name, description, and location enter the default catalog. The model calls load_skill when a description matches the task. To load a skill explicitly, select it in **Workspace → Skills** or mention **$evidence-review** in your task. Supporting files can live beside SKILL.md and be read with file tools. Invalid entries are shown in Workspace.

The bundled **$skill-creator** guides the agent to inspect existing procedures, write one with save_skill, and load it again to validate the result. For example: “$skill-creator create a skill for reviewing this project's Python changes.” You can also create/edit skills directly in Workspace. Bundled skills are read-only; project skills can be committed after review. Skill text cannot grant permissions or alter the sandbox.

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
```

It temporarily binds port 8766, uses a disposable workspace and an explicitly simulated model, and requires working Linux/WSL bubblewrap. It verifies direct Run retry identity, actual file/Bash operations, hidden host paths, network isolation, memory/Skills, context provenance, read-only enforcement, and cancellation cleanup. It does not call a model service or modify the selected user workspace.

For manual offline checks, explicitly select **Simulated model** in Settings or set STUDIO_PROVIDER=fake. Commands such as `/read README.md`, `/echo hello`, and `/tool bash {"command":"pwd"}` use simulated decisions with real tools. `/slow` exercises Stop. Simulation is a development aid, not the default provider.

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
| **1 — Inspectable execution foundation** | Web workspace, prompt blocks, provider boundary, single-agent loop, MCP, traces, persistence, approvals, recovery | Implemented locally. Live-provider acceptance remains pending endpoint configuration. |
| **2 — Working local agent** | Direct Run, Bash/read/write/edit, OS workspace sandbox, instructions, persistent memory, Skills and skill creation | Implemented in this increment and exercised through real local requests. Complete real-model coding/analysis acceptance before declaring the stage complete. |
| **3 — Daily agent use** | Explicit context compaction, context accounting, session branching, diff review, command output streaming, interactive/background process lifecycle, practical dependency/network policies | Complete substantial multi-file tasks across stop/restart/resume with reliable context and no stray processes; verify resource/permission boundaries. |
| **4 — Extensible harness** | Stable programmatic run interface, CLI/JSON/RPC, focused lifecycle/tool hooks, reusable prompt/skill packages, more providers | Use the same loop through Web, CLI, and embedding; add integrations without patching the core. Derive hooks from real use cases. |
| **5 — Security analysis workflows** | Stateful MCP, accepted IDA/Ghidra/debugger/browser integrations, evidence/artifacts, reusable analysis skills | Complete representative authorized reverse-engineering, debugging, and CTF tasks with real tools and inspectable evidence. |
| **6 — Evaluation and justified scale** | Reproducible task sets, replay/comparison, A/B and ablation, usage/cost reporting; bounded delegation, more workers, PostgreSQL or teams only when needed | Give each capability a real workload and its own acceptance criteria. Preserve the simple local path. |
