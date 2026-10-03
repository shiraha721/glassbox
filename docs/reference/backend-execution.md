# Model adapters and the agent execution graph

The runtime is one explicit LangGraph model/tools loop. Providers perform one model request, never another agent loop. Application data in app.db tracks browser state and durable receipts; checkpoints.db records graph positions. This separation explains completion, approval, cancellation, and recovery.

[Reference index](README.md) · [System architecture](../architecture.md)

## src/rtagent/providers.py

**Layer: Infrastructure / model adapter.** Maps canonical messages to LangChain ChatOpenAI, prepares the first body for inspection, captures actual HTTP bodies, and normalizes streaming results.

**Connections and effects:** Studio.preview and Runner.model_node call prepare; only Runner invokes generate. resolve_secret is called only at real execution. LangChain serializer private API use is localized here and contract-tested; the provider does not decide tool permissions or execute tools.

Every function below belongs to the layer named above. The last column identifies its specific architectural role. Private methods and nested functions are included.

| Function / source | Purpose and contract | How it works | Why / architectural role |
| --- | --- | --- | --- |
| [`langchain_messages`](../../src/rtagent/providers.py#L18) | Convert canonical Message values to SDK message objects. | Map system/user/assistant to SystemMessage/HumanMessage/AIMessage, retain assistant calls/IDs/args, and require a tool_call_id for ToolMessage. | Provider integration cannot add instructions or lose role/call pairing; source mapping remains in the separate canonical evidence. |
| [`model`](../../src/rtagent/providers.py#L43) | Construct one ChatOpenAI client from ProviderConfig and an in-memory key. | Reject blank model/unsafe URL, configure Chat Completions only, no SDK retries, temperature/output limit, 90-second timeout, streamed usage, and optional HTTPX client. | Real configuration errors fail visibly; no model-name fallback or simulation is implicit. A placeholder key permits SDK construction for a keyless local endpoint, whose server must ignore it. |
| [`call_options`](../../src/rtagent/providers.py#L76) | Produce shared streaming/tool request options. | Set stream=true and include_usage; convert each frozen ToolDefinition dictionary to a function tool with name/description/parameters; omit tools when empty. | Preview and execution use exactly the same schema serialization and streaming settings. |
| [`prepare`](../../src/rtagent/providers.py#L94) | Return a provider-specific application request body without sending it. | Fake mode returns an explicit simulation object; real mode builds a preview-only client, invokes _get_request_payload, and checks serialized roles against canonical roles. | Reusing the pinned SDK serializer avoids a second approximated request renderer; role-changing adapters are rejected rather than silently rewriting the prompt. |
| [`fake_stream`](../../src/rtagent/providers.py#L119) | Yield explicit offline decisions and synthetic text, with unknown usage. | Inspect current_input and existing tool results; /read, /echo, and /tool emit one call, /error raises, /slow delays text chunks, and post-tool output reports real tool evidence. | Simulation exercises actual runtime/tool/approval behavior without credentials; it is a selected development adapter, not a fallback or quality claim. |
| [`generate`](../../src/rtagent/providers.py#L176) | Stream one real or simulated model call. | Fake mode records no wire body. Real mode opens a no-redirect HTTPX client with capture hook, accumulates LangChain chunks, yields text deltas, validates final calls/IDs, and returns Message/usage/metadata. | The runtime gets canonical output and preserves original IDs. No automatic retry can duplicate a charge; empty/invalid streams fail and provider exceptions expose safe type-based messages. |
| [`generate.capture`](../../src/rtagent/providers.py#L193) | Check and record the outbound application JSON before transmission. | Parse request.content, compare it exactly with prepared, raise request_mismatch if unequal, otherwise await the on_wire receipt callback. | Inspection is evidence of the body the client sends, not guessed SDK behavior. Hook-level capture includes no authorization header and makes no claim about hidden provider-side additions. |

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
| [`Runner.__init__`](../../src/rtagent/runtime.py#L46) | Prepare a task/checkpoint owner without I/O. | Store repository/registry/path, create AsyncExitStack, task dictionary, one-slot semaphore, and shutdown flag. | Process lifecycle controls every worker and database resource; constructing a graph owner does not start background work. |
| [`Runner.open`](../../src/rtagent/runtime.py#L55) | Open checkpoint storage, build the graph, and recover queued work. | Enter AsyncSqliteSaver, set it up, register model/tools nodes and explicit routes, compile, then start Repository.recover's queued IDs with resume enabled. | LangGraph owns checkpoint positions while the repository owns effect certainty; no hidden prebuilt agent loop or custom checkpoint-table mutation is added. |
| [`Runner.start`](../../src/rtagent/runtime.py#L75) | Schedule at most one local task for a Run. | Return if its task is still active; otherwise create drive task and attach an identity-checked removal callback. | Idempotent API retries and quick approvals cannot create concurrent graph invocations for the same Run within one process. |
| [`Runner.close`](../../src/rtagent/runtime.py#L87) | Stop owned tasks and release checkpoints on shutdown. | Mark shutting_down, cancel/gather a snapshot of tasks, then close AsyncExitStack. | Workers need live storage while recording interrupted/unknown outcomes; they finish before checkpoint connections close. |
| [`Runner.drive`](../../src/rtagent/runtime.py#L97) | Execute/resume a frozen Run under ownership and time limits. | Take semaphore, check the frozen root still exists off-thread, use thread_id=run_id, invoke with sync durability and remaining active budget, classify stop/uncertainty, and record elapsed time. | Project/default-root changes never redirect existing execution; explicit receipts and original checkpoints govern recovery. |
| [`Runner.mark_unknown`](../../src/rtagent/runtime.py#L191) | Mark unfinished executing receipts as uncertain. | Read Run trace and rewrite only executing calls to outcome_unknown with evidence/result preserved. | A cancelled network/tool request may already have effects; status records what is known instead of claiming rollback. |
| [`Runner.cancel`](../../src/rtagent/runtime.py#L204) | Await local Stop and settle a Run. | Ignore terminal state; emit cancelling and cancel/gather its task, mark unknown receipts, reread state, then idempotently finish cancelled. | Cancellation arriving before task start or during final metrics must still release the session. External effects and edits remain reviewable rather than undone. |
| [`Runner.model_node`](../../src/rtagent/runtime.py#L220) | Produce the next assistant delta or reuse an existing response. | Address receipt by Run/model step, block unknown outcomes, reuse succeeded result, otherwise compile frozen history/context plus delta, enforce limits/first-body equality, save intent, capture/stream/save result, update usage/messages/events, and return next pending calls. | Receipt-before-checkpoint recovery can reuse a response without another model charge. Compiler/attempt bounds apply on every step; missing usage or repeated uncertain attempts keeps totals null. |
| [`Runner.model_node.on_wire`](../../src/rtagent/runtime.py#L283) | Persist the actual request body in the current model intent. | Update the node's data dictionary and save its executing receipt before the HTTP hook returns. | A restart after network dispatch has evidence of the body that was sent or about to be sent, independent of graph state. |
| [`Runner.tool_node`](../../src/rtagent/runtime.py#L329) | Authorize and execute exactly one pending model call, or reuse its receipt. | Resolve frozen schema/config, count attempts, validate policy, persist ask interrupt/decision, save intent before dispatch, enforce deadline, save success/error/unknown receipt, append one matching tool message, advance tool_index. | Every original model call gets a paired result before the next model request. Durable receipts prevent repeat dispatch on node replay; unknown effects need explicit reconciliation and schema drift stops the Run. |

### Types and ownership

| Type / source | Purpose | Representation / behavior | Why / architectural role |
| --- | --- | --- | --- |
| [`AgentState`](../../src/rtagent/runtime.py#L33) | Minimal checkpoint payload for one Run. | TypedDict fields run_id, current delta messages, model step, pending calls, and tool_index. | Original configuration/history remain in an immutable application snapshot; graph state only records execution progress. |
| [`Runner`](../../src/rtagent/runtime.py#L43) | Lifecycle-owned single graph worker. | Concrete graph/checkpointer/resources, per-Run asyncio tasks, semaphore, and shutdown flag. | All scheduling, budget enforcement, cleanup, and effect evidence are visible in one bounded implementation. |

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
def Runner.__init__(self, repository: Repository, registry: ToolRegistry, checkpoint_path: str)
async def Runner.open(self) -> None
def Runner.start(self, run_id: str, resume: bool=False) -> None
async def Runner.close(self) -> None
async def Runner.drive(self, run_id: str, resume: bool) -> None
async def Runner.mark_unknown(self, run_id: str) -> None
async def Runner.cancel(self, run_id: str) -> None
async def Runner.model_node(self, state: AgentState) -> dict[str, Any]
async def Runner.model_node.on_wire(body)
async def Runner.tool_node(self, state: AgentState) -> dict[str, Any]
```

These are signature listings; qualified names identify ownership and are not executable declarations. Methods include `self`; validators include `cls`.

</details>
