"""Helper process for the disconnect test: opens an MCP session, prints the server's process id, then waits.

The test kills this client abruptly (SIGKILL) and checks that the orphaned server notices the closed stdin and exits.
"""
from __future__ import annotations

import asyncio
import os
import subprocess

from structured_agent.mcp_transport.client import connect, server_parameters


async def hold() -> None:
    async with connect(server_parameters()):
        children = subprocess.run(["pgrep", "-P", str(os.getpid())], capture_output=True, text=True).stdout.split()
        print(f"SERVER_PID {children[0]}", flush=True)
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(hold())
