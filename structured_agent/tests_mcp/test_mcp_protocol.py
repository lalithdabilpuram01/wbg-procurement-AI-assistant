"""MCP protocol behaviour over a real stdio transport: handshake, discovery, schemas, structured responses, invalid
arguments, shutdown and failure handling."""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time

import pytest

from structured_agent.contracts import (MetricDefinitionResponse, SearchTableRegistryResponse, TableRelationshipsResponse,
                                        TableSchemaResponse, VerifiedSqlExamplesResponse)
from structured_agent.mcp_server import TOOL_NAMES
from structured_agent.mcp_transport.client import PROJECT_DIR, MCP_PYTHON, ToolCallError, connect, server_parameters
from structured_agent.tests_mcp.conftest import child_pids
from structured_agent.tests_mcp.mcp_session import BackgroundMcpSession

GOLD = "workspace.wbg_gold"
EXPECTED_ARGUMENTS = {
    "search_table_registry": ({"query"}, {"query", "top_k", "request_id"}),
    "get_table_schema": ({"object_fqn"}, {"object_fqn", "request_id"}),
    "get_table_relationships": ({"object_fqn"}, {"object_fqn", "request_id"}),
    "get_metric_definition": (set(), {"metric_id", "query", "request_id"}),
    "get_verified_sql_examples": (set(), {"query", "object_fqn", "limit", "request_id"}),
    "validate_sql": ({"sql"}, {"sql", "parameters", "request_id"}),
    "execute_readonly_sql": ({"sql"}, {"sql", "parameters", "max_rows", "request_id"}),
}


# ------------------------------------------------------------------------------------------- handshake and discovery
def test_initialize_handshake_over_stdio():
    """A fresh client runs the MCP initialize handshake and the server identifies itself."""
    async def handshake():
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        async with stdio_client(server_parameters()) as (read, write):
            async with ClientSession(read, write) as session:
                return await session.initialize()
    result = asyncio.run(handshake())
    assert result.serverInfo.name == "wbg-structured-procurement-data"
    assert result.protocolVersion and result.capabilities.tools is not None


def test_exactly_the_seven_tools_are_discovered(analyst):
    assert {tool.name for tool in analyst.list_tools()} == set(TOOL_NAMES) == set(EXPECTED_ARGUMENTS)


def test_tool_schemas_match_the_contracts(analyst):
    for tool in analyst.list_tools():
        required, properties = EXPECTED_ARGUMENTS[tool.name]
        schema = tool.inputSchema
        assert schema["type"] == "object"
        assert set(schema.get("required", [])) == required, tool.name
        assert set(schema["properties"]) == properties, tool.name
        assert not any("principal" in name or "workspace" in name for name in schema["properties"]), tool.name
        assert tool.description


# ------------------------------------------------------------------------------------ structured registry responses
def test_registry_search_through_mcp(analyst):
    response = SearchTableRegistryResponse.model_validate(
        analyst.call("search_table_registry", query="contract award value by procurement method", top_k=3, request_id="proto_search"))
    assert response.status == "ok" and response.request_id == "proto_search" and response.invocation_id.startswith("inv_")
    assert response.candidates[0].object_fqn == f"{GOLD}.gold_contract_analytics"
    assert all(c.layer == "gold" for c in response.candidates)  # analysts never see Silver candidates


def test_table_schema_through_mcp(analyst):
    response = TableSchemaResponse.model_validate(analyst.call("get_table_schema", object_fqn=f"{GOLD}.gold_contract_analytics"))
    names = {c.name for c in response.columns}
    assert response.status == "ok" and response.grain_columns == ["wb_contract_number"]
    assert "contract_amount_usd" in names and "bronze_source_file" not in names and response.hidden_column_count == 8


def test_relationships_and_metrics_through_mcp(analyst):
    relationships = TableRelationshipsResponse.model_validate(
        analyst.call("get_table_relationships", object_fqn=f"{GOLD}.gold_contract_analytics"))
    assert {r.relationship_id for r in relationships.relationships} == {"gold_contracts_to_portfolio", "gold_contracts_to_country_context"}
    assert all(r.status == "verified" for r in relationships.relationships)

    metric = MetricDefinitionResponse.model_validate(analyst.call("get_metric_definition", metric_id="contract_award_value_usd"))
    assert metric.metrics[0].expression == "sum(contract_amount_usd)"

    ambiguous = MetricDefinitionResponse.model_validate(
        analyst.call("get_metric_definition", query="What is the total value for Ethiopia?"))
    assert ambiguous.status == "needs_clarification" and ambiguous.errors[0].code.value == "AMBIGUOUS_METRIC"


