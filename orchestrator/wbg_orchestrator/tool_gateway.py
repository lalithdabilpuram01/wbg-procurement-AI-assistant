"""Where each agent's tools run: over MCP, or in-process inside the LangGraph app.

Over MCP: the server `wbg-structured-procurement-data` (structured_agent/mcp_server.py). It is a separate process in
its own environment (.venv-structured-mcp), holds the read-only Databricks principal and contains no LLM.
    search_table_registry, get_table_schema, get_table_relationships, get_metric_definition,
    get_verified_sql_examples, validate_sql, execute_readonly_sql
  used by the Structured Data agent (all seven) and by the Prediction and Planning agents (execute_readonly_sql, for
  their fixed, reviewed Gold lookups).

In-process, inside the app: document retrieval (Lakebase pgvector, bge-m3), TableLens (table search + SQL over the
PDF tables in Lakebase), the frozen ML models, similar-project scoring, guardrails, and the Claude planner, SQL writer
and answer writer.

WBG_TOOL_TRANSPORT selects `mcp` (the app's default) or `in_process` (offline tests, local fallback). With MCP the app
process opens no Databricks connection for Gold: only the server does.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from structured_agent.mcp_server import TOOL_NAMES
from structured_agent.mcp_transport.client import ToolCallError, server_parameters
from structured_agent.mcp_transport.session import BackgroundMcpSession
from structured_agent.nl2sql.tools import ToolCaller

from ml.serving.gold import RunSql

from .observability import OrchestratorTracer, current_trace_id
from .workers.prediction import GATEWAY_MAX_ROWS, LookupFailed

MCP_SERVER = "wbg-structured-procurement-data"
TRANSPORTS = ("mcp", "in_process")
IN_PROCESS_TOOLS = ("lakebase_vector_search", "tablelens_table_sql", "frozen_ml_models", "similar_project_scoring",
                    "monitoring_rules", "tender_rules", "lesson_card_store")


class McpUnavailable(ConnectionError):
    """The MCP server could not be started, or stopped answering and did not come back after one restart."""


class SupervisedMcpSession:
    """The MCP server process: started with the app, its tool list checked, restarted once if it dies, closed with the app.

    A tool that answers with an error (ToolCallError) is the server working as designed and is never retried here.
    """

    def __init__(self, parameters: Callable[[], Any], tracer: OrchestratorTracer | None = None,
                 open_session: Callable[[Any], Any] = lambda p: BackgroundMcpSession(p).open()):
        self._parameters, self._open = parameters, open_session
        self._tracer = tracer or OrchestratorTracer.disabled()
        self._lock = threading.Lock()
        self._session: Any = None
        self.restarts = 0

    def open(self) -> "SupervisedMcpSession":
        with self._lock:
            self._start()
        return self

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                _quietly(self._session.close)
                self._session = None

    def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        started = time.perf_counter()
        with self._tracer.span("mcp.tool_call", operation=tool, request_id=arguments.get("request_id")) as span:
            try:
                result = self._call_with_restart(tool, arguments)
            except ToolCallError:
                span(status="tool_error", duration_ms=_ms(started))
                raise
            except McpUnavailable:
                span(status="unavailable", duration_ms=_ms(started))
                raise
            span(status="ok", duration_ms=_ms(started), retries=self.restarts)
            return result

    def _call_with_restart(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self._current()
        try:
            return session.call(tool, **arguments)
        except ToolCallError:
            raise
        except Exception as error:                              # the transport broke: the server died or hung
            with self._lock:
                if self._session is session:                    # not already restarted by another thread
                    _quietly(session.close)
                    self._session = None
                    self.restarts += 1
            try:
                return self._current().call(tool, **arguments)
            except ToolCallError:
                raise
            except Exception as retry_error:
                raise McpUnavailable(f"{MCP_SERVER} did not answer after a restart") from retry_error

    def _current(self) -> Any:
        with self._lock:
            if self._session is None:
                self._start()
            return self._session

    def _start(self) -> None:
        try:
            session = self._open(self._parameters())
        except Exception as error:
            raise McpUnavailable(f"{MCP_SERVER} could not be started") from error
        names = {t.name for t in session.list_tools()}
        if names != set(TOOL_NAMES):
            _quietly(session.close)
            raise McpUnavailable(f"{MCP_SERVER} offers unexpected tools")
        self._session = session


def mcp_parameters(principal_id: str, databricks_profile: str, audit_log: Path) -> Callable[[], Any]:
    """How to launch the server: the read-only principal, its Databricks profile and its own audit log; no other settings."""
    return lambda: server_parameters(principal_id, audit_log=audit_log, extra_env={"WBG_DATABRICKS_PROFILE": databricks_profile})


def gold_sql(tools: ToolCaller) -> RunSql:
    """Fixed, reviewed Gold lookups through the `execute_readonly_sql` tool (over MCP or in-process)."""
    def run(sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        result = tools.call("execute_readonly_sql", {"sql": sql, "parameters": parameters, "max_rows": GATEWAY_MAX_ROWS},
                            current_trace_id() or "gold-lookup")
        if result.get("status") != "ok":
            raise LookupFailed(f"gold_lookup_{result.get('status')}")
        return list(result.get("rows") or [])
    run.transport = tools.transport                              # type: ignore[attr-defined]
    return run


def _quietly(close: Callable[[], None]) -> None:
    try:
        close()
    except Exception:                                            # a dead server cannot be closed cleanly; nothing to do
        pass


def _ms(started: float) -> int:
    return int(1000 * (time.perf_counter() - started))
