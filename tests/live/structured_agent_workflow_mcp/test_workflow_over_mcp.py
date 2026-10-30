"""The SQL workflow over the real MCP stdio transport, with a scripted (fake) Claude.

Validation only: execution is not requested, so the server never contacts Databricks (no credential check, no query).
Run with the isolated environment (from procurement-intelligence/):
    .venv-structured-mcp/bin/python -m pytest structured_agent/tests_workflow_mcp -q -p no:cacheprovider
"""
from __future__ import annotations

import json

import pytest

from structured_agent.mcp_transport.client import server_parameters
from structured_agent.mcp_transport.session import BackgroundMcpSession
from structured_agent.nl2sql.result import FailureClass, WorkflowStatus
from structured_agent.nl2sql.settings import RuntimeSettings
from structured_agent.nl2sql.tools import McpToolCaller
from structured_agent.nl2sql.workflow import NlToSqlWorkflow
from structured_agent.observability import Telemetry
from tests.structured_agent.nl2sql_fakes import TEST_API_KEY, FakePortkey, offline_claude_client, sql_draft
from tests.structured_agent.test_nl2sql_workflow import BID_QUESTION, BID_SQL, SILVER, ListSink


@pytest.fixture(scope="module")
def server_audit(tmp_path_factory):
    return tmp_path_factory.mktemp("mcp_workflow") / "server_audit.jsonl"


@pytest.fixture(scope="module")
def mcp_tools(server_audit):
    session = BackgroundMcpSession(server_parameters("analyst-demo", audit_log=server_audit)).open()
    yield McpToolCaller(session, "analyst-demo")
    session.close()


def run(mcp_tools, portkey: FakePortkey, request_id: str):
    workflow = NlToSqlWorkflow(mcp_tools, offline_claude_client(portkey),
                               Telemetry(ListSink()), RuntimeSettings())
    return workflow.run(BID_QUESTION, execute=False, request_id=request_id)


def test_valid_sql_is_approved_through_mcp_tools(mcp_tools, server_audit):
    result = run(mcp_tools, FakePortkey(sql_draft(BID_SQL, {"fiscal_year": 2024})), "wf_mcp_valid")
    assert result.status == WorkflowStatus.VALIDATED_NOT_EXECUTED and result.validation.approved
    events = [json.loads(line) for line in server_audit.read_text().splitlines()]
    tools_used = {e["event_type"] for e in events if e["request_id"] == "wf_mcp_valid"}
    assert {"search_table_registry", "get_table_schema", "get_table_relationships", "get_metric_definition",
            "get_verified_sql_examples", "validate_sql"} <= tools_used
    assert "execute_readonly_sql" not in tools_used


def test_unauthorized_sql_is_denied_through_mcp_tools(mcp_tools):
    result = run(mcp_tools, FakePortkey(sql_draft(f"SELECT project_id FROM {SILVER}.projects LIMIT 5")), "wf_mcp_silver")
    assert result.status == WorkflowStatus.REJECTED and result.failure_class == FailureClass.AUTHORIZATION_DENIED


def test_only_approved_tools_can_be_called(mcp_tools):
    with pytest.raises(ValueError):
        mcp_tools.call("drop_everything", {}, "wf_mcp_unknown")


def test_server_audit_contains_no_credentials_or_question_text(server_audit):
    text = server_audit.read_text()
    assert TEST_API_KEY not in text and BID_QUESTION not in text and "SELECT" not in text
