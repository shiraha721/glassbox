"""Bounded local tools and stateless MCP operations, without a plugin framework."""

import asyncio
import json
import os
import re
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from rtagent.config import resolve_secret
from rtagent.domain import AppError, MCPServer, StudioConfig, ToolDefinition, digest
from rtagent.sandbox import BashSandbox
from rtagent.workspace import MEMORY_PATH, Workspace

WRITE_TOOLS = {"write_text", "edit_text", "save_memory", "save_skill"}


def native_tools() -> list[ToolDefinition]:
    """Describe the concrete workspace tools without a generic extension framework."""

    def tool(name: str, description: str, properties: dict, required: list[str]) -> ToolDefinition:
        return ToolDefinition(
            name=name,
            description=description,
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
            description="Read workspace UTF-8 text and its content hash (maximum 64 KiB).",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
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
        if os.name == "nt":
            from rtagent.windows_sandbox import WindowsSandbox

            self.sandbox = WindowsSandbox(self.files)
        else:
            self.sandbox = BashSandbox(self.files)
        self.catalog: dict[str, ToolDefinition] = {}
        self.errors: dict[str, str] = {}
        self.reset()

    def reset(self) -> None:
        """Discard discovered schemas when connection configuration changes."""
        self.catalog = {
            tool.name: tool.model_copy(update={"schema_hash": digest(tool.input_schema)})
            for tool in native_tools()
        }
        self.errors = {}

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
            if permission == "deny" or (config.sandbox_mode == "read-only" and name in WRITE_TOOLS):
                continue
            if name not in self.catalog:
                raise AppError(
                    "tool_unavailable",
                    f"Tool {name} is unavailable. Refresh discovery or remove it from the profile.",
                )
            result.append(self.catalog[name])
        return result

    def read_file(self, relative: str) -> str:
        """Read at most 64 KiB of workspace UTF-8 text, excluding links and secrets."""
        return self.files.read(relative)["content"]

    async def execute(self, definition: ToolDefinition, args: dict, config: StudioConfig) -> str:
        """Execute one authorized call after checking its current MCP schema."""
        name = definition.name
        if config.sandbox_mode == "read-only" and name in WRITE_TOOLS:
            raise AppError("tool_denied", "The workspace is read-only.", 403)
        if name in {"bash", "powershell"}:
            if name != ("powershell" if os.name == "nt" else "bash"):
                raise AppError(
                    "tool_unavailable", "This command tool belongs to a different operating system."
                )
            timeout = min(args.get("timeout", config.tool_timeout), config.tool_timeout)
            return await self.sandbox.execute(
                args["command"], args.get("cwd", "."), config.sandbox_mode, timeout
            )
        if name == "echo":
            return args["text"]
        operations = {
            "read_text": self.files.read,
            "list_files": self.files.list_files,
            "write_text": self.files.write,
            "edit_text": self.files.edit,
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
