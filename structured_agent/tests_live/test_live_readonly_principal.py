"""C2 live check: the read-only Databricks service principal, end to end (opt-in; no LLM; read-only).

Run with the MCP environment, from procurement-intelligence/:
    WBG_DATABRICKS_LIVE_TESTS=1 WBG_DATABRICKS_PROFILE=wbg-readonly \
        .venv-structured-mcp/bin/python -m pytest structured_agent/tests_live -q -p no:cacheprovider

Two independent boundaries are checked:
  * the application layer: approved Gold SELECTs run through the existing gateway in `read_only_principal` mode, and
    every rejected-SQL case of the MCP security suite is refused before reaching Databricks;
  * the database layer: with the validator bypassed, Databricks itself refuses Silver reads, writes and DDL for this
    identity. Write probes are no-ops even if allowed (`WHERE 1 = 0`), and the probed table's Delta version is
    compared before and after.
Audit events go to a temporary file, never to the project audit log.
"""
from __future__ import annotations

import dataclasses
import os

import pytest

from structured_agent.config import Settings
from structured_agent.contracts import QueryResult
from structured_agent.execution.executor import DatabricksSparkExecutor
from structured_agent.service import RequestContext, StructuredDataService
from structured_agent.tests_mcp.test_mcp_security import CONTRACT_SQL, GOLD, REJECTED, SILVER

PROFILE = "wbg-readonly"
PROBE_TABLE = f"{GOLD}.gold_country_context"

pytestmark = pytest.mark.skipif(
    os.environ.get("WBG_DATABRICKS_LIVE_TESTS") != "1" or os.environ.get("WBG_DATABRICKS_PROFILE") != PROFILE,
    reason=f"live read-only checks need WBG_DATABRICKS_LIVE_TESTS=1 and WBG_DATABRICKS_PROFILE={PROFILE}")


@pytest.fixture(scope="module")
def executor() -> DatabricksSparkExecutor:
    return DatabricksSparkExecutor(PROFILE)


@pytest.fixture(scope="module")
def service(executor, tmp_path_factory) -> StructuredDataService:
    settings = dataclasses.replace(Settings(), databricks_profile=PROFILE, allow_dev_owner_credentials=False,
                                   audit_log_path=tmp_path_factory.mktemp("audit") / "audit.jsonl")
    return StructuredDataService.from_environment(settings, executor=executor)


@pytest.fixture(scope="module")
def analyst() -> RequestContext:
    return RequestContext("analyst-demo", "wbg-procurement-poc", "c2_live")


def delta_version(executor: DatabricksSparkExecutor, table: str) -> int:
    return executor.execute(f"DESCRIBE HISTORY {table} LIMIT 1", {}, 60).rows[0]["version"]


# --------------------------------------------------------------------------------------------- identity
def test_the_identity_is_a_service_principal_without_write_privileges(executor):
    user = executor.execute("SELECT current_user() AS u", {}, 60).rows[0]["u"]
    assert "@" not in user                                               # not a human login
    check = executor.check_credential()
    assert check.write_capable is False, check.evidence


# ------------------------------------------------------------------------------------- application layer
def test_approved_gold_queries_run_in_read_only_mode(service, analyst):
    result = service.execute_readonly_sql(analyst, sql=CONTRACT_SQL, parameters={"contract": "1803814"})
    assert isinstance(result, QueryResult) and result.status == "ok" and result.row_count == 1, result.errors
    assert result.provenance.credential_mode == "read_only_principal" and result.provenance.production_read_only is True
    kpi = service.execute_readonly_sql(analyst, sql=f"SELECT country_name, contract_awards FROM {GOLD}.vw_kpi_country_comparison "
                                                    "ORDER BY contract_awards DESC LIMIT 5")
    assert kpi.status == "ok" and kpi.row_count >= 1, kpi.errors


@pytest.mark.parametrize("name, sql, code", REJECTED, ids=[r[0] for r in REJECTED])
def test_rejected_sql_never_reaches_databricks(service, analyst, name, sql, code):
    result = service.execute_readonly_sql(analyst, sql=sql)
    assert result.status == "rejected" and code in {e.code for e in result.errors}
    assert result.query_id is None and result.provenance is None


# ---------------------------------------------------------------------------------------- database layer
@pytest.mark.parametrize("name, sql", [
    ("silver_read", f"SELECT project_id FROM {SILVER}.projects LIMIT 1"),
    ("delete_no_op", f"DELETE FROM {PROBE_TABLE} WHERE 1 = 0"),
    ("update_no_op", f"UPDATE {PROBE_TABLE} SET country_name = country_name WHERE 1 = 0"),
    ("create_table", f"CREATE TABLE {GOLD}.c2_readonly_probe_must_not_exist (x INT)"),
    ("grant", f"GRANT SELECT ON SCHEMA {SILVER} TO `account users`"),
])
def test_databricks_itself_refuses_what_the_validator_blocks(executor, name, sql):
    assert executor.check_credential().write_capable is False                # never probe with a writable identity
    before = delta_version(executor, PROBE_TABLE)
    with pytest.raises(Exception) as refused:
        executor.execute(sql, {}, 60)
    message = str(refused.value).upper()
    assert any(word in message for word in ("PERMISSION", "PRIVILEGE", "NOT_FOUND", "DOES NOT HAVE", "UNAUTHORIZED")), \
        message[:300]
    assert delta_version(executor, PROBE_TABLE) == before
