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


def native_tools() -> list[ToolDefinition]:
    """Describe the bounded file reader and side-effect-free approval demo tool."""
    return [
        ToolDefinition(
            name="read_text",
            description="Read a UTF-8 text file inside the configured workspace (maximum 64 KiB).",
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

    def __init__(self, workspace: Path):
        """Bind local tools to one resolved workspace and seed the native catalog."""
        self.workspace = workspace.resolve()
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
            if permission == "deny":
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
        path = (self.workspace / relative).resolve()
        if not path.is_relative_to(self.workspace):
            raise AppError("tool_denied", "File path is outside the configured workspace.", 403)
        # Windows paths are case-insensitive, including credential file exclusions.
        parts = [part.casefold() for part in path.relative_to(self.workspace).parts]
        if any(
            part in {".git", ".venv", "node_modules", "var", "access-token"}
            or part == ".env"
            or (part.startswith(".env.") and part != ".env.example")
            for part in parts
        ) or path.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
            raise AppError(
                "tool_denied",
                "Credential, runtime, and dependency files are excluded from the file tool.",
                403,
            )
        if not path.is_file():
            raise AppError("tool_error", "The requested text file does not exist.")
        # Refuse links in every component; this local tool is not an OS sandbox.
        candidate = self.workspace / relative
        for part in [candidate, *candidate.parents]:
            if part == self.workspace:
                break
            if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                raise AppError(
                    "tool_denied", "Linked paths are not supported by the local file tool.", 403
                )
        with path.open("rb") as handle:
            content = handle.read(65537)
        if len(content) > 65536:
            raise AppError("tool_output_limit", "File exceeds the 64 KiB read limit.")
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError("tool_error", "The selected file is not UTF-8 text.") from exc

    async def execute(self, definition: ToolDefinition, args: dict, config: StudioConfig) -> str:
        """Execute one authorized call after checking its current MCP schema."""
        if definition.name == "read_text":
            return await asyncio.to_thread(self.read_file, args["path"])
        if definition.name == "echo":
            return args["text"]
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
