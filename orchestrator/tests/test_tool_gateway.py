"""The tool gateway (offline): MCP session supervision, Gold lookups through execute_readonly_sql, the fixed principal,
and the catalog of which tools run over MCP and which in-process."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from conversation.routing.routes import Route
from structured_agent.mcp_server import TOOL_NAMES
from structured_agent.mcp_transport.client import ToolCallError
from structured_agent.nl2sql.tools import McpToolCaller
from tests.helpers import AUTH
from wbg_orchestrator.catalog import CATALOG, TOOLS, as_markdown
from wbg_orchestrator.factory import structured_worker
from wbg_orchestrator.runtime import RuntimeConfig, structured_tools
from wbg_orchestrator.tool_gateway import IN_PROCESS_TOOLS, McpUnavailable, SupervisedMcpSession, gold_sql
from wbg_orchestrator.workers.base import ResultCache, TicketAuthority
from wbg_orchestrator.workers.prediction import LookupFailed


class FakeSession:
    """One server process: answers until `dies_after` calls, then every call fails like a broken pipe."""

    def __init__(self, tools=TOOL_NAMES, dies_after=None, answer=None):
        self.tools, self.dies_after, self.calls, self.closed = tools, dies_after, [], False
        self.answer = answer or (lambda tool, args: {"status": "ok", "tool": tool, "rows": [{"n": 1}]})

    def list_tools(self):
        return [SimpleNamespace(name=n) for n in self.tools]

    def call(self, tool, **arguments):
        self.calls.append(tool)
        if self.dies_after is not None and len(self.calls) > self.dies_after:
            raise BrokenPipeError("server gone")
        return self.answer(tool, arguments)

    def close(self):
        self.closed = True


def supervised(*sessions):
    queue = list(sessions)
    return SupervisedMcpSession(lambda: "params", open_session=lambda params: queue.pop(0)).open()


def test_a_dead_server_is_restarted_once_and_the_call_succeeds():
    first, second = FakeSession(dies_after=0), FakeSession()
    session = supervised(first, second)
    assert session.call("validate_sql", sql="SELECT 1")["status"] == "ok"
    assert first.closed and session.restarts == 1 and second.calls == ["validate_sql"]


def test_a_server_that_stays_down_is_reported_as_unavailable():
    session = supervised(FakeSession(dies_after=0), FakeSession(dies_after=0))
    with pytest.raises(McpUnavailable):
        session.call("execute_readonly_sql", sql="SELECT 1")
    assert isinstance(McpUnavailable("x"), ConnectionError)          # workers treat it as transient


def test_a_tool_error_is_the_server_working_and_is_not_retried():
    def refuse(tool, args):
        raise ToolCallError("unexpected argument")
    session = supervised(FakeSession(answer=refuse))
    with pytest.raises(ToolCallError):
        session.call("validate_sql", sql="DROP TABLE x")
    assert session.restarts == 0


def test_a_server_offering_other_tools_is_refused_at_start():
    with pytest.raises(McpUnavailable):
        supervised(FakeSession(tools=("execute_readonly_sql", "drop_everything")))


def test_gold_lookups_go_through_execute_readonly_sql():
    session = supervised(FakeSession())
    run = gold_sql(McpToolCaller(session, "analyst-demo"))
    assert run("SELECT 1", {}) == [{"n": 1}] and run.transport == "mcp"
    refused = supervised(FakeSession(answer=lambda tool, args: {"status": "rejected", "rows": []}))
    with pytest.raises(LookupFailed, match="gold_lookup_rejected"):
        gold_sql(McpToolCaller(refused, "analyst-demo"))("SELECT 1", {})


def test_the_structured_agent_serves_only_the_principal_of_its_tools():
    auth = AUTH((Route.STRUCTURED_DATA,))
    other = structured_worker(McpToolCaller(supervised(FakeSession()), "someone-else"), claude=None, telemetry=None,
                              authority=TicketAuthority(), cache=ResultCache())
    with pytest.raises(PermissionError):
        other._tools_for(auth)
    own = structured_worker(McpToolCaller(supervised(FakeSession()), auth.registry_principal), claude=None, telemetry=None,
                            authority=TicketAuthority(), cache=ResultCache())
    assert own._tools_for(auth).transport == "mcp"


def test_an_unknown_transport_is_refused():
    with pytest.raises(ValueError):
        structured_tools(RuntimeConfig(ledger_path=None, max_attempts=1, max_usd=0, tool_transport="http"), tracer=None)


def test_every_operation_says_which_tools_it_uses_and_where_they_run():
    assert {c.operation for c in CATALOG} == set(TOOLS)
    for operation, tools in TOOLS.items():
        for tool, transport in tools:
            assert (transport == "mcp" and tool in TOOL_NAMES) or (transport == "in_process" and tool in IN_PROCESS_TOOLS), tool
    transports = {t for tools in TOOLS.values() for _, t in tools}
    assert transports == {"mcp", "in_process"} and "mcp: execute_readonly_sql" in as_markdown()
