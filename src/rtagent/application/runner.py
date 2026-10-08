"""An explicit, bounded LangGraph loop with durable call receipts."""

import asyncio
import contextvars
import json
import time
from collections.abc import Callable
from contextlib import AsyncExitStack, nullcontext
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from langsmith import tracing_context

from rtagent.domain import (
    AppError,
    Attachment,
    Grant,
    Message,
    Preset,
    RunEvent,
    StudioConfig,
    ToolCall,
    ToolDefinition,
    call_cost,
    digest,
    now,
)
from rtagent.domain.execution import ToolExecution, current_execution
from rtagent.domain.hooks import Hooks, matching_hooks
from rtagent.domain.lifecycle import TERMINAL
from rtagent.domain.policies import decide, granted
from rtagent.domain.prompts import compile_prompt
from rtagent.infrastructure.attachments import AttachmentStore
from rtagent.infrastructure.providers.attachments import attachment_data
from rtagent.infrastructure.providers.chat import generate, prepare, transformations
from rtagent.infrastructure.shell_hooks import OUTPUT_LIMIT, run_hook
from rtagent.infrastructure.storage import Repository
from rtagent.infrastructure.tools import ToolRegistry
from rtagent.infrastructure.tracing import redact

DELEGATE = "delegate"
# Appended to a delegated task, so the sub-agent knows its reply is a result for another agent.
SUBAGENT_NOTE = (
    "\n\nYou are working as a sub-agent: you cannot ask the user anything or wait for "
    "approval. Finish the task and give a complete final answer; it is returned to the agent "
    "that delegated the task."
)
# Events that mark a Run's end, once each (finish is idempotent).
RUN_END_EVENTS = frozenset({"run.completed", "run.failed", "run.cancelled"})
# Each conversation has at most one active Run; this bounds concurrent provider and tool load
# across conversations. Further Runs stay queued until a slot is free.
MAX_ACTIVE_RUNS = 4


class AgentState(TypedDict):
    """Checkpoint only the current Run delta and its next model/tool positions."""

    run_id: str
    messages: list[dict[str, Any]]
    step: int
    pending: list[dict[str, Any]]
    tool_index: int


