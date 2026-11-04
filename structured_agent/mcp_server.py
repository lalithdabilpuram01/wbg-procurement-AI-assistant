"""Domain MCP server for structured procurement data (FastMCP).

Exposes exactly the seven approved operations. It does not expose database credentials, raw table discovery or
arbitrary SQL execution: every call goes through StructuredDataService (contract validation, principal
authorization, SQL validation, gateway execution, audit).

Identity: the calling principal is fixed by the server's deployment configuration (WBG_MCP_PRINCIPAL,
WBG_MCP_WORKSPACE), not by tool arguments, so a client cannot choose its own identity or role. This is a
single-principal development setup: per-user authorization (an authenticated identity per MCP session) is NOT
implemented yet; every client of one server process acts as that server's configured principal.

Run with the isolated MCP environment (stdio transport, from procurement-intelligence/):
    WBG_MCP_PRINCIPAL=analyst-demo WBG_MCP_WORKSPACE=wbg-procurement-poc \
        .venv-structured-mcp/bin/python -m structured_agent.mcp_server
stdout carries only MCP protocol messages; logs go to stderr.
"""
from __future__ import annotations

import os
import time
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext

from structured_agent.service import RequestContext, StructuredDataService

TOOL_NAMES = ("search_table_registry", "get_table_schema", "get_table_relationships", "get_metric_definition",
              "get_verified_sql_examples", "validate_sql", "execute_readonly_sql")


class AuditRejectedCalls(Middleware):
    """Audits tool calls that FastMCP rejects before the service runs (an unexpected argument such as an attempted
    principal id, or an unknown tool). Calls that reach the service are audited by the service itself.

    Calls whose arguments fail the tool's JSON schema (missing or wrongly typed fields) are rejected earlier, by the
    MCP SDK, and never reach this middleware; they are refused but not audited (documented limitation)."""

    def __init__(self, service: StructuredDataService, principal_id: str):
        self._service = service
        self._principal_id = principal_id

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        """Pass the call on; if FastMCP rejects it, audit the rejection (known tool names only) and re-raise."""
        started = time.perf_counter()
        try:
            return await call_next(context)
        except Exception:
            arguments = context.message.arguments or {}
            request_id = arguments.get("request_id") if isinstance(arguments.get("request_id"), str) else None
            tool = context.message.name if context.message.name in TOOL_NAMES else "unknown_tool"  # no client text in logs
            self._service.audit_protocol_rejection(tool, self._principal_id, request_id,
                                                   round(1000 * (time.perf_counter() - started)))
            raise  # FastMCP turns the exception into an MCP error result for the client


def build_server(service: StructuredDataService, principal_id: str, workspace: str) -> FastMCP:
    """Build the FastMCP server with the seven tools, all bound to one fixed principal and workspace."""
    mcp = FastMCP("wbg-structured-procurement-data")
    mcp.add_middleware(AuditRejectedCalls(service, principal_id))

    # A missing request id falls back to RequestContext's default, which generates a new one.
    def context(request_id: str | None) -> RequestContext:
        return RequestContext(principal_id, workspace, request_id) if request_id else RequestContext(principal_id, workspace)

    # Each tool below only forwards to the service and returns its response as JSON. Tool docstrings are the
    # descriptions the MCP client (and the model) sees, so they stay short and exact.
    @mcp.tool
    def search_table_registry(query: str, top_k: int = 5, request_id: str | None = None) -> dict[str, Any]:
        """Find the registered tables, views and metrics relevant to a question (authorized objects only)."""
        return service.search_table_registry(context(request_id), query=query, top_k=top_k).model_dump(mode="json")

    @mcp.tool
    def get_table_schema(object_fqn: str, request_id: str | None = None) -> dict[str, Any]:
        """Schema, grain, required filters and provenance of one registered object (authorized columns only)."""
        return service.get_table_schema(context(request_id), object_fqn=object_fqn).model_dump(mode="json")

    @mcp.tool
    def get_table_relationships(object_fqn: str, request_id: str | None = None) -> dict[str, Any]:
        """Approved joins of one registered object, with cardinality and aggregation rules."""
        return service.get_table_relationships(context(request_id), object_fqn=object_fqn).model_dump(mode="json")

    @mcp.tool
    def get_metric_definition(metric_id: str | None = None, query: str | None = None,
                              request_id: str | None = None) -> dict[str, Any]:
        """Definition of a business metric, or a clarification request when the wording is ambiguous."""
        return service.get_metric_definition(context(request_id), metric_id=metric_id, query=query).model_dump(mode="json")

    @mcp.tool
    def get_verified_sql_examples(query: str | None = None, object_fqn: str | None = None, limit: int = 3,
                                  request_id: str | None = None) -> dict[str, Any]:
        """Reviewed SQL templates for authorized objects, with their verification status."""
        return service.get_verified_sql_examples(context(request_id), query=query, object_fqn=object_fqn,
                                                 limit=limit).model_dump(mode="json")

    @mcp.tool
    def validate_sql(sql: str, parameters: dict[str, Any] | None = None, request_id: str | None = None) -> dict[str, Any]:
        """Validate one SELECT query against the registry, policy and budgets. Never executes anything."""
        return service.validate_sql(context(request_id), sql=sql, parameters=parameters or {}).model_dump(mode="json")

    @mcp.tool
    def execute_readonly_sql(sql: str, parameters: dict[str, Any] | None = None, max_rows: int | None = None,
                             request_id: str | None = None) -> dict[str, Any]:
        """Re-validate and execute one read-only SELECT through the approved gateway; returns a structured result."""
        return service.execute_readonly_sql(context(request_id), sql=sql, parameters=parameters or {},
                                            max_rows=max_rows).model_dump(mode="json")

    return mcp


def main() -> None:
    """Start the stdio MCP server for the principal and workspace set in the environment."""
    principal_id = os.environ.get("WBG_MCP_PRINCIPAL")
    workspace = os.environ.get("WBG_MCP_WORKSPACE")
    if not principal_id or not workspace:
        raise SystemExit("Set WBG_MCP_PRINCIPAL and WBG_MCP_WORKSPACE for this server deployment")
    build_server(StructuredDataService.from_environment(), principal_id, workspace).run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
