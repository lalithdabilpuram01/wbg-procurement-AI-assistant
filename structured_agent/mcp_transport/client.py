"""Minimal MCP client helpers for the Structured Data MCP server (official MCP Python SDK, stdio transport).

The server is always started as a separate process: in the isolated environment `.venv-structured-mcp` when it exists,
else in the project's uv environment (MCP_PYTHON). It receives
only the MCP SDK's default safe environment (HOME, PATH, ...) plus the WBG_* settings passed here, so a test never
leaks flags such as WBG_ALLOW_DEV_OWNER_CREDENTIALS into a server unintentionally.
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

PROJECT_DIR = Path(__file__).resolve().parents[2]
ISOLATED_PYTHON = PROJECT_DIR / ".venv-structured-mcp" / "bin" / "python"
# The isolated environment when it exists (the validated setup); otherwise the project's uv environment (`uv sync`),
# which carries the same pinned packages. WBG_MCP_PYTHON overrides both.
MCP_PYTHON = Path(os.environ.get("WBG_MCP_PYTHON") or (ISOLATED_PYTHON if ISOLATED_PYTHON.exists() else sys.executable))
WORKSPACE = "wbg-procurement-poc"


class ToolCallError(Exception):
    """The server answered with an MCP error result (e.g. arguments rejected by the tool schema)."""


def server_parameters(principal_id: str = "analyst-demo", *, allow_dev_execution: bool = False,
                      timeout_seconds: int | None = None, audit_log: Path | None = None,
                      extra_env: dict[str, str] | None = None) -> StdioServerParameters:
    """How to launch one server process. Development execution with the owner credential is off unless requested."""
    env = {"WBG_MCP_PRINCIPAL": principal_id, "WBG_MCP_WORKSPACE": WORKSPACE}
    if allow_dev_execution:
        env["WBG_ALLOW_DEV_OWNER_CREDENTIALS"] = "true"
    if timeout_seconds is not None:
        env["WBG_SQL_TIMEOUT_SECONDS"] = str(timeout_seconds)
    if audit_log is not None:
        env["WBG_AUDIT_LOG"] = str(audit_log)
    env.update(extra_env or {})
    return StdioServerParameters(command=str(MCP_PYTHON), args=["-m", "structured_agent.mcp_server"],
                                 cwd=str(PROJECT_DIR), env=env)


@asynccontextmanager
async def connect(parameters: StdioServerParameters, read_timeout_seconds: float = 180) -> AsyncIterator[ClientSession]:
    """Start the server process, run the MCP initialize handshake, and close everything on exit."""
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream,
                                 read_timeout_seconds=timedelta(seconds=read_timeout_seconds)) as session:
            await session.initialize()
            yield session


def structured(result: CallToolResult) -> dict[str, Any]:
    """The tool's structured response, or ToolCallError when the server returned an MCP error result."""
    if result.isError:
        message = " ".join(getattr(block, "text", "") for block in result.content)
        raise ToolCallError(message[:500])
    return result.structuredContent


async def call_tool(session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call one tool and return its structured response (ToolCallError on an MCP error result)."""
    return structured(await session.call_tool(name, arguments))