def test_verified_sql_examples_through_mcp(analyst):
    response = VerifiedSqlExamplesResponse.model_validate(
        analyst.call("get_verified_sql_examples", object_fqn=f"{GOLD}.gold_contract_analytics", limit=5))
    assert response.status == "ok" and all(f"{GOLD}.gold_contract_analytics" in e.objects for e in response.examples)


# ---------------------------------------------------------------------------------------------- invalid arguments
@pytest.mark.parametrize("tool, arguments, message", [
    ("validate_sql", {}, "required"),                                                         # missing argument
    ("search_table_registry", {"query": "contracts", "top_k": "many"}, "integer"),            # wrong type
    ("validate_sql", {"sql": "SELECT 1", "principal_id": "steward-demo"}, "Unexpected keyword"),  # impersonation attempt
    ("execute_readonly_sql", {"sql": "SELECT 1", "workspace": "other"}, "Unexpected keyword"),
    ("drop_everything", {}, "Unknown tool"),                                                  # tool that does not exist
])
def test_invalid_tool_arguments_return_mcp_errors(analyst, tool, arguments, message):
    with pytest.raises(ToolCallError, match=message):
        analyst.call(tool, **arguments)


def test_contract_violation_returns_a_typed_error(analyst):
    response = analyst.call("search_table_registry", query="contracts", top_k=50)
    assert response["status"] == "error" and response["errors"][0]["code"] == "INVALID_REQUEST"


def test_server_keeps_working_after_errors(analyst):
    analyst.raw_call("validate_sql", {})
    assert analyst.call("get_metric_definition", metric_id="project_count")["status"] == "ok"


# ---------------------------------------------------------------------------------------- shutdown and failures
def test_clean_shutdown_stops_the_server_process():
    before = child_pids()
    session = BackgroundMcpSession(server_parameters()).open()
    server_pid = (child_pids() - before).pop()
    assert session.call("get_metric_definition", metric_id="project_count")["status"] == "ok"
    session.close()
    assert not _process_alive(server_pid, wait_seconds=10)


def test_server_startup_failure_is_reported_to_the_client():
    """A misconfigured server (no principal) exits at startup; the client gets an error instead of hanging."""
    started = time.perf_counter()
    with pytest.raises(Exception):
        BackgroundMcpSession(server_parameters(extra_env={"WBG_MCP_PRINCIPAL": ""}), read_timeout_seconds=30).open()
    assert time.perf_counter() - started < 60


def test_server_killed_mid_session_fails_the_pending_call():
    before = child_pids()
    session = BackgroundMcpSession(server_parameters(), read_timeout_seconds=20).open()
    server_pid = (child_pids() - before).pop()
    os.kill(server_pid, signal.SIGKILL)
    started = time.perf_counter()
    with pytest.raises(Exception):
        session.call("get_metric_definition", metric_id="project_count")
    assert time.perf_counter() - started < 30
    session.close()


def test_server_exits_when_the_client_disconnects_abruptly():
    client = subprocess.Popen([str(MCP_PYTHON), "-m", "structured_agent.tests_mcp.hold_connection"], cwd=PROJECT_DIR,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    server_pid = _read_server_pid(client)
    client.send_signal(signal.SIGKILL)
    client.wait(timeout=10)
    assert not _process_alive(server_pid, wait_seconds=20)


# ---------------------------------------------------------------------------------------------------- helpers
def _read_server_pid(client: subprocess.Popen) -> int:
    for line in client.stdout:
        if line.startswith("SERVER_PID"):
            return int(line.split()[1])
    raise RuntimeError("helper client did not report a server pid")


def _process_alive(pid: int, wait_seconds: float) -> bool:
    """Waits up to wait_seconds for the process to exit; True if it is still running."""
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        if subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip().startswith("Z"):
            return False  # exited, waiting to be reaped
        time.sleep(0.2)
    return True
