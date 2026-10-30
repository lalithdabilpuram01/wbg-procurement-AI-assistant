"""Integration tests against the local PostgreSQL registry and the MCP server.

Skipped when the registry database is not reachable. Databricks is replaced by a test-double executor, so these tests
never run SQL against Databricks.

MCP transport limitation: every MCP transport (in-memory, stdio, HTTP) uses mcp.shared.session, which needs
anyio >= 4.5; this environment has anyio 3.7.1 (a pre-existing conflict reported by `pip check`). The MCP tests
therefore call the registered FastMCP tools in-process (tool schema + handler), without a transport.
"""
from __future__ import annotations

import asyncio

import psycopg
import pytest

from structured_agent.config import load_settings
from structured_agent.registry.store import RegistryStore
from tests.structured_agent.test_gateway_and_generation import FakeExecutor

SETTINGS = load_settings()
EXPECTED_TOOLS = {"search_table_registry", "get_table_schema", "get_table_relationships", "get_metric_definition",
                  "get_verified_sql_examples", "validate_sql", "execute_readonly_sql"}


def registry_available() -> bool:
    try:
        with psycopg.connect(SETTINGS.registry_dsn, connect_timeout=3):
            return True
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not registry_available(), reason="registry database not reachable")


@pytest.fixture(scope="module")
def service():
    from structured_agent.service import StructuredDataService

    return StructuredDataService.from_environment(executor=FakeExecutor(write_capable=True))


def call(server, tool: str, **arguments):
    """Run one registered MCP tool in-process (FastMCP argument validation + handler)."""
    async def run():
        tools = await server.get_tools()
        return (await tools[tool].run(arguments)).structured_content
    return asyncio.run(run())


# ------------------------------------------------------------------------------------------------------- registry
def test_registry_tables_are_populated():
    counts = RegistryStore(SETTINGS.registry_dsn).table_counts()
    assert counts["registered_objects"] == 30 and counts["relationships"] == 21
    assert counts["description_embeddings"] > 1000 and counts["audit_events"] == 0


def test_runtime_role_cannot_write_to_the_registry():
    with psycopg.connect(SETTINGS.registry_dsn) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("DELETE FROM registry.metrics")


def test_embeddings_have_one_model_and_dimension():
    documents, index = RegistryStore(SETTINGS.registry_dsn).load_documents_and_index()
    assert index.model_id == SETTINGS.embedding_model and index.dimensions == 768 and len(documents) > 1000


# ------------------------------------------------------------------------------------------------------ MCP server
def test_server_exposes_exactly_the_seven_operations(service):
    from structured_agent.mcp_server import build_server

    tools = asyncio.run(build_server(service, "analyst-demo", "wbg-procurement-poc").get_tools())
    assert set(tools) == EXPECTED_TOOLS
    assert all("principal" not in name for tool in tools.values() for name in tool.parameters["properties"])


def test_mcp_validate_rejects_destructive_sql(service):
    from structured_agent.mcp_server import build_server

    server = build_server(service, "analyst-demo", "wbg-procurement-poc")
    result = call(server, "validate_sql", sql="DROP TABLE workspace.wbg_gold.gold_project_portfolio", request_id="req_test_1")
    assert result["status"] == "rejected" and result["request_id"] == "req_test_1" and result["invocation_id"].startswith("inv_")
    assert result["errors"][0]["code"] == "FORBIDDEN_OPERATION"


def test_mcp_execution_refuses_write_capable_credentials_by_default(service):
    from structured_agent.mcp_server import build_server

    server = build_server(service, "analyst-demo", "wbg-procurement-poc")
    sql = "SELECT project_id, notice_count FROM workspace.wbg_gold.gold_project_portfolio WHERE project_id = :p LIMIT 5"
    refused = call(server, "execute_readonly_sql", sql=sql, parameters={"p": "P1"})
    assert refused["errors"][0]["code"] == "CREDENTIAL_NOT_READ_ONLY"


def test_unknown_principal_is_refused(service):
    from structured_agent.mcp_server import build_server

    server = build_server(service, "someone-else", "wbg-procurement-poc")
    result = call(server, "get_table_schema", object_fqn="workspace.wbg_gold.gold_project_portfolio")
    assert result["errors"][0]["code"] == "UNAUTHORIZED_PRINCIPAL"


def test_invalid_request_contract_is_a_typed_error(service):
    result = service.search_table_registry(_ctx(), query="x", top_k=50)
    assert result.status == "error" and result.errors[0].code.value == "INVALID_REQUEST"


def test_schema_hides_columns_the_principal_cannot_use(service):
    analyst = service.get_table_schema(_ctx(), object_fqn="workspace.wbg_gold.gold_contract_analytics")
    names = {c.name for c in analyst.columns}
    assert "contract_amount_usd" in names and "bronze_source_file" not in names and analyst.hidden_column_count > 0
    silver = service.get_table_schema(_ctx(), object_fqn="workspace.wbg_silver.contract_award_lines")
    assert silver.status == "rejected" and silver.errors[0].code.value == "UNAUTHORIZED_OBJECT"


def test_ambiguous_metric_asks_for_clarification(service):
    result = service.get_metric_definition(_ctx(), query="What is the total value for Ethiopia?")
    assert result.status == "needs_clarification" and result.ambiguity.group == "monetary_value"


def _ctx():
    from structured_agent.service import RequestContext

    return RequestContext("analyst-demo", "wbg-procurement-poc")
