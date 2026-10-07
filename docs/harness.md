# Harness interfaces and extensions

The agent is available through HTTP, a CLI, newline JSON-RPC and Python embedding. All paths use the same compiler, immutable Run snapshots, permission decisions, approval/checkpoint recovery and receipts. There is one LangGraph loop in one service process, and no automatically loaded plugin code.

## Attach to the running service

Start `uv run rtagent serve` (or `uv run python -m rtagent`; add `--port` for another port) and open http://127.0.0.1:8001. Client commands attach to that service over HTTP; they never open its databases. The older `python -m rtagent.main` entry remains supported.

```powershell
uv run rtagent run "Inspect the project and report its entry point" --preset coding --wait --json
uv run rtagent run --task-file task.txt --session SESSION_ID --skill debugging --wait
uv run rtagent run "Review src/ for injection" --model anthropic:claude-opus-5-5 --effort high --access read-only
uv run rtagent sessions
uv run rtagent status RUN_ID
uv run rtagent events RUN_ID --after 0
uv run rtagent approve RUN_ID APPROVAL_ID
uv run rtagent approve RUN_ID APPROVAL_ID --reject
uv run rtagent resume RUN_ID --resolutions decisions.json
uv run rtagent cancel RUN_ID
```

Use `--url http://127.0.0.1:8001` **before** the subcommand to choose a service. `--project PROJECT_ID` selects a registered workspace for a new conversation. Existing conversations keep their saved Project; change that membership explicitly through the Web/API before submitting a new turn. `--skill` can repeat. `--model PROVIDER_ID:MODEL` (a provider ID from Settings, e.g. `openai:gpt-5`, or `simulated:simulated`), `--effort` and `--access read-only|ask|workspace-write` apply to this Run only; without them the saved default model and sandbox mode apply. `--effort` requires `--model`. `--task-file` is UTF-8; when task/file is omitted, Run reads stdin. Standard input/output/error use UTF-8 on Windows as well as Linux.

`run` returns the accepted Run immediately; `--wait` waits for success/failure or required attention, including approval and interrupted outcomes. Approval is never automatic. A waiting approval exits successfully with that state/ID so the caller can decide; failed/cancelled Runs and client errors return exit code 1. Ctrl+C detaches the client (130); it does not cancel saved work. The printed Run/conversation IDs and `status` allow reconnection. `wait`/`events` emit JSON. An event page contains at most 200 persisted numbered events; advance the cursor to its last sequence and request the next page. SSE remains available for browser subscribers.

Recovery decisions use application receipt IDs from `unknown_calls`, rather than the model's tool IDs:

```json
{"CALL_ID": {"action": "record", "result": "Result independently verified after the interruption"}}
```

Use `retry` only after deciding repetition is appropriate. A model call with an unknown outcome must be retried; tools may instead accept a verified recorded result. Existing recovery invariants also apply to these clients.

## Python client and stable submission

```python
import asyncio
from rtagent.client import AgentClient
from rtagent.domain import ModelChoice

async def main() -> None:
    async with AgentClient() as client:
        run = await client.run(
            "Inspect the workspace",
            preset_id="coding",
            model=ModelChoice(provider_id="openai", model="gpt-5", reasoning_effort="high"),
            access="read-only",
        )
        result = await client.wait(run.id)
        print(result.status, result.id, result.approval)

asyncio.run(main())
```

`AgentClient.run` reads current revisions and optionally creates a conversation. `model` and `access` are optional per-Run choices, as in the CLI; the Run view reports the `provider_id`, `model`, `reasoning_effort` and `access` that were used. For reliable retry after a lost response, capture a typed `PreviewRequest` containing the observed session/preset/config revisions and input, then call **`client.start(request, key)` with the same request and key**. The server returns the original Run even if the session revision later changes. Reconstructing a convenience request with newer revisions or creating a new session is a new submission. Neither convenience nor explicit submission requires Preview. Exact request/response shapes are in `/openapi.json` and `src/rtagent/domain/models.py`.

The Web client follows the same rule: an unchanged visible task retains its complete submitted body and key across a lost response and later history revisions. HTTP validation, business and unexpected pre-response failures share `{"error":{"code":"...","message":"...","details":null,"request_id":"..."}}`. Field errors contain locations/types without submitted input values. AgentClient handles non-JSON failures as transport errors; JSON-RPC returns `-32603` for an unexpected operation and continues reading independent frames.

## Newline JSON-RPC

```powershell
uv run python -m rtagent rpc
```

Each UTF-8 input line is an ID-bearing JSON-RPC 2.0 request; each output line is its response. Requests are processed sequentially, capped at 512 KiB per line. Batch envelopes and notifications are outside this interface. Run submission is asynchronous; use state/event requests between operations. No arbitrary tool invocation or URL forwarding exists.