class Runner:
    """Own one bounded LangGraph worker and durable per-call execution receipts."""

    def __init__(
        self,
        repository: Repository,
        registry: ToolRegistry,
        checkpoint_path: str,
        hooks: Hooks | None = None,
    ) -> None:
        """Prepare lifecycle resources without opening files or starting background tasks."""
        self.repository, self.registry = repository, registry
        self.hooks = hooks or Hooks()
        repository.on_event = self.observe
        self.hook_tasks: set[asyncio.Task[None]] = set()
        registry.bind_repository(repository)
        self.checkpoint_path = checkpoint_path
        self.stack = AsyncExitStack()
        self.tasks: dict[str, asyncio.Task] = {}
        self.semaphore = asyncio.Semaphore(MAX_ACTIVE_RUNS)
        self.shutting_down = False
        # Told the Run ID after a Run succeeds and its messages are committed. It must only
        # schedule work: cancelling a Run waits for this task, so it cannot wait for locks.
        self.on_committed: Callable[[str], None] | None = None
        self.attachments = AttachmentStore(registry.files.data_dir / "attachments")

    async def observe(self, event: RunEvent) -> None:
        """Start `run_end` shell hooks when a Run ends, then call the trusted event observer."""
        if event.type in RUN_END_EVENTS:
            task = asyncio.create_task(self.run_end_hooks(event.run_id))
            self.hook_tasks.add(task)
            task.add_done_callback(self.hook_tasks.discard)
        if self.hooks.on_event:
            await self.hooks.on_event(event)

    @staticmethod
    def hook_payload(run: dict[str, Any], event: str) -> dict[str, Any]:
        """The fields every hook receives on stdin."""
        return {
            "event": event,
            "run_id": run["id"],
            "session_id": run["session_id"],
            "workspace_root": run["snapshot"]["workspace_root"],
        }

    async def before_tool_hooks(
        self, run: dict[str, Any], config: StudioConfig, call: ToolCall, data: dict[str, Any]
    ) -> None:
        """Run `before_tool` hooks in order; any outcome except exit code 0 blocks the call."""
        for index, hook in matching_hooks(config.hooks, "before_tool", call.name):
            payload = {
                **self.hook_payload(run, "before_tool"),
                "tool": {"name": call.name, "args": call.args},
            }
            outcome = await run_hook(hook, payload, Path(run["snapshot"]["workspace_root"]))
            data.setdefault("hooks", []).append(outcome.record(index, hook))
            if outcome.succeeded:
                continue
            if outcome.exit_code == 2:
                data["hook_rejection"] = (outcome.stderr.strip() or "Blocked by a hook.")[:500]
                raise AppError("tool_denied", data["hook_rejection"], 403)
            reason = outcome.error or f"exit code {outcome.exit_code}"
            raise AppError(
                "hook_failed", f"A before-tool hook failed ({reason}); no tool was dispatched."
            )

    async def after_tool_hooks(
        self,
        run: dict[str, Any],
        config: StudioConfig,
        call: ToolCall,
        data: dict[str, Any],
        status: str,
        content: str,
    ) -> None:
        """Run `after_tool` hooks for a dispatched call; their outcome never changes the Run."""
        for index, hook in matching_hooks(config.hooks, "after_tool", call.name):
            payload = {
                **self.hook_payload(run, "after_tool"),
                "tool": {"name": call.name, "args": call.args},
                "status": status,
                "result": content[:OUTPUT_LIMIT],
            }
            outcome = await run_hook(hook, payload, Path(run["snapshot"]["workspace_root"]))
            data.setdefault("hooks", []).append(outcome.record(index, hook))

    async def run_end_hooks(self, run_id: str) -> None:
        """Run `run_end` hooks once a Run ends, recording each outcome as a Run event."""
        run = await self.repository.run(run_id)
        config = StudioConfig.model_validate(run["snapshot"]["config"])
        for index, hook in matching_hooks(config.hooks, "run_end"):
            payload = {
                **self.hook_payload(run, "run_end"),
                "status": run["status"],
                "stop_reason": run["stop_reason"],
                "error": run["error"],
                "input": run["snapshot"]["input"][:4000],
            }
            outcome = await run_hook(hook, payload, Path(run["snapshot"]["workspace_root"]))
            await self.repository.emit(
                run_id,
                "hook.ran" if outcome.succeeded else "hook.failed",
                redact(outcome.record(index, hook)),
            )

    async def open(self) -> None:
        """Open checkpoints, build the explicit loop, and recover queued work."""
        self.shutting_down = False
        self.checkpointer = await self.stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(self.checkpoint_path)
        )
        await self.checkpointer.setup()
        builder = StateGraph(AgentState)
        builder.add_node("model", self.model_node)
        builder.add_node("tools", self.tool_node)
        builder.add_edge(START, "model")
        builder.add_conditional_edges("model", lambda state: "tools" if state["pending"] else END)
        builder.add_conditional_edges(
            "tools",
            lambda state: "tools" if state["tool_index"] < len(state["pending"]) else "model",
        )
        self.graph = builder.compile(checkpointer=self.checkpointer)
        for run_id in await self.repository.recover():
            # A queued Run may already contain an approved, interrupted checkpoint.
            self.start(run_id, resume=True)

    def start(self, run_id: str, resume: bool = False) -> None:
        """Schedule at most one active task for a Run inside this process."""
        if run_id in self.tasks and not self.tasks[run_id].done():
            return
        task = asyncio.create_task(self.drive(run_id, resume))
        self.tasks[run_id] = task
        task.add_done_callback(
            lambda completed: (
                self.tasks.pop(run_id, None) if self.tasks.get(run_id) is completed else None
            )
        )

    async def close(self) -> None:
        """Interrupt active work, preserve uncertain outcomes, and release checkpoints."""
        self.shutting_down = True
        try:
            pending = list(self.tasks.values()) + list(self.hook_tasks)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await self.registry.processes.close()
        finally:
            await self.stack.aclose()

    async def drive(self, run_id: str, resume: bool, nested: bool = False) -> None:
        """Execute or resume a graph under the Run budget and persist its stop state.

        A `nested` Run is a sub-agent executing inside its parent's tool call: it uses the
        parent's worker slot, and a cancellation is recorded and then passed on to the parent."""
        started = None
        try:
            async with nullcontext() if nested else self.semaphore:
                run = await self.repository.run(run_id)
                if run["status"] in TERMINAL:
                    return
                config = StudioConfig.model_validate(run["snapshot"]["config"])
                if not await asyncio.to_thread(Path(run["snapshot"]["workspace_root"]).is_dir):
                    raise AppError(
                        "workspace_changed",
                        "Restore the original workspace before resuming this Run.",
                    )
                graph_config = {
                    "configurable": {"thread_id": run_id},
                    "recursion_limit": config.max_steps + config.max_tool_calls + 10,
                }
                state_input: Any = {
                    "run_id": run_id,
                    "messages": [],
                    "step": 0,
                    "pending": [],
                    "tool_index": 0,
                }
                if resume:
                    checkpoint = await self.graph.aget_state(graph_config)
                    if checkpoint.tasks and any(task.interrupts for task in checkpoint.tasks):
                        state_input = Command(resume=True)
                    else:
                        state_input = None if checkpoint.values else state_input
                await self.repository.emit(run_id, "run.started", {}, status="running", error=None)
                remaining = config.run_timeout - run["usage"].get("active_seconds", 0)
                if remaining <= 0:
                    raise AppError("timeout", "Run active execution budget exceeded.")
                started = time.monotonic()
                with tracing_context(enabled=False):
                    async with asyncio.timeout(remaining):
                        result = await self.graph.ainvoke(
                            state_input, graph_config, durability="sync"
                        )
                if not result.get("__interrupt__"):
                    await self.repository.finish(
                        run_id, "succeeded", result["messages"], "completed"
                    )
                    if self.on_committed:
                        self.on_committed(run_id)
        except asyncio.CancelledError:
            run = await self.repository.run(run_id)
            await self.mark_unknown(run_id)
            if self.shutting_down:
                if run["status"] not in TERMINAL and run["status"] != "queued":
                    await self.repository.emit(
                        run_id, "run.interrupted", {"reason": "shutdown"}, status="interrupted"
                    )
            else:
                await self.repository.finish(
                    run_id, "cancelled", run["messages"], "cancelled_by_user"
                )
            if nested:
                raise
        except Exception as exc:
            run = await self.repository.run(run_id)
            await self.mark_unknown(run_id)
            uncertain = bool(await self.repository.calls_with_status(run_id, ["outcome_unknown"]))
            if uncertain:
                await self.repository.emit(
                    run_id,
                    "run.interrupted",
                    {"reason": "outcome_unknown"},
                    status="interrupted",
                    error="An interrupted call has an unknown outcome. Review the call before resuming.",
                )
            else:
                reason = (
                    exc.code
                    if isinstance(exc, AppError)
                    else "timeout" if isinstance(exc, TimeoutError) else "execution_error"
                )
                message = (
                    exc.message
                    if isinstance(exc, AppError)
                    else f"Execution stopped ({type(exc).__name__})."
                )
                await self.repository.finish(run_id, "failed", run["messages"], reason, message)
        finally:
            if started is not None:
                run = await self.repository.run(run_id)
                if run["status"] in {"failed", "cancelled", "interrupted"}:
                    await self.registry.processes.stop_run(run_id)
                usage = {
                    **run["usage"],
                    "active_seconds": run["usage"].get("active_seconds", 0)
                    + time.monotonic()
                    - started,
                }
                await self.repository.emit(run_id, "run.metrics", {}, usage=usage)

    async def delegate(
        self,
        run: dict[str, Any],
        config: StudioConfig,
        call: ToolCall,
        call_id: str,
        data: dict[str, Any],
    ) -> str:
        """Run a self-contained task as a sub-agent Run inside this tool call.

        The child gets the parent's frozen preset, model, workspace and context. It cannot
        pause for approval (tools that would ask are denied) or delegate again."""
        snapshot = run["snapshot"]
        policy = {
            name: "deny" if permission == "ask" else permission
            for name, permission in config.tools.items()
            if name != DELEGATE
        }
        child_config = config.model_copy(
            update={
                "tools": policy,
                "command_rules": [
                    (
                        rule.model_copy(update={"permission": "deny"})
                        if rule.permission == "ask"
                        else rule
                    )
                    for rule in config.command_rules
                ],
            }
        )
        tools = [
            item
            for item in snapshot["tools"]
            if item["name"] != DELEGATE and policy.get(item["name"], "deny") != "deny"
        ]
        task = call.args["task"] + SUBAGENT_NOTE
        context = [Message.model_validate(item) for item in snapshot.get("workspace_context", [])]
        compiled = compile_prompt(
            Preset.model_validate(snapshot["preset"]),
            child_config,
            [],
            task,
            snapshot["variables"],
            tools,
            workspace_context=context,
        )
        child_snapshot = {
            "schema_version": snapshot.get("schema_version", 1),
            "preset": snapshot["preset"],
            "config": child_config.model_dump(),
            "history": [],
            "context_compaction_id": None,
            "input": task,
            "command": None,
            "attachments": [],
            "variables": snapshot["variables"],
            "tools": tools,
            "session_revision": 0,
            "workspace_root": snapshot["workspace_root"],
            "project_id": snapshot.get("project_id"),
            "workspace_context": snapshot.get("workspace_context", []),
            "prepared_request": prepare(child_config.provider, compiled, tools),
            "access": snapshot.get("access"),
            "parent_run_id": run["id"],
            "parent_call_id": call_id,
        }
        child = await self.repository.create_child_run(
            run["session_id"], f"subagent:{call_id}", child_snapshot
        )
        # Record the link before the child works, so the parent's receipt always names it.
        data["child_run_id"] = child["id"]
        await self.repository.save_call(call_id, run["id"], "tool", "executing", data)
        await self.repository.emit(run["id"], "tool.output", {"call_id": call_id})
        if child["status"] not in TERMINAL:
            # A fresh context keeps the parent's graph state (and execution identity) out of
            # the child's graph. Cancelling this call cancels the awaited child task too.
            await asyncio.get_running_loop().create_task(
                self.drive(child["id"], resume=child["status"] != "queued", nested=True),
                context=contextvars.Context(),
            )
        finished = await self.repository.run(child["id"])
        answer = next(
            (
                message["content"]
                for message in reversed(finished["messages"])
                if message["role"] == "assistant" and message["content"]
            ),
            "",
        )
        return json.dumps(
            {
                "run_id": child["id"],
                "status": finished["status"],
                "answer": answer[:48_000],
                "answer_truncated": len(answer) > 48_000,
                "error": finished["error"],
                "usage": {
                    key: finished["usage"].get(key)
                    for key in ("input_tokens", "output_tokens", "cost")
                },
            },
            ensure_ascii=False,
        )

    async def mark_unknown(self, run_id: str) -> None:
        """Preserve the uncertainty of calls interrupted before a durable receipt."""
        for call in await self.repository.calls_with_status(run_id, ["executing"]):
            await self.repository.save_call(
                call["id"], run_id, call["kind"], "outcome_unknown", call["data"], call["result"]
            )

    async def cancel(self, run_id: str) -> None:
        """Stop local execution without claiming that external effects were rolled back."""
        run = await self.repository.run(run_id)
        await self.registry.processes.stop_run(run_id)
        if run["status"] in TERMINAL:
            return
        task = self.tasks.get(run_id)
        if task and not task.done():
            await self.repository.emit(run_id, "run.cancelling", {}, status="cancelling")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # Cancellation may arrive in drive's final metrics write or before it starts.
        # Settle the command here as well; finish is an idempotent transaction.
        await self.mark_unknown(run_id)
        current = await self.repository.run(run_id)
        await self.repository.finish(run_id, "cancelled", current["messages"], "cancelled_by_user")

    async def model_node(self, state: AgentState) -> dict[str, Any]:
        """Reuse a durable response or invoke the model with the frozen compiler input."""
        run_id, step = state["run_id"], state["step"]
        run = await self.repository.run(run_id)
        snapshot = run["snapshot"]
        config = StudioConfig.model_validate(snapshot["config"])
        call_id = f"{run_id}:model:{step:04d}"
        existing = await self.repository.call(call_id)
        if existing and existing["status"] == "outcome_unknown":
            raise AppError(
                "outcome_unknown",
                "The previous model request has an unknown outcome. Review it before retrying.",
            )
        if existing and existing["status"] == "succeeded":
            response = existing["result"]
        else:
            if step >= config.max_steps:
                raise AppError("max_steps", "The Run reached its model call limit.")
            compiled = compile_prompt(
                Preset.model_validate(snapshot["preset"]),
                config,
                [Message.model_validate(item) for item in snapshot["history"]],
                snapshot["input"],
                snapshot["variables"],
                snapshot["tools"],
                [Message.model_validate(item) for item in state["messages"]],
                workspace_context=[
                    Message.model_validate(item) for item in snapshot.get("workspace_context", [])
                ],
                attachments=[
                    Attachment.model_validate(item) for item in snapshot.get("attachments", [])
                ],
            )
            prepared = prepare(config.provider, compiled, snapshot["tools"])
            if step == 0 and prepared != snapshot["prepared_request"]:
                raise AppError(
                    "request_mismatch", "The first request no longer matches the preview."
                )
            data = {
                "step": step,
                "started_at": now(),
                "canonical_messages": [item.model_dump() for item in compiled.messages],
                "source_map": compiled.source_map,
                "prepared_request": prepared,
                "wire_request_body": None,
                "simulated": config.provider.adapter == "fake",
                "transformations": transformations(config.provider.adapter),
                "attempts": (existing or {}).get("data", {}).get("attempts", 0) + 1,
                "attempt_history": (existing or {}).get("data", {}).get("attempt_history", []),
            }
            attempts = sum(
                call["attempts"] for call in await self.repository.call_summaries(run_id, "model")
            )
            if attempts >= config.max_steps:
                raise AppError("max_steps", "The Run reached its model attempt limit.")
            await self.repository.save_call(call_id, run_id, "model", "executing", data)
            await self.repository.emit(
                run_id, "model.started", {"call_id": call_id}, partial_output=""
            )

            async def on_wire(body):
                data["wire_request_body"] = body
                await self.repository.save_call(call_id, run_id, "model", "executing", data)

            started = time.monotonic()
            response = None
            key = self.registry.credentials.resolve(config.provider.credential_ref)
            images = {
                item.hash: item for message in compiled.messages for item in message.attachments
            }
            # Image data enters only the outgoing request; records keep the references.
            token = attachment_data.set(
                await asyncio.to_thread(self.attachments.encoded, list(images.values()))
                if images
                else None
            )
            try:
                async for event in generate(
                    config.provider, compiled, snapshot["tools"], prepared, on_wire, key
                ):
                    if event["kind"] == "delta":
                        await self.repository.delta(run_id, call_id, event["text"])
                    elif event["kind"] == "result":
                        response = event["message"].model_dump()
                        data["usage"] = event["usage"]
                        data["cost"] = call_cost(
                            event["usage"], config.provider.price, config.provider.adapter
                        )
                        data["response_metadata"] = event.get("metadata", {})
            finally:
                attachment_data.reset(token)
            if response is None:
                raise AppError("provider_protocol", "No complete response was received.")
            data["latency_ms"] = round((time.monotonic() - started) * 1000)
            await self.repository.save_call(call_id, run_id, "model", "succeeded", data, response)
        messages = state["messages"] + [response]
        summaries = await self.repository.call_summaries(run_id, "model")
        usage = dict(run["usage"])
        for key in ("input_tokens", "output_tokens", "cost"):
            values = [call[key] for call in summaries]
            # Retried calls may have consumed unreported tokens, so their total is unknown.
            usage[key] = (
                round(sum(values), 6)
                if values
                and all(value is not None for value in values)
                and not any(call["retries"] for call in summaries)
                else None
            )
        await self.repository.emit(
            run_id,
            "model.completed",
            {"call_id": call_id},
            messages=messages,
            partial_output="",
            usage=usage,
        )
        return {
            "messages": messages,
            "step": step + 1,
            "pending": response["tool_calls"],
            "tool_index": 0,
        }

    async def tool_node(self, state: AgentState) -> dict[str, Any]:
        """Authorize one call, pause for human approval, and persist its paired result.

        Reentry uses the call ledger; an uncertain external effect requires an explicit
        recovery decision before any retry can occur."""
        run_id = state["run_id"]
        run = await self.repository.run(run_id)
        config = StudioConfig.model_validate(run["snapshot"]["config"])
        call = ToolCall.model_validate(state["pending"][state["tool_index"]])
        call_id = f"{run_id}:tool:{state['step'] - 1:04d}:{state['tool_index']:04d}"
        existing = await self.repository.call(call_id)
        definitions = [ToolDefinition.model_validate(item) for item in run["snapshot"]["tools"]]
        definition = next((item for item in definitions if item.name == call.name), None)
        if existing and existing["status"] == "outcome_unknown":
            raise AppError(
                "outcome_unknown",
                "A tool's previous outcome is unknown. Review it before retrying.",
            )
        if existing and existing["status"] in {"succeeded", "failed"}:
            content = existing["result"]
        else:
            attempts = sum(
                item["attempts"] for item in await self.repository.call_summaries(run_id, "tool")
            )
            if attempts >= config.max_tool_calls:
                raise AppError("max_tool_calls", "The Run reached its tool attempt limit.")
            data = {
                "tool_call_id": call.id,
                "name": call.name,
                "args": call.args,
                "args_hash": digest(call.args),
                "started_at": now(),
                "attempts": (existing or {}).get("data", {}).get("attempts", 0) + 1,
                "attempt_history": (existing or {}).get("data", {}).get("attempt_history", []),
            }
            status = "succeeded"
            started = time.monotonic()
            dispatched = False
            try:
                decision = decide(call, definition, config)
                data["decision"] = decision
                if decision == "ask":
                    # A standing approval the user gave in this conversation answers the prompt;
                    # the receipt names it so the authorization stays inspectable.
                    session = await self.repository.session(run["session_id"])
                    grant = granted(
                        call, [Grant.model_validate(item) for item in session.get("grants", [])]
                    )
                    if grant:
                        decision = "allow"
                        data.update(decision=decision, granted_by=grant.model_dump())
                if decision == "ask":
                    approval = run["approval"]
                    if not approval or approval["id"] != call_id:
                        approval = {
                            "id": call_id,
                            "tool_call_id": call.id,
                            "name": call.name,
                            "args": call.args,
                            "args_hash": digest(call.args),
                            "decision": None,
                        }
                        await self.repository.emit(
                            run_id,
                            "approval.required",
                            approval,
                            status="waiting_approval",
                            approval=approval,
                        )
                    # A decision can be durable before the interrupt checkpoint is.
                    # Reentry must honor it without pausing a second time.
                    if approval["decision"] is None:
                        interrupt({"approval_id": call_id})
                        approval = (await self.repository.run(run_id))["approval"]
                    decision = (
                        "allow"
                        if approval
                        and approval["id"] == call_id
                        and approval["decision"] == "approve"
                        else "deny"
                    )
                    data["decision"] = decision
                if decision != "allow":
                    raise AppError("tool_denied", "Tool execution was not authorized.", 403)
                await self.before_tool_hooks(run, config, call, data)
                if self.hooks.before_tool:
                    try:
                        async with asyncio.timeout(5):
                            rejection = await self.hooks.before_tool(
                                call.model_copy(deep=True), config.model_copy(deep=True)
                            )
                    except Exception as exc:
                        raise AppError(
                            "hook_failed",
                            f"Tool guard failed ({type(exc).__name__}); no tool was dispatched.",
                        ) from None
                    if rejection:
                        data["hook_rejection"] = str(rejection)[:500]
                        raise AppError("tool_denied", data["hook_rejection"], 403)
                await self.repository.save_call(call_id, run_id, "tool", "executing", data)
                await self.repository.emit(
                    run_id, "tool.started", {"call_id": call_id, "name": call.name}, approval=None
                )
                # Bash owns its command deadline; allow its process-tree cleanup to finish. A
                # sub-agent has its own Run budget, enforced inside its Run.
                deadline = (
                    config.tool_timeout + 35
                    if call.name in {"bash", "powershell"}
                    else config.run_timeout + 60 if call.name == DELEGATE else config.tool_timeout
                )
                async with asyncio.timeout(deadline):
                    # Session/project changes never redirect tools from the frozen Run root.
                    registry = self.registry.for_workspace(Path(run["snapshot"]["workspace_root"]))

                    async def on_output(frame: dict[str, Any]) -> None:
                        """Persist the latest bounded output snapshot before the final receipt."""
                        output = data.setdefault(
                            "output", {"stdout": "", "stderr": "", "truncated": False}
                        )
                        output[frame["stream"]] = frame["text"]
                        output["truncated"] |= frame["truncated"]
                        await self.repository.save_call(call_id, run_id, "tool", "executing", data)
                        # The text lives in the call receipt; events only signal progress.
                        await self.repository.emit(
                            run_id,
                            "tool.output",
                            {"call_id": call_id, "stream": frame["stream"]},
                        )

                    execution = ToolExecution(run_id, run["session_id"], on_output)
                    token = current_execution.set(execution)
                    dispatched = True
                    try:
                        if call.name == DELEGATE:
                            content = await self.delegate(run, config, call, call_id, data)
                        else:
                            content = await registry.execute(definition, call.args, config)
                    finally:
                        current_execution.reset(token)
                        # Kept even when the call fails, e.g. a patch that stopped part way.
                        if execution.changes:
                            data["file_changes"] = execution.changes
            except GraphInterrupt:
                raise
            except AppError as exc:
                if exc.code == "outcome_unknown":
                    await self.repository.save_call(
                        call_id, run_id, "tool", "outcome_unknown", data
                    )
                    raise
                if exc.code == "tool_schema_changed":
                    await self.repository.save_call(
                        call_id,
                        run_id,
                        "tool",
                        "failed",
                        data,
                        json.dumps({"error": {"code": exc.code, "message": exc.message}}),
                    )
                    raise
                content, status = (
                    json.dumps(
                        {
                            "error": {
                                "code": exc.code,
                                "message": exc.message,
                                **({"details": exc.details} if exc.details is not None else {}),
                            }
                        },
                        ensure_ascii=False,
                    ),
                    "failed",
                )
            except TimeoutError:
                # External completion is not known after a timeout. Preserve that fact.
                await self.repository.save_call(call_id, run_id, "tool", "outcome_unknown", data)
                raise AppError(
                    "outcome_unknown", "Tool timed out; its external outcome is unknown."
                ) from None
            except Exception as exc:
                if definition and (definition.server_id or call.name in self.registry.local_tools):
                    await self.repository.save_call(
                        call_id, run_id, "tool", "outcome_unknown", data
                    )
                    raise AppError(
                        "outcome_unknown",
                        "Integration failed during execution; its effect outcome is unknown.",
                    ) from None
                content, status = (
                    json.dumps(
                        {
                            "error": {
                                "code": "tool_error",
                                "message": f"Tool failed ({type(exc).__name__}).",
                            }
                        }
                    ),
                    "failed",
                )
            data["latency_ms"] = round((time.monotonic() - started) * 1000)
            await self.repository.save_call(call_id, run_id, "tool", status, data, content)
            if dispatched and matching_hooks(config.hooks, "after_tool", call.name):
                await self.after_tool_hooks(run, config, call, data, status, content)
                await self.repository.save_call(call_id, run_id, "tool", status, data, content)
        message = Message(role="tool", content=content, tool_call_id=call.id).model_dump()
        messages = state["messages"] + [message]
        await self.repository.emit(
            run_id,
            "tool.completed",
            {"call_id": call_id, "tool_call_id": call.id},
            messages=messages,
            approval=None,
        )
        return {"messages": messages, "tool_index": state["tool_index"] + 1}
