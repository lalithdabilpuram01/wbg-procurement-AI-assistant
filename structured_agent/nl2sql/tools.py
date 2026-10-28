"""Access to the seven existing Structured Data tools for the workflow.

The workflow never touches the registry, validator or Databricks directly. It calls the approved tools, which keep
contract validation, principal authorization, SQL validation, the execution gateway and audit in one place.

  * McpToolCaller        real MCP stdio transport to a server process (deployment-level principal)
  * InProcessToolCaller  the same StructuredDataService methods the MCP tools wrap, for tests and local runs

Both return the tools' JSON responses as plain dicts, identical in shape.
"""
from __future__ import annotations

from typing import Any, Protocol

from structured_agent.mcp_server import TOOL_NAMES
from structured_agent.mcp_transport.session import BackgroundMcpSession
from structured_agent.service import RequestContext, StructuredDataService


class ToolCaller(Protocol):
    principal_id: str

    def call(self, tool: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        """Invoke one approved tool and return its structured response."""
        ...


def _require_known_tool(tool: str) -> None:
    if tool not in TOOL_NAMES:
        raise ValueError(f"'{tool}' is not one of the approved Structured Data tools")


class McpToolCaller:
    """Uses an open MCP session. The server's principal is fixed by its deployment configuration."""

    transport = "mcp"

    def __init__(self, session: BackgroundMcpSession, principal_id: str):
        self._session = session
        self.principal_id = principal_id

    def call(self, tool: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        _require_known_tool(tool)
        return self._session.call(tool, **arguments, request_id=request_id)


class InProcessToolCaller:
    """Calls the service methods behind the MCP tools, with a trusted principal and workspace."""

    transport = "in_process"

    def __init__(self, service: StructuredDataService, principal_id: str, workspace: str):
        self._service = service
        self.principal_id = principal_id
        self._workspace = workspace

    def call(self, tool: str, arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
        _require_known_tool(tool)
        context = RequestContext(self.principal_id, self._workspace, request_id)
        return getattr(self._service, tool)(context, **arguments).model_dump(mode="json")
