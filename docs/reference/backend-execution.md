# Model adapters and the agent execution graph

The runtime is one explicit LangGraph model/tools loop. Providers perform one model request, never another agent loop. Application data in app.db tracks browser state and durable receipts; checkpoints.db records graph positions. This separation explains completion, approval, cancellation, and recovery.

[Reference index](README.md) · [System architecture](../architecture.md)

## src/rtagent/providers.py

**Layer: Infrastructure / model adapter.** Maps canonical messages to LangChain ChatOpenAI, prepares the first body for inspection, captures actual HTTP bodies, and normalizes streaming results.

**Connections and effects:** Studio.preview and Runner.model_node call prepare; only Runner invokes generate. resolve_secret is called only at real execution. LangChain serializer private API use is localized here and contract-tested; the provider does not decide tool permissions or execute tools.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`langchain_messages`](../../src/rtagent/providers.py#L19) | Convert canonical Message values to SDK message objects. | Map system/user/assistant to SystemMessage/HumanMessage/AIMessage, retain assistant calls/IDs/args, and require a tool_call_id for ToolMessage. | Provider integration cannot add instructions or lose role/call pairing; source mapping remains in the separate canonical evidence. |
| [`model`](../../src/rtagent/providers.py#L44) | Construct one ChatOpenAI client from ProviderConfig and an in-memory key. | Reject blank model/unsafe URL, configure Chat Completions only, no SDK retries, temperature/output limit, 90-second timeout, streamed usage, and optional HTTPX client. | Real configuration errors fail visibly; no model-name fallback or simulation is implicit. A placeholder key permits SDK construction for a keyless local endpoint, whose server must ignore it. |
| [`call_options`](../../src/rtagent/providers.py#L77) | Produce shared streaming/tool request options. | Set stream=true and include_usage; convert each frozen ToolDefinition dictionary to a function tool with name/description/parameters; omit tools when empty. | Preview and execution use exactly the same schema serialization and streaming settings. |
| [`prepare`](../../src/rtagent/providers.py#L95) | Return the exact selected adapter body without sending it. | Fake returns explicit simulation, Messages calls its loss-checking serializer, OpenAI uses the pinned SDK serializer and verifies canonical roles. | Preview and execution share preparation; provider-required transformations are explicit rather than hidden or guessed. |
| [`fake_stream`](../../src/rtagent/providers.py#L122) | Yield explicit offline decisions and synthetic text, with unknown usage. | Inspect current_input and existing tool results; /read, /echo, and /tool emit one call, /error raises, /slow delays text chunks, and post-tool output reports real tool evidence. | Simulation exercises actual runtime/tool/approval behavior without credentials; it is a selected development adapter, not a fallback or quality claim. |
| [`generate`](../../src/rtagent/providers.py#L195) | Stream one selected provider request without another tool loop. | Route explicit fake or Messages streams, otherwise capture the OpenAI body and reconstruct LangChain text/tool chunks, preserving IDs/usage/metadata. | No automatic retry, silent fallback or partial-success stream can repeat effects or misrepresent provider support. |
| [`generate.capture`](../../src/rtagent/providers.py#L216) | Check and record the outbound application JSON before transmission. | Parse request.content, compare it exactly with prepared, raise request_mismatch if unequal, otherwise await the on_wire receipt callback. | Inspection is evidence of the body the client sends, not guessed SDK behavior. Hook-level capture includes no authorization header and makes no claim about hidden provider-side additions. |
| [`transformations`](../../src/rtagent/providers.py#L181) | Describe explicit application serialization for an adapter. | Return a concrete adapter-specific list of role/tool/block transformations, or none for simulation. | Preview, Run and reduction receipts explain wire differences instead of hiding them in an SDK. |

### Request evidence and compatibility

For each model step the runtime stores **canonical_messages/source_map**, **prepared_request**, **wire_request_body**, and **response_metadata/usage** as separate fields. Canonical sources explain origin but are not provider fields. The prepared request is produced by the SDK serializer; the HTTP hook sees the JSON body the client is about to transmit. Exact comparison happens before sending it. A simulated call has `wire_request_body=None` and `simulated=True`.

The real adapter requires streaming Chat Completions with valid function tool calls and stable IDs. It does not implement Responses, automatic model capability negotiation, arbitrary role merging, tokenizer-specific budgeting, or price lookup. `max_retries=0` and `follow_redirects=False` make observable side-effect boundaries easier to reason about. Capturing a body is not proof that a remote server completed the request; lost responses still produce unknown outcomes.

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def langchain_messages(messages: list[Message]) -> list
def model(config: ProviderConfig, key: str, client: httpx.AsyncClient | None=None) -> ChatOpenAI
def call_options(tools: list[dict[str, Any]]) -> dict[str, Any]
def prepare(config: ProviderConfig, compiled: CompiledPrompt, tools: list[dict[str, Any]]) -> dict[str, Any]
async def fake_stream(messages: list[Message], tools: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]
def transformations(adapter: str) -> list[str]
async def generate(config: ProviderConfig, compiled: CompiledPrompt, tools: list[dict[str, Any]], prepared: dict[str, Any], on_wire: Callable[[dict[str, Any] | None], Awaitable[None]]) -> AsyncIterator[dict[str, Any]]
async def generate.capture(request: httpx.Request) -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/runtime.py

**Layer: Execution orchestration / runtime.** Owns the explicit graph, per-Run tasks, active-time/attempt budgets, durable effects, and interrupt recovery.

**Connections and effects:** Reads frozen snapshots and writes projections/events/receipts through Repository; dispatches through ToolRegistry after decide; calls the pure compiler and model adapter. One semaphore allows one active graph invocation at a time. LangSmith tracing is disabled around execution, keeping traces local.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`Runner.__init__`](../../src/rtagent/runtime.py#L48) | Prepare a task/checkpoint owner without I/O. | Store repository/registry/path, create AsyncExitStack, task dictionary, one-slot semaphore, and shutdown flag. | Process lifecycle controls every worker and database resource; constructing a graph owner does not start background work. |
| [`Runner.open`](../../src/rtagent/runtime.py#L66) | Open checkpoint storage, build the graph, and recover queued work. | Enter AsyncSqliteSaver, set it up, register model/tools nodes and explicit routes, compile, then start Repository.recover's queued IDs with resume enabled. | LangGraph owns checkpoint positions while the repository owns effect certainty; no hidden prebuilt agent loop or custom checkpoint-table mutation is added. |
| [`Runner.start`](../../src/rtagent/runtime.py#L86) | Schedule at most one local task for a Run. | Return if its task is still active; otherwise create drive task and attach an identity-checked removal callback. | Idempotent API retries and quick approvals cannot create concurrent graph invocations for the same Run within one process. |
| [`Runner.close`](../../src/rtagent/runtime.py#L98) | Stop owned tasks/processes before closing checkpoint storage. | Mark shutdown, cancel/gather graph tasks, close the shared ProcessManager and checkpoint context. | Native cleanup and durable interruption receipts finish while their stores remain available. |
| [`Runner.drive`](../../src/rtagent/runtime.py#L109) | Execute/resume a frozen Run under ownership and time limits. | Take semaphore, check the frozen root still exists off-thread, use thread_id=run_id, invoke with sync durability and remaining active budget, classify stop/uncertainty, and record elapsed time. | Project/default-root changes never redirect existing execution; explicit receipts and original checkpoints govern recovery. |
| [`Runner.mark_unknown`](../../src/rtagent/runtime.py#L205) | Mark unfinished executing receipts as uncertain. | Read Run trace and rewrite only executing calls to outcome_unknown with evidence/result preserved. | A cancelled network/tool request may already have effects; status records what is known instead of claiming rollback. |
| [`Runner.cancel`](../../src/rtagent/runtime.py#L218) | Await local Stop and settle a Run. | Ignore terminal state; emit cancelling and cancel/gather its task, mark unknown receipts, reread state, then idempotently finish cancelled. | Cancellation arriving before task start or during final metrics must still release the session. External effects and edits remain reviewable rather than undone. |
| [`Runner.model_node`](../../src/rtagent/runtime.py#L235) | Produce the next assistant delta or reuse an existing response. | Address receipt by Run/model step, block unknown outcomes, reuse succeeded result, otherwise compile frozen history/context plus delta, enforce limits/first-body equality, save intent, capture/stream/save result, update usage/messages/events, and return next pending calls. | Receipt-before-checkpoint recovery can reuse a response without another model charge. Compiler/attempt bounds apply on every step; missing usage or repeated uncertain attempts keeps totals null. |
| [`Runner.model_node.on_wire`](../../src/rtagent/runtime.py#L294) | Persist the actual request body in the current model intent. | Update the node's data dictionary and save its executing receipt before the HTTP hook returns. | A restart after network dispatch has evidence of the body that was sent or about to be sent, independent of graph state. |
| [`Runner.tool_node`](../../src/rtagent/runtime.py#L340) | Authorize, execute and pair each pending original tool call. | Reuse known receipts, interrupt for approval, enforce schema/policy before optional additional guard, capture per-dispatch ownership/output, execute one adapter and retain paired result or explicit uncertainty. | Trusted callbacks cannot grant core-denied calls; unexpected integration failures or invalid local results require explicit recovery rather than automatic effect repetition. |
| [`Runner.tool_node.on_output`](../../src/rtagent/runtime.py#L443) | Commit native command output before its receipt completes. | Append a bounded stream frame to executing call data, save the receipt and emit numbered tool.output. | Active chat/finite event clients can inspect real progress; final tool_call_id pairing remains unchanged. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`AgentState`](../../src/rtagent/runtime.py#L35) | Minimal checkpoint payload for one Run. | TypedDict fields run_id, current delta messages, model step, pending calls, and tool_index. | Original configuration/history remain in an immutable application snapshot; graph state only records execution progress. |
| [`Runner`](../../src/rtagent/runtime.py#L45) | Lifecycle-owned single graph worker. | Concrete graph/checkpointer/resources, per-Run asyncio tasks, semaphore, and shutdown flag. | All scheduling, budget enforcement, cleanup, and effect evidence are visible in one bounded implementation. |

### Graph routing and nested callbacks

`START → model`. The first conditional callback chooses `tools` when `pending` is nonempty, otherwise `END`. After each tools node, the second callback stays in `tools` until `tool_index == len(pending)`, then returns to `model`. These two local lambdas route state; they do not perform I/O or authorization. Calls execute sequentially even if the provider returned a batch, preserving a full ordered result set before the next model step.

The task completion lambda in `start` removes an entry only if it still points to that completed task. This prevents an old completion callback from removing a newly scheduled resume task. Every graph invocation sets recursion_limit to max_steps + max_tool_calls + 10; the receipt counters are the actual model/tool attempt ceilings, including retries. Command adapters own their timeout and cleanup, so the outer tool deadline grants Bash/PowerShell an additional 35 seconds to finish cleanup.

### Receipts, checkpoints, and interruptions

Application call IDs use `run_id:model:NNNN` or `run_id:tool:NNNN:NNNN`; model-generated tool IDs are recorded separately and copied unchanged into tool messages. A receipt begins `executing` immediately before an effect. A complete model result becomes `succeeded`; an ordinary validation/native error becomes a `failed` paired tool result. A timeout/MCP transport failure after dispatch can become `outcome_unknown`, causing `interrupted` Run state. A changed MCP schema produces a failed receipt and stops execution instead of making a new request with the old advertised interface.

An approval persists the exact name/arguments/hash before `interrupt`. Reentry first checks a stored decision, because an HTTP approval may arrive before the graph has saved its interrupt checkpoint. No tool effect occurs before allow. `GraphInterrupt` is re-raised rather than converted to a tool error. Resume uses `Command(resume=True)` for an interrupt checkpoint and the same thread; normal saved progress uses `None` graph input. A complete receipt can be reused if the checkpoint is older than the effect result. There is still no exactly-once guarantee for a remote effect whose result was lost.

`drive` records active seconds around each graph invocation, subtracting previously accumulated time on resume. The accounting occurs in a final write, so a hard process crash can lose the last interval. A waiting approval is idle between invocations; queue wait is outside the semaphore-owned execution interval. Failed/cancelled Runs remain inspectable, but only successful deltas become the next conversation's history.

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def Runner.__init__(self, repository: Repository, registry: ToolRegistry, checkpoint_path: str, hooks: Hooks | None=None)
async def Runner.open(self) -> None
def Runner.start(self, run_id: str, resume: bool=False) -> None
async def Runner.close(self) -> None
async def Runner.drive(self, run_id: str, resume: bool) -> None
async def Runner.mark_unknown(self, run_id: str) -> None
async def Runner.cancel(self, run_id: str) -> None
async def Runner.model_node(self, state: AgentState) -> dict[str, Any]
async def Runner.model_node.on_wire(body)
async def Runner.tool_node(self, state: AgentState) -> dict[str, Any]
async def Runner.tool_node.on_output(frame: dict[str, Any]) -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/context.py

**Layer: Application orchestration / explicit context reduction.** Runs bounded no-tools summaries over a frozen prefix and retains every original message.

**Connections and effects:** Studio reserves a job; this module calls the existing compiler/provider and Repository receipts. It is one explicit auxiliary task, not a second agent loop, hidden memory service or automatic retry path.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`compaction_view`](../../src/rtagent/context.py#L22) | Produce an ordinary safe reduction projection. | Extract ownership/source/final text/error/usage and derive model-call count from the saved record. | Context UI needs provenance and progress without transferring full provider bodies by default. |
| [`estimated_tokens`](../../src/rtagent/context.py#L41) | Estimate history-only size. | Serialize message data and round UTF-8 bytes divided by four. | Accounting is labeled heuristic and stays separate from actual usage and full compiled instruction/tool estimates. |
| [`reduce_context`](../../src/rtagent/context.py#L46) | Summarize and conditionally activate captured source. | Reduce output reservation, carry the previous summary, consume UTF-8-safe bounded evidence chunks, shrink only on explicit context overflow, save request intent/wire/result/usage and enforce call/time budgets. | Large sessions can continue without mutating original roles/sources; incomplete, cancelled or stale summaries stay inactive and unknown usage stays null. |
| [`reduce_context.on_wire`](../../src/rtagent/context.py#L115) | Retain checked application request bodies before reduction I/O. | Capture the current call in a default argument, save its wire body and await Repository. | Every paid summary request remains attributable to its frozen canonical input and source offsets. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def compaction_view(record: dict[str, Any]) -> CompactionView
def estimated_tokens(messages: list[dict[str, Any]]) -> int
async def reduce_context(repository: Repository, record: dict[str, Any]) -> None
async def reduce_context.on_wire(body: dict[str, Any] | None, current_call: dict[str, Any]=call) -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/anthropic.py

**Layer: Infrastructure / Messages provider adapter.** Maps inspectable canonical text/tool messages to Anthropic-compatible Messages and parses its streaming protocol.

**Connections and effects:** Provider prepare/generate select this adapter. Credentials resolve only on the server at I/O; the adapter never selects tools, grants permission, retries, inserts agent instructions or hides unsupported roles/content.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`prepare_anthropic`](../../src/rtagent/anthropic.py#L13) | Build the exact Messages request from canonical evidence. | Extract only leading system blocks, keep ordered user/assistant text, preserve tool_use IDs/args and pair tool_result user blocks; validate model/temperature and pairing. | Interleaved system blocks and unsupported ordering fail rather than changing their roles or location silently. |
| [`sse_events`](../../src/rtagent/anthropic.py#L94) | Parse one bounded SSE data event at a time. | Collect data lines until the blank delimiter, cap event bytes at 1 MiB and reject a partial final event. | Broken streams cannot be mistaken for completed responses. |
| [`stream_anthropic`](../../src/rtagent/anthropic.py#L112) | Execute one captured streaming Messages request. | Use explicit HTTP headers/no proxy or redirects, check body equality, reconstruct text and partial-JSON tool blocks, enforce supported event/block/ID bounds and require message_stop; retain model/stop/usage metadata. | Partial/unsupported thinking or server-tool streams fail explicitly, with no fallback or invented token usage. |
| [`stream_anthropic.capture`](../../src/rtagent/anthropic.py#L119) | Record checked wire JSON before transmission. | Parse the actual httpx request body, compare prepared equality and invoke the receipt callback without headers. | API keys never enter evidence; application-level capture does not claim visibility into provider-internal prompts. |

<details>
<summary>Source signatures (including private and nested functions)</summary>

```python
def prepare_anthropic(config: ProviderConfig, compiled: CompiledPrompt, tools: list[dict[str, Any]]) -> dict[str, Any]
async def sse_events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]
async def stream_anthropic(config: ProviderConfig, prepared: dict[str, Any], on_wire: Callable[[dict[str, Any] | None], Awaitable[None]]) -> AsyncIterator[dict[str, Any]]
async def stream_anthropic.capture(request: httpx.Request) -> None
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>

## src/rtagent/hooks.py

**Layer: Application extension contracts / trusted callbacks.** Defines optional event observation and additional tool restriction hooks.

**Connections and effects:** Repository delivers committed redacted events; Runner invokes the guard only after core schema/policy/approval. Callbacks are explicitly supplied trusted Python code with bounded timeouts, not dynamically loaded Skills or sandboxed untrusted plugins.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`Hooks`](../../src/rtagent/hooks.py#L13) | Immutable optional extension callback bundle. | Frozen dataclass with asynchronous on_event and before_tool references. | Event observations cannot mutate stored payloads, and tool guards can add denial but never override a core denial. |

## src/rtagent/execution.py

**Layer: Runtime ownership / per-dispatch context.** Carries trusted Run/session ownership to native command adapters.

**Connections and effects:** Runner sets/resets a ContextVar around Registry.execute. Model arguments never determine process owner IDs; no mutable global current Run or changed execute signature is needed.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`ToolExecution`](../../src/rtagent/execution.py#L10) | Immutable live tool ownership context. | Frozen Run/session IDs plus optional async bounded output consumer. | Foreground streaming and background starts share trustworthy ownership while remaining ordinary tool calls. |