```json
{"jsonrpc":"2.0","id":1,"method":"run.submit","params":{"task":"Read README.md","preset_id":"coding"}}
{"jsonrpc":"2.0","id":2,"method":"run.get","params":{"run_id":"RUN_ID"}}
{"jsonrpc":"2.0","id":3,"method":"events.list","params":{"run_id":"RUN_ID","after":0}}
{"jsonrpc":"2.0","id":4,"method":"rpc.schema"}
```

| Methods | Parameters and result |
| --- | --- |
| `rpc.schema`, `bootstrap` | No params; supported methods/contract location or service projection. |
| `run.submit` | `task`, optional `session_id`, `project_id`, `preset_id`, `variables`, `skills`, `key`, `model` (`{provider_id, model, reasoning_effort}`), `access`; current-revision Run. |
| `run.start` | `{request: PreviewRequest, key: string}`; captured-request retry identity. |
| `run.get`, `run.cancel` | `run_id`; saved Run projection. |
| `run.wait`, `events.list` | `run_id` plus optional `timeout` (seconds, up to 3600) or `after`; Run or finite event page. |
| `run.approve` | `run_id`, `approval_id`, `body: {decision: approve or reject}`. |
| `run.resume` | `run_id`, `body: {resolutions: ...}` using the shared recovery contract. |
| `run.files`, `run.revert_files` | `run_id`; the files the Run changed with file tools and their state, or the result of undoing them. |
| `session.create`, `session.get` | Creation `body` (optional title/project), or `session_id`. |
| `session.copy`, `session.update`, `session.delete` | `session_id` plus the corresponding typed `body` for copy/update; delete has no body. |
| `context.get`, `context.compact`, `context.restore` | `session_id`; compact/restore include the typed revision-checked `body`. |
| `process.list`, `process.input`, `process.stop` | `session_id`, plus `process_id` for controls and `{text, eof}` body for input. |
| `preset.export`, `preset.import` | `preset_id` for export; versioned package `body` for import. |
| `skill.export`, `skill.import` | `name` for export or package `body` for import; optional `project_id`. |

Unknown parameters fail. IDs are strict strings/integers, not booleans. Errors distinguish malformed JSON (`-32700`), invalid envelope (`-32600`), unknown method (`-32601`), invalid parameters (`-32602`) and application/service errors (`-32000`, safe code/status metadata). Oversized input returns an error and closes that RPC process. Detaching does not cancel Runs.

## Own an embedded harness

Use embedding when another Python program owns the agent lifecycle. **Do not share its data directory with a running Web server or another Harness.** Use AgentClient to attach to an existing owner.

```python
import asyncio
from pathlib import Path
from rtagent.infrastructure.config import Settings
from rtagent.harness import Harness

async def main() -> None:
    settings = Settings(data_dir=Path("var/embedded"), workspace_root=Path("."))
    async with Harness(settings) as harness:
        run = await harness.run(
            "Inspect this project's startup", preset_id="coding", access="read-only"
        )
        result = await harness.wait(run.id)
        if result.approval:
            print("Approval required:", result.approval)
        print(result.status)

asyncio.run(main())
```

`Harness` has `run`, `wait`, `approve`, `cancel` and `resume`, plus its concrete `studio`, `repository` and `registry` for supported local integration. It constructs no resources on import. Entering migrates/seeds and opens the same graph/checkpointer used by Web. Exiting awaits summaries, Runs, native processes and database closure. A successful Run can leave an owned background command alive; it ends at Stop, its absolute deadline, or harness/service shutdown. Restart retains evidence, never relaunches processes.

## Focused trusted extensions

```python
import asyncio
import json
from rtagent.domain import RunEvent, StudioConfig, ToolCall, ToolDefinition
from rtagent.domain.hooks import Hooks
from rtagent.infrastructure.workspace.files import Workspace

async def observe(event: RunEvent) -> None:
    """Consume a committed, redacted event without mutating its stored payload."""
    print(event.run_id, event.seq, event.type)

async def guard(call: ToolCall, config: StudioConfig) -> str | None:
    """Restrict direct file writes in addition to the existing tool policy."""
    if call.name in {"write_text", "edit_text"} and call.args.get("path") == "KEEP.txt":
        return "Preserve KEEP.txt."
    return None

async def source_inventory(args: dict, workspace: Workspace) -> str:
    """Return a bounded source inventory in the current frozen workspace."""
    return json.dumps(await asyncio.to_thread(workspace.search_files, "**/*.py"))

hooks = Hooks(on_event=observe, before_tool=guard)
# Construct Harness(settings, hooks), then register before entering:
# harness.registry.register(ToolDefinition(
#     name="source_inventory", description="List project Python sources.",
#     input_schema={"type": "object", "properties": {}, "additionalProperties": False},
# ), source_inventory, writes_workspace=False)
# Inside the context, explicitly enable config.tools["source_inventory"] and save config.
```

