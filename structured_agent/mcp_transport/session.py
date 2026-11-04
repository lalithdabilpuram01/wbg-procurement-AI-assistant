"""A real MCP stdio session usable from synchronous code (backend workflow and pytest tests).

The MCP SDK requires a session to be opened and closed inside the same asyncio task (anyio cancel scopes). The
session is therefore held by one long-running task on a background event loop; each tool call is submitted to that
loop as its own task and the test thread waits for the result.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.types import CallToolResult

from structured_agent.mcp_transport.client import connect, structured

CALL_TIMEOUT_SECONDS = 240


class BackgroundMcpSession:
    """Holds one MCP stdio session on a background event loop so synchronous code can call tools."""

    def __init__(self, parameters: StdioServerParameters, read_timeout_seconds: float = 180):
        """Prepare the background event loop and its thread; nothing starts until `open`."""
        self._parameters = parameters
        self._read_timeout_seconds = read_timeout_seconds
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._opened = threading.Event()
        self._close_requested = asyncio.Event()  # binds to the background loop when first awaited (Python >= 3.10)
        self.session: ClientSession | None = None

    # ------------------------------------------------------------------------------------------------ lifecycle
    def open(self) -> "BackgroundMcpSession":
        """Start the server process and complete the MCP initialize handshake (raises if the server fails)."""
        self._thread.start()
        self._holder = asyncio.run_coroutine_threadsafe(self._hold_session(), self._loop)
        while not self._opened.wait(timeout=0.2):
            if self._holder.done():
                self._holder.result()  # re-raise the startup failure
                raise RuntimeError("MCP session ended before it was opened")
        return self

    def close(self) -> None:
        """Close the session (the SDK then stops the server process) and stop the background loop."""
        if self._holder.done():
            self._holder.exception()  # the session already ended, e.g. the server was killed
        else:
            self._loop.call_soon_threadsafe(self._close_requested.set)
            self._holder.result(timeout=60)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)

    async def _hold_session(self) -> None:
        """Open the session and keep it open, in one task, until close() is requested."""
        async with connect(self._parameters, self._read_timeout_seconds) as session:
            self.session = session
            self._opened.set()
            await self._close_requested.wait()

    def _run_soon(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    # ---------------------------------------------------------------------------------------------------- calls
    def raw_call(self, tool: str, arguments: dict[str, Any]) -> CallToolResult:
        """The unmodified MCP result (use for protocol-level error tests)."""
        return self._run_soon(self.session.call_tool(tool, arguments)).result(timeout=CALL_TIMEOUT_SECONDS)

    def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        """The tool's structured response; raises ToolCallError on an MCP error result."""
        return structured(self.raw_call(tool, arguments))

    def call_concurrently(self, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        """Send several tool calls at once over the same session and wait for all of them."""
        async def all_calls():
            results = await asyncio.gather(*(self.session.call_tool(tool, args) for tool, args in calls))
            return [structured(r) for r in results]
        return self._run_soon(all_calls()).result(timeout=CALL_TIMEOUT_SECONDS)

    def list_tools(self):
        return self._run_soon(self.session.list_tools()).result(timeout=CALL_TIMEOUT_SECONDS).tools
