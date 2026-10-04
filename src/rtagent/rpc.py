"""Bounded newline JSON-RPC 2.0 forwarding to a fixed local-service method table."""

import asyncio
import json
import re
import sys
from typing import Any, Literal
from urllib.parse import quote

from pydantic import Field, StrictInt, StrictStr, ValidationError

from rtagent.client import AgentClient
from rtagent.domain import AppError, Contract, PreviewRequest

LINE_LIMIT = 512 * 1024
METHODS = {
    "bootstrap": ("GET", "/bootstrap"),
    "run.get": ("GET", "/runs/{run_id}"),
    "run.cancel": ("POST", "/runs/{run_id}/cancel"),
    "run.approve": ("POST", "/runs/{run_id}/approvals/{approval_id}"),
    "run.resume": ("POST", "/runs/{run_id}/resume"),
    "session.create": ("POST", "/sessions"),
    "session.get": ("GET", "/sessions/{session_id}"),
    "session.copy": ("POST", "/sessions/{session_id}/copy"),
    "session.update": ("PATCH", "/sessions/{session_id}"),
    "session.delete": ("DELETE", "/sessions/{session_id}"),
    "context.get": ("GET", "/sessions/{session_id}/context"),
    "context.compact": ("POST", "/sessions/{session_id}/compact"),
    "context.restore": ("PUT", "/sessions/{session_id}/context"),
    "process.list": ("GET", "/sessions/{session_id}/processes"),
    "process.input": ("POST", "/sessions/{session_id}/processes/{process_id}/input"),
    "process.stop": ("POST", "/sessions/{session_id}/processes/{process_id}/stop"),
    "preset.export": ("GET", "/presets/{preset_id}/export"),
    "preset.import": ("POST", "/presets/import"),
    "skill.export": ("GET", "/workspace/skills/{name}/export"),
    "skill.import": ("POST", "/workspace/skills/import"),
}


class Submission(Contract):
    """Convenience Run input; revisions are read once by the HTTP client."""

    task: str = Field(min_length=1, max_length=100000)
    session_id: str | None = None
    project_id: str | None = None
    preset_id: str = "general-agent"
    variables: dict[str, Any] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list, max_length=20)
    key: str | None = None


class RPCRequest(Contract):
    """Require a request ID so each input line has one observable response."""

    jsonrpc: Literal["2.0"]
    id: StrictStr | StrictInt
    method: StrictStr = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


async def dispatch(client: AgentClient, method: str, params: dict[str, Any]) -> Any:
    """Forward fixed operations without exposing tool execution or arbitrary transport URLs."""
    if method == "rpc.schema":
        if params:
            raise AppError("rpc_params", "rpc.schema takes no parameters.")
        return {
            "protocol": "2.0",
            "version": 1,
            "methods": sorted(
                [*METHODS, "rpc.schema", "run.submit", "run.start", "run.wait", "events.list"]
            ),
            "contracts": "/openapi.json",
            "line_limit": LINE_LIMIT,
        }
    if method == "run.submit":
        return (await client.run(**Submission.model_validate(params).model_dump())).model_dump()
    if method == "run.start":
        if set(params) != {"request", "key"} or not isinstance(params["key"], str):
            raise AppError("rpc_params", "run.start requires request and key.")
        return (
            await client.start(PreviewRequest.model_validate(params["request"]), params["key"])
        ).model_dump()
    if method in {"run.wait", "events.list"}:
        allowed = {"run_id", "timeout" if method == "run.wait" else "after"}
        if set(params) - allowed or not isinstance(params.get("run_id"), str):
            raise AppError("rpc_params", "Supply run_id and an optional timeout/after cursor.")
        if method == "run.wait":
            timeout = params.get("timeout", 600)
            if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
                raise AppError("rpc_params", "timeout must be between zero and 3600 seconds.")
            return (await client.wait(params["run_id"], timeout)).model_dump()
        after = params.get("after", 0)
        if type(after) is not int or after < 0:
            raise AppError("rpc_params", "after must be a nonnegative integer.")
        return await client.events(params["run_id"], after)
    if method not in METHODS:
        raise AppError("rpc_method", "Method not found.", 404)
    verb, template = METHODS[method]
    fields = re.findall(r"\{(\w+)\}", template)
    allowed = {*fields}
    if verb in {"POST", "PUT", "PATCH"}:
        allowed.add("body")
    if method.startswith("skill."):
        allowed.add("project_id")
    if set(params) - allowed:
        raise AppError("rpc_params", "Supply path identifiers and optional body/project_id.")
    if any(not isinstance(params.get(field), str) or not params[field] for field in fields):
        raise AppError("rpc_params", "Required path identifiers must be nonempty strings.")
    path = template.format(**{field: quote(params[field], safe="") for field in fields})
    if "project_id" in params:
        if not method.startswith("skill.") or not isinstance(params["project_id"], str):
            raise AppError("rpc_params", "project_id is only supported for Skill packages.")
        path += "?project_id=" + quote(params["project_id"], safe="")
    return await client.request(verb, path, params.get("body"))


async def serve_rpc(client: AgentClient) -> None:
    """Read bounded sequential requests; stdout contains only JSON and disconnect leaves Runs active."""
    while line := await asyncio.to_thread(sys.stdin.buffer.readline, LINE_LIMIT + 1):
        identifier = None
        phase = "parse"
        try:
            if len(line) > LINE_LIMIT:
                raise AppError("rpc_size", "Request line exceeds 512 KiB.")
            decoded = json.loads(line)
            phase = "request"
            if isinstance(decoded, dict) and type(decoded.get("id")) in (str, int):
                identifier = decoded["id"]
            value = RPCRequest.model_validate(decoded)
            identifier = value.id
            phase = "params"
            response = {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": await dispatch(client, value.method, value.params),
            }
        except (AppError, ValidationError, ValueError) as exc:
            if isinstance(exc, AppError):
                code = (
                    -32601
                    if exc.code == "rpc_method"
                    else (
                        -32600
                        if exc.code == "rpc_size"
                        else -32602 if exc.code == "rpc_params" or exc.status == 422 else -32000
                    )
                )
            else:
                code = -32700 if phase == "parse" else -32600 if phase == "request" else -32602
            response = {
                "jsonrpc": "2.0",
                "id": identifier,
                "error": {
                    "code": code,
                    "message": (
                        exc.message
                        if isinstance(exc, AppError)
                        else (
                            "Parse error."
                            if phase == "parse"
                            else "Invalid request." if phase == "request" else "Invalid parameters."
                        )
                    ),
                    "data": (
                        {"code": exc.code, "status": exc.status}
                        if isinstance(exc, AppError)
                        else None
                    ),
                },
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if len(line) > LINE_LIMIT:
            return