`on_event` receives a typed redacted copy **after** the event transaction, with a two-second delivery timeout. Callback errors produce a saved `hook.failed` event without changing a known Run/effect status or recursively notifying failures. It is best-effort live observation: imported/copy/recovery transactions and missed deliveries are not replayed; use persisted events for a complete audit.

`before_tool` runs **after** core schema validation, permissions and any approval. It receives copies of the captured call/config, has a five-second timeout and can add a denial reason. It cannot grant a core-denied call. The example guards only the named direct-write tools; it is not a complete filesystem protection rule for commands/patches.

`registry.register` accepts at most twenty explicit local tools, a JSON Schema and an async `(args, Workspace) -> str` handler. Returns are capped at 64 KiB and captured like other tool results. New tools require explicit allow/ask permission; frozen schema hashes must still match at dispatch. `writes_workspace` defaults to true; read-only mode excludes/rejects declared mutators. Trusted handlers run inside the application process and have host Python authority, like configured MCP integrations outside the command sandbox. They are not an isolation boundary for untrusted code. Skills remain Markdown data and cannot register handlers.

Registration rejects invalid JSON Schemas before model exposure. Unexpected handler failures or invalid returned output after dispatch have uncertain effects: the Run interrupts and requires an explicit recovery decision. An integration can raise a known structured AppError for a verified failure; it must not claim certainty about an effect it cannot establish.

Web integrations pass `hooks=` and `configure=` to `create_app`; the configure callback receives that same Harness before entry. This is the accepted way to add the source-inventory extension without patching core files or creating an alternate loop.

## Reusable data packages

Prompt/Workspace expose **Import/Export** controls. CLI equivalents:

```powershell
uv run rtagent preset export coding.json --id coding
uv run rtagent preset import coding.json
uv run rtagent skill export review.json --id code-review
uv run rtagent skill import review.json --project PROJECT_ID
```

Exports create new local files and refuse overwrite. Package formats are:

```json
{"format":"rtagent.preset.v1","preset":{"id":"source-id","name":"Coding","revision":1,"blocks":[]}}
```

```json
{"format":"rtagent.skill.v1","name":"csv-review","files":{"SKILL.md":"---\nname: csv-review\ndescription: Review CSV input.\n---\n\nRead resources/rules.md.\n","resources/rules.md":"Preserve quoted fields and verify actual output.\n"}}
```

Preset import always creates fresh aggregate/block identities and a revision-one saved preset. Skill imports are new-name-only, with matching frontmatter, portable forward-slash relative paths, no links/traversal/case aliases, one to fifty text resources, 64 KiB per file and 1 MiB total. All resources stage and validate before a directory rename publishes the Skill. Existing/bundled names cannot be overwritten. Export a bundled procedure, change the name/frontmatter and import it as a project procedure when customization is needed. Browser/CLI JSON inputs additionally cap at 2 MiB. No secrets, runtime configuration, live effects or checkpoint state are included by these formats.

Studio owns package use cases for HTTP and embedding. Explicit user Skill import follows the same authoring policy as the memory/Skill editors, including when agent tools are read-only. Agent mutation tools and native commands still obey their captured policy; importing procedure text cannot grant tool permissions.

## Provider support

Providers are configured in **Settings → Providers** (or seeded from `.env`) and addressed by ID. Both adapters are explicit; there is no fallback between them:

- **OpenAI-compatible** streaming Chat Completions, with `reasoning_effort` low, medium or high.
- **Anthropic Messages**, with `output_config.effort` low through max. Leading canonical system blocks become ordered top-level system text blocks; assistant calls become `tool_use`, paired results become user `tool_result` blocks. Thinking blocks are replayed unchanged within a Run and dropped from later turns (see [Architecture → Providers](architecture.md#providers)). Interleaved system blocks, incomplete streams and unsupported server-tool content fail explicitly; a classifier refusal is reported as `provider_refusal`.

```dotenv
STUDIO_PROVIDER=anthropic
ANTHROPIC_BASE_URL=https://api.anthropic.com/v1
ANTHROPIC_MODEL=claude-opus-5-5
ANTHROPIC_API_KEY=
```

Inspector and every call record disclose transformations, canonical input, prepared/actual body, usage and returned metadata. No role relocation or automatic retry occurs. The [official Messages](https://platform.claude.com/docs/en/api/messages/create) and [streaming contracts](https://platform.claude.com/docs/en/build-with-claude/streaming) describe the external protocol. The adapter is tested against local real-HTTP fixtures, including thinking replay and refusals; no external Claude credential was available for a production endpoint check. See the [Stage 4 acceptance record](history/acceptance-stage4.md).
