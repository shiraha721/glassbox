"""Bounded local tools and stateless MCP operations, without a plugin framework."""

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator, SchemaError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError

from rtagent.domain import AppError, MCPServer, StudioConfig, TaskPlan, ToolDefinition, digest
from rtagent.domain.execution import current_execution
from rtagent.infrastructure.config import resolve_secret
from rtagent.infrastructure.sandbox.linux import BashSandbox
from rtagent.infrastructure.sandbox.processes import ProcessManager, process_view
from rtagent.infrastructure.storage import Repository
from rtagent.infrastructure.workspace.downloads import fetch_url
from rtagent.infrastructure.workspace.files import MEMORY_PATH, Workspace

WRITE_TOOLS = {"write_text", "edit_text", "apply_patch", "save_memory", "save_skill", "fetch_url"}
ToolHandler = Callable[[dict, Workspace], Awaitable[str]]


def native_tools() -> list[ToolDefinition]:
    """Describe the concrete workspace tools without a generic extension framework."""

    def tool(name: str, description: str, properties: dict, required: list[str]) -> ToolDefinition:
        return ToolDefinition(
            name=name,
            description=description,
            writes_workspace=name in WRITE_TOOLS,
            input_schema={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        )

    string = {"type": "string"}
    content = {"type": "string", "maxLength": 65536}
    return [
        tool(
            "list_files",
            "List one workspace directory, excluding credentials and runtime data.",
            {"path": string},
            [],
        ),
        tool(
            "search_files",
            "Find files recursively by relative glob (e.g. **/*.py). Excludes credentials, runtime data, links, Git metadata, and dependencies. Results report truncation.",
            {
                "pattern": {"type": "string", "minLength": 1, "maxLength": 200},
                "path": string,
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            [],
        ),
        tool(
            "search_text",
            "Search literal UTF-8 text recursively with relative file paths and 1-based line numbers. Not a regex tool. Narrow path/pattern when results are truncated; binary/oversized files are counted as skipped.",
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "path": string,
                "pattern": {"type": "string", "minLength": 1, "maxLength": 200},
                "case_sensitive": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            ["query"],
        ),
        tool(
            "read_instructions",
            "Read applicable AGENTS.override.md or AGENTS.md from the workspace root down to a file's parent or existing directory. Call before editing unfamiliar subdirectories. Later documents specialize earlier project guidance; none grant tool permissions.",
            {"path": string},
            [],
        ),
        tool(
            "write_text",
            "Create a UTF-8 file. To replace an existing file, pass expected_hash from read_text.",
            {"path": string, "content": content, "expected_hash": string},
            ["path", "content"],
        ),
        tool(
            "edit_text",
            "Replace exactly one occurrence of old_text in a workspace file; ambiguous matches fail.",
            {"path": string, "old_text": content, "new_text": content},
            ["path", "old_text", "new_text"],
        ),
        tool(
            "apply_patch",
            "Apply an exact multi-file patch: *** Begin Patch, *** Add File: path (every line prefixed +), *** Update File: path (hunks start @@ with space/+/- lines), *** Delete File: path, *** End Patch. For each update/delete, expected_hashes[path] must be the full-file hash from read_text. Context must match exactly once; no fuzzy edits or Move headers. All operations preflight before per-file atomic writes; multi-file changes are not a transaction. Returns diffs and explicit partial-failure evidence.",
            {
                "patch": {"type": "string", "minLength": 1, "maxLength": 65_536},
                "expected_hashes": {
                    "type": "object",
                    "maxProperties": 20,
                    "additionalProperties": {"type": "string"},
                },
            },
            ["patch"],
        ),
        ToolDefinition(
            name="update_plan",
            description="Record a concise plan for substantial multi-step work, and update it when progress changes. At most one step is in_progress. Skip plans for trivial tasks. Plan status is the agent's progress statement, not proof that a test passed or permission to run a tool.",
            input_schema=TaskPlan.model_json_schema(),
        ),
        tool(
            "powershell" if os.name == "nt" else "bash",
            (
                "Run Windows PowerShell in a native Windows AppContainer. Use PowerShell syntax, "
                "relative paths or Workspace:\\; $env:RTAGENT_WORKSPACE is the native directory. "
                "Changes are copied back after the command stops. "
                if os.name == "nt"
                else "Run Bash inside the Linux workspace sandbox at /workspace. "
            )
            + "Network access is disabled. Credentials, runtime data, Git metadata, and dependency folders are excluded. Returns exit code, stdout, stderr, and timeout status.",
            {
                "command": {"type": "string", "minLength": 1, "maxLength": 16000},
                "cwd": string,
                "timeout": {"type": "number", "minimum": 0.1, "maximum": 300},
            },
            ["command"],
        ),
        tool(
            "start_process",
            "Start a background command in the native no-network sandbox, using PowerShell on Windows or Bash on Linux. Returns an owned process ID. Poll output, send stdin or stop it with process tools. Pipe input only, not a terminal. Windows workspace changes appear after exit; timeout stops the whole process tree.",
            {
                "command": {"type": "string", "minLength": 1, "maxLength": 16000},
                "cwd": string,
                "timeout": {"type": "number", "minimum": 1, "maximum": 3600},
            },
            ["command"],
        ),
        tool(
            "poll_process",
            "Read the recorded status and bounded output of a process in this conversation workspace.",
            {"process_id": string},
            ["process_id"],
        ),
        tool(
            "send_process_input",
            "Send UTF-8 stdin text (include a newline when needed) or EOF to an owned process. No terminal key sequences. Input is capped at 8 KiB.",
            {
                "process_id": string,
                "text": {"type": "string", "maxLength": 8192},
                "eof": {"type": "boolean"},
            },
            ["process_id"],
        ),
        tool(
            "stop_process",
            "Stop an owned process and wait for its entire sandbox process tree to be cleaned up. Does not undo completed edits.",
            {"process_id": string},
            ["process_id"],
        ),
        tool(
            "fetch_url",
            "Download a bounded public HTTPS resource from an explicitly allowed host to a new workspace file. No redirects, credentials, private/local addresses or command networking. Supply sha256 when known. Ask the user to allow the exact host in Settings if blocked.",
            {
                "url": {"type": "string", "maxLength": 2048},
                "path": string,
                "sha256": {"type": "string", "pattern": "^[a-fA-F0-9]{64}$"},
            },
            ["url", "path"],
        ),
        tool(
            "read_memory",
            "Read durable project notes and their hash from .rtagent/MEMORY.md.",
            {},
            [],
        ),
        tool(
            "save_memory",
            "Save durable project notes without credentials. Pass the hash returned by read_memory.",
            {"content": content, "expected_hash": string},
            ["content", "expected_hash"],
        ),
        tool(
            "load_skill",
            "Load the full instructions of one discovered skill when its description matches the task.",
            {"name": string},
            ["name"],
        ),
        tool(
            "save_skill",
            "Create .agents/skills/<name>/SKILL.md. Supply an English description and Markdown instructions without frontmatter. For updates, pass expected_hash from load_skill.",
            {
                "name": string,
                "description": {"type": "string", "maxLength": 1000},
                "instructions": content,
                "expected_hash": string,
            },
            ["name", "description", "instructions"],
        ),
        ToolDefinition(
            name="read_text",
            description="Read workspace UTF-8 text with 1-based start_line and line_count (default 200, max 500). Files may be up to 2 MiB. Returns a full-file hash for guarded edits, total_lines and next_line when more remains. Line numbers are metadata, not file content.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": string,
                    "start_line": {"type": "integer", "minimum": 1},
                    "line_count": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        ),
        ToolDefinition(
            name="echo",
            description="Return a text value unchanged. Useful for checking the approval workflow; has no external side effects.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 4000}},
                "required": ["text"],
                "additionalProperties": False,
            },
        ),
    ]


@asynccontextmanager
async def mcp_session(server: MCPServer):
    # One context owns the subprocess and both SDK streams; cancellation closes all of them.
    """Own a fresh stateless MCP session and close its transports on exit."""
    async with AsyncExitStack() as stack:
        if server.transport == "stdio":
            if not server.command:
                raise AppError("mcp_config", "An executable is required for stdio.")
            environment = {
                key: os.environ[key]
                for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT")
                if key in os.environ
            }
            environment.update(
                {key: resolve_secret(reference) for key, reference in server.env.items()}
            )
            streams = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=server.command, args=server.args, cwd=server.cwd, env=environment
                    )
                )
            )
        else:
            if not server.url.startswith(("http://", "https://")):
                raise AppError("mcp_config", "An HTTP(S) MCP URL is required.")
            secret = resolve_secret(server.credential_ref)
            headers = {"Authorization": "Bearer " + secret} if secret else {}
            client = await stack.enter_async_context(
                httpx.AsyncClient(headers=headers, timeout=60, follow_redirects=False)
            )
            streams = await stack.enter_async_context(
                streamable_http_client(server.url, http_client=client)
            )
        session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
        await session.initialize()
        yield session


