"""A stateless MCP fixture used by offline transport contract tests."""

import argparse
import os

from mcp.server.fastmcp import FastMCP

parser = argparse.ArgumentParser()
parser.add_argument("--http", type=int)
arguments = parser.parse_args()
server = FastMCP(
    "studio-contract",
    host="127.0.0.1",
    port=arguments.http or 8766,
    stateless_http=True,
    json_response=True,
)


if os.environ.get("FIXTURE_SCHEMA") == "changed":

    @server.tool(name="fixture_echo")
    def changed_echo(value: str) -> str:
        """Expose a changed schema to test stale snapshot rejection."""
        return value

else:

    @server.tool()
    def fixture_echo(text: str) -> str:
        """Return synthetic test text without external side effects."""
        return "MCP evidence: " + text


@server.tool()
def environment_check() -> dict[str, bool]:
    """Report variable presence without revealing any credential values."""
    return {
        "explicit_reference": bool(os.environ.get("FIXTURE_SECRET")),
        "ambient_key": bool(os.environ.get("UNRELATED_TEST_KEY")),
    }


if __name__ == "__main__":
    server.run(transport="streamable-http" if arguments.http else "stdio")