class ToolRegistry:
    """Discover tools and execute schemas pinned to a Run snapshot."""

    def __init__(self, workspace: Path, data_dir: Path | None = None):
        """Bind local tools to one resolved workspace and seed the native catalog."""
        self.workspace = workspace.resolve()
        self.files = Workspace(self.workspace, data_dir)
        self.workspaces: dict[Path, ToolRegistry] = {}
        self.processes: ProcessManager | None = None
        self.local_tools: dict[str, tuple[ToolDefinition, ToolHandler]] = {}
        if os.name == "nt":
            from rtagent.infrastructure.sandbox.windows import WindowsSandbox

            self.sandbox = WindowsSandbox(self.files)
        else:
            self.sandbox = BashSandbox(self.files)
        self.catalog: dict[str, ToolDefinition] = {}
        self.errors: dict[str, str] = {}
        self.reset()

    def for_workspace(self, root: Path) -> "ToolRegistry":
        """Bind native tools to a chosen root while sharing current stateless MCP discovery."""
        root = root.resolve()
        if root == self.workspace:
            return self
        if root not in self.workspaces:
            self.workspaces[root] = ToolRegistry(root, self.files.data_dir)
        registry = self.workspaces[root]
        registry.catalog, registry.errors = self.catalog, self.errors
        registry.processes = self.processes
        registry.local_tools = self.local_tools
        return registry

    def bind_repository(self, repository: Repository) -> None:
        """Share one concrete process owner across project registries in this service."""
        if self.processes is None:
            self.processes = ProcessManager(repository)

    def reset(self) -> None:
        """Discard discovered schemas when connection configuration changes."""
        self.catalog = {
            tool.name: tool.model_copy(update={"schema_hash": digest(tool.input_schema)})
            for tool in native_tools()
        }
        self.errors = {}
        self.catalog.update({name: value[0] for name, value in self.local_tools.items()})

    def register(
        self, definition: ToolDefinition, handler: ToolHandler, *, writes_workspace: bool = True
    ) -> None:
        """Register one trusted embedding tool with unchanged snapshot/schema and policy checks."""
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,63}", definition.name) or definition.server_id:
            raise AppError("local_tool", "Local tools require a simple unnamespaced name.")
        if definition.name in self.catalog or len(self.local_tools) >= 20:
            raise AppError(
                "local_tool", "Tool names must be unique; at most twenty local tools are supported."
            )
        try:
            Draft202012Validator.check_schema(definition.input_schema)
        except SchemaError:
            raise AppError(
                "local_tool_schema", "Provide a valid JSON Schema for local tools."
            ) from None
        captured = definition.model_copy(
            update={
                "schema_hash": digest(definition.input_schema),
                "writes_workspace": writes_workspace,
            },
            deep=True,
        )
        self.local_tools[captured.name] = (captured, handler)
        self.catalog[captured.name] = captured

    @staticmethod
    def definition(server: MCPServer, tool) -> ToolDefinition:
        """Namespace MCP tool names and fingerprint their input contracts."""
        name = (
            "mcp_"
            + digest(server.id)[:8]
            + "_"
            + re.sub(r"[^A-Za-z0-9_-]", "_", tool.name)[:32]
            + "_"
            + digest(tool.name)[:6]
        )
        return ToolDefinition(
            name=name,
            original_name=tool.name,
            server_id=server.id,
            description=tool.description or tool.name,
            input_schema=tool.inputSchema,
            schema_hash=digest(tool.inputSchema),
        )

    async def discover(self, config: StudioConfig) -> list[ToolDefinition]:
        """Refresh bounded discovery pages and retain safe per-server failures."""
        self.reset()
        for server in config.mcp_servers:
            try:
                async with asyncio.timeout(15), mcp_session(server) as session:
                    cursor = None
                    for _ in range(20):
                        page = await session.list_tools(cursor=cursor)
                        for tool in page.tools:
                            definition = self.definition(server, tool)
                            self.catalog[definition.name] = definition
                        cursor = page.nextCursor
                        if not cursor:
                            break
            except Exception as exc:
                self.errors[server.id] = (
                    f"Discovery failed ({type(exc).__name__}). Check the server configuration."
                )
        return list(self.catalog.values())

    def selected(self, config: StudioConfig) -> list[ToolDefinition]:
        """Intersect discovered tools with the locally configured permission map."""
        result = []
        for name, permission in config.tools.items():
            if permission == "deny" or (
                config.sandbox_mode == "read-only"
                and (
                    name in WRITE_TOOLS
                    or (name in self.catalog and self.catalog[name].writes_workspace)
                )
            ):
                continue
            if name not in self.catalog:
                raise AppError(
                    "tool_unavailable",
                    f"Tool {name} is unavailable. Refresh discovery or remove it from the profile.",
                )
            result.append(self.catalog[name])
        return result

    async def execute(self, definition: ToolDefinition, args: dict, config: StudioConfig) -> str:
        """Execute one authorized call after checking its current MCP schema."""
        name = definition.name
        if config.sandbox_mode == "read-only" and (
            name in WRITE_TOOLS
            or definition.writes_workspace
            or (name in self.local_tools and self.local_tools[name][0].writes_workspace)
        ):
            raise AppError("tool_denied", "The workspace is read-only.", 403)
        if name in self.local_tools:
            current, handler = self.local_tools[name]
            if current.schema_hash != definition.schema_hash:
                raise AppError("tool_schema_changed", "Local tool schema changed since this Run.")
            text = await handler(dict(args), self.files)
            if not isinstance(text, str) or len(text.encode()) > 65536:
                raise AppError(
                    "outcome_unknown",
                    "Local tool returned an invalid result; inspect its effects before resuming.",
                )
            return text
        if name in {"bash", "powershell"}:
            if name != ("powershell" if os.name == "nt" else "bash"):
                raise AppError(
                    "tool_unavailable", "This command tool belongs to a different operating system."
                )
            timeout = min(args.get("timeout", config.tool_timeout), config.tool_timeout)
            return await self.sandbox.execute(
                args["command"],
                args.get("cwd", "."),
                config.sandbox_mode,
                timeout,
                on_output=(current_execution.get().on_output if current_execution.get() else None),
                toolchain_roots=config.toolchain_roots,
            )
        if name == "fetch_url":
            return json.dumps(
                await fetch_url(self.files, config.network_hosts, **args), ensure_ascii=False
            )
        if name in {"start_process", "poll_process", "send_process_input", "stop_process"}:
            owner = current_execution.get()
            if owner is None or self.processes is None:
                raise AppError("process_owner", "Process tools require a recorded Run.")
            if name == "start_process":
                return await self.processes.start(self.sandbox, args, config, owner)
            identifier = args["process_id"]
            if name == "poll_process":
                return process_view(
                    await self.processes.owned(identifier, owner.session_id, self.workspace)
                ).model_dump_json()
            if name == "send_process_input":
                if (
                    config.sandbox_mode == "read-only"
                    and (await self.processes.owned(identifier, owner.session_id, self.workspace))[
                        "data"
                    ]["mode"]
                    != "read-only"
                ):
                    raise AppError(
                        "tool_denied", "A read-only Run cannot feed a writable process.", 403
                    )
                view = await self.processes.input(
                    identifier,
                    owner.session_id,
                    args.get("text", ""),
                    args.get("eof", False),
                    self.workspace,
                )
            else:
                view = await self.processes.stop(identifier, owner.session_id, self.workspace)
            return view.model_dump_json()
        if name == "echo":
            return args["text"]
        if name == "update_plan":
            try:
                return TaskPlan.model_validate(args).model_dump_json()
            except ValidationError as exc:
                raise AppError("invalid_tool_arguments", exc.errors()[0]["msg"]) from exc
        operations = {
            "read_text": self.files.read_lines,
            "list_files": self.files.list_files,
            "search_files": self.files.search_files,
            "search_text": self.files.search_text,
            "read_instructions": self.files.read_instructions,
            "write_text": self.files.write,
            "edit_text": self.files.edit,
            "apply_patch": self.files.apply_patch,
            "read_memory": self.files.memory,
            "load_skill": self.files.load_skill,
            "save_skill": self.files.save_skill,
        }
        if name == "save_memory":
            result = await asyncio.to_thread(self.files.write, MEMORY_PATH, **args)
        elif name in operations:
            result = await asyncio.to_thread(operations[name], **args)
        else:
            result = None
        if result is not None:
            text = json.dumps(result, ensure_ascii=False)
            if len(text.encode()) > 65536:
                raise AppError(
                    "tool_output_limit", "Serialized tool output exceeds the 64 KiB limit."
                )
            return text
        server = next(
            (server for server in config.mcp_servers if server.id == definition.server_id), None
        )
        if server is None:
            raise AppError("tool_unavailable", "MCP server is absent from the Run snapshot.")
        async with mcp_session(server) as session:
            current, cursor = None, None
            for _ in range(20):
                page = await session.list_tools(cursor=cursor)
                current = next(
                    (tool for tool in page.tools if tool.name == definition.original_name), None
                )
                cursor = page.nextCursor
                if current or not cursor:
                    break
            schema_matches = (
                current is not None and digest(current.inputSchema) == definition.schema_hash
            )
            result = (
                await session.call_tool(definition.original_name, args) if schema_matches else None
            )
        # Raise local validation errors after SDK task groups close, preserving their identity.
        if result is None:
            raise AppError(
                "tool_schema_changed", "MCP tool schema changed. Create a new preview and Run."
            )
        text = json.dumps(result.model_dump(mode="json", exclude_none=True), ensure_ascii=False)
        if len(text.encode()) > 65536:
            raise AppError("tool_output_limit", "MCP result exceeds the 64 KiB limit.")
        return text
