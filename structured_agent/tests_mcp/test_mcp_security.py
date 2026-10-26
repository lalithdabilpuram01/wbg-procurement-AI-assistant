"""Security, execution, concurrency and audit behaviour through the real MCP stdio transport.

Execution goes MCP client -> server process -> service contract + authorization -> SQL validator -> gateway ->
Databricks -> result validation -> MCP response. Executed results are compared with trusted Silver baselines run by
this test process directly. The final test checks that no Databricks table or object changed during the run.
"""
from __future__ import annotations

import pytest

from structured_agent.contracts import QueryResult, ValidateSqlResponse
from structured_agent.evaluation.run_evaluation import compare_rows
from structured_agent.execution.executor import to_json_value
from structured_agent.tests_mcp.conftest import databricks_state, read_audit

GOLD = "workspace.wbg_gold"
SILVER = "workspace.wbg_silver"
CONTRACT_SQL = (f"SELECT wb_contract_number, procurement_method, contract_amount_usd FROM {GOLD}.gold_contract_analytics "
                "WHERE wb_contract_number = :contract LIMIT 5")
SENT_REQUEST_IDS: list[str] = []   # every request id sent to the analyst server, checked against its audit log


def request_id(name: str) -> str:
    SENT_REQUEST_IDS.append(f"sec_{name}")
    return f"sec_{name}"


def baseline(spark, sql: str) -> list[dict]:
    return [{k: to_json_value(v) for k, v in row.asDict().items()} for row in spark.sql(sql).collect()]


# ---------------------------------------------------------------------------------------------- valid execution
def test_valid_select_executes_and_matches_the_silver_baseline(analyst, spark):
    result = QueryResult.model_validate(analyst.call("execute_readonly_sql", sql=CONTRACT_SQL, parameters={"contract": "1803814"},
                                                     request_id=request_id("contract_lookup")))
    assert result.status == "ok" and result.row_count == 1 and result.query_id.startswith("qry_")
    assert result.provenance.credential_mode == "dev_owner_credentials" and result.provenance.production_read_only is False
    assert all(c.passed for c in result.validation_checks + result.result_checks)
    expected = baseline(spark, f"SELECT wb_contract_number, procurement_method, contract_amount_usd FROM {SILVER}.contract_awards "
                               "WHERE wb_contract_number = '1803814'")
    matched, detail = compare_rows(result.rows, expected, "wb_contract_number", ["procurement_method", "contract_amount_usd"])
    assert matched, detail


def test_reviewed_template_aggregate_matches_the_silver_baseline(analyst, spark):
    examples = analyst.call("get_verified_sql_examples", query="award value by procurement method", limit=10,
                            request_id=request_id("examples"))["examples"]
    template = next(e for e in examples if e["example_id"] == "award_value_by_method")
    result = analyst.call("execute_readonly_sql", sql=template["sql_text"], request_id=request_id("award_by_method"))
    expected = baseline(spark, f"SELECT procurement_method, count(*) AS contract_awards, sum(contract_amount_usd) AS contract_award_value_usd "
                               f"FROM {SILVER}.contract_awards GROUP BY procurement_method")
    matched, detail = compare_rows(result["rows"], expected, "procurement_method", ["contract_awards", "contract_award_value_usd"])
    assert result["status"] == "ok" and matched, detail


def test_validate_sql_returns_a_full_report(analyst):
    response = ValidateSqlResponse.model_validate(
        analyst.call("validate_sql", sql=CONTRACT_SQL, parameters={"contract": "1803814"}, request_id=request_id("validate_ok")))
    assert response.report.approved and len(response.report.checks) == 12 and response.report.validated_sql.endswith("LIMIT 5")


# ------------------------------------------------------------------------------------------------ rejected SQL
REJECTED = [
    ("silver_by_analyst", f"SELECT project_id FROM {SILVER}.projects LIMIT 5", "UNAUTHORIZED_OBJECT"),
    ("bronze", "SELECT project_id FROM workspace.wbg_bronze.bronze_projects_v3 LIMIT 5", "UNAUTHORIZED_OBJECT"),
    ("nested_silver", f"SELECT project_id FROM {GOLD}.gold_project_portfolio WHERE project_id IN (SELECT project_id FROM {SILVER}.projects) LIMIT 5",
     "UNAUTHORIZED_OBJECT"),
    ("unknown_table", f"SELECT x FROM {GOLD}.gold_supplier_rankings LIMIT 5", "UNKNOWN_TABLE"),
    ("unknown_column", f"SELECT contract_value FROM {GOLD}.gold_contract_analytics LIMIT 5", "UNKNOWN_COLUMN"),
    ("drop", f"DROP TABLE {GOLD}.gold_project_portfolio", "FORBIDDEN_OPERATION"),
    ("delete", f"DELETE FROM {GOLD}.gold_project_portfolio WHERE 1 = 1", "FORBIDDEN_OPERATION"),
    ("update", f"UPDATE {GOLD}.gold_project_portfolio SET project_name = 'x'", "FORBIDDEN_OPERATION"),
    ("stacked", f"SELECT project_id FROM {GOLD}.gold_project_portfolio LIMIT 1; DROP TABLE {GOLD}.gold_project_portfolio",
     "MULTIPLE_STATEMENTS"),
    ("insert_in_cte", f"WITH a AS (SELECT project_id FROM {GOLD}.gold_project_portfolio) INSERT INTO {GOLD}.gold_project_portfolio "
                      "SELECT project_id FROM a", "FORBIDDEN_OPERATION"),
    ("nested_unsafe_function", f"SELECT project_id FROM {GOLD}.gold_project_portfolio WHERE project_id IN "
                               "(SELECT reflect('java.lang.Runtime', 'getRuntime')) LIMIT 5", "FORBIDDEN_FUNCTION"),
    ("row_limit", f"SELECT notice_op_id FROM {GOLD}.gold_procurement_notices LIMIT 50000", "LIMIT_EXCEEDED"),
    ("fan_out_join", f"SELECT sum(c.contract_amount_usd) AS v FROM {GOLD}.gold_contract_analytics AS c "
                     f"JOIN {GOLD}.gold_financing_analytics AS f ON c.project_id = f.project_id LIMIT 5", "FAN_OUT_RISK"),
]


@pytest.mark.parametrize("name, sql, code", REJECTED, ids=[r[0] for r in REJECTED])
def test_rejected_sql_never_reaches_databricks(analyst, name, sql, code):
    result = analyst.call("execute_readonly_sql", sql=sql, request_id=request_id(f"reject_{name}"))
    assert result["status"] == "rejected" and code in {e["code"] for e in result["errors"]}
    # The gateway creates a query id only immediately before calling Databricks: none means it never ran.
    assert result["query_id"] is None and result["row_count"] == 0 and result["provenance"] is None


def test_steward_has_restricted_silver_access(steward):
    allowed = steward.call("execute_readonly_sql", sql=f"SELECT count(*) AS projects FROM {SILVER}.projects LIMIT 1")
    assert allowed["status"] == "ok" and allowed["rows"] == [{"projects": 2000}]
    prohibited = steward.call("execute_readonly_sql", sql=f"SELECT supplier_name FROM {SILVER}.contract_award_lines LIMIT 5")
    assert prohibited["errors"][0]["code"] == "PROHIBITED_COLUMN" and prohibited["query_id"] is None


def test_impersonation_through_arguments_is_rejected_and_audited(analyst, audit_dir):
    raw = analyst.raw_call("execute_readonly_sql", {"sql": f"SELECT project_id FROM {SILVER}.projects LIMIT 1",
                                                    "principal_id": "steward-demo", "request_id": request_id("impersonation")})
    assert raw.isError and "Unexpected keyword argument" in raw.content[0].text


# ---------------------------------------------------------------------------------------- default deny, timeout
def test_write_capable_credential_is_refused_without_the_dev_flag(analyst_default_deny):
    result = analyst_default_deny.call("execute_readonly_sql", sql=CONTRACT_SQL, parameters={"contract": "1803814"})
    assert result["status"] == "error" and result["errors"][0]["code"] == "CREDENTIAL_NOT_READ_ONLY"
    assert result["rows"] == [] and result["provenance"] is None


def test_query_timeout_returns_a_typed_error_and_the_server_recovers(analyst_short_timeout):
    slow_sql = (f"SELECT notice_type, procurement_method, count(*) AS notices, avg(published_response_window_days) AS avg_window "
                f"FROM {GOLD}.gold_procurement_notices GROUP BY notice_type, procurement_method LIMIT 100")
    result = analyst_short_timeout.call("execute_readonly_sql", sql=slow_sql)
    assert result["status"] == "error" and result["errors"][0]["code"] == "EXECUTION_TIMEOUT" and result["rows"] == []
    assert analyst_short_timeout.call("get_metric_definition", metric_id="project_count")["status"] == "ok"


# ------------------------------------------------------------------------------------------------- concurrency
def test_concurrent_read_only_requests(analyst):
    contracts = ["1803814", "1727396", "1675726"]
    # Request ids are logged verbatim, so they are opaque labels and never contain business values.
    calls = [("execute_readonly_sql", {"sql": CONTRACT_SQL, "parameters": {"contract": c}, "request_id": request_id(f"concurrent_{i}")})
             for i, c in enumerate(contracts)]
    calls += [("search_table_registry", {"query": "tender notices by category", "request_id": request_id("concurrent_search")}),
              ("validate_sql", {"sql": f"DROP TABLE {GOLD}.gold_project_portfolio", "request_id": request_id("concurrent_drop")})]
    results = analyst.call_concurrently(calls)

    for (tool, arguments), result in zip(calls, results):
        assert result["request_id"] == arguments["request_id"]
    executed = results[:3]
    assert [r["rows"][0]["wb_contract_number"] for r in executed] == contracts
    assert all(r["status"] == "ok" and r["provenance"]["credential_mode"] == "dev_owner_credentials" for r in executed)
    assert results[3]["status"] == "ok" and results[4]["status"] == "rejected"


# ------------------------------------------------------------------------------------------------------- audit
def test_every_mcp_request_is_audited_without_sensitive_content(analyst, audit_dir):
    events = read_audit(audit_dir / "analyst.jsonl")
    by_request = {}
    for event in events:
        by_request.setdefault(event["request_id"], []).append(event)

    for sent in SENT_REQUEST_IDS:
        assert len(by_request.get(sent, [])) == 1, f"expected one audit event for {sent}"
        event = by_request[sent][0]
        attributes = event["attributes"]
        assert event["invocation_id"].startswith("inv_") and event["event_type"] == attributes["tool"]
        assert {"status", "duration_ms", "error_codes", "authorization", "principal_hash"} <= attributes.keys()
        if attributes["tool"] in ("validate_sql", "execute_readonly_sql"):
            assert attributes["validation_status"] in ("approved", "rejected", "not_run")
        if attributes.get("query_id"):
            assert attributes["credential_mode"] == "dev_owner_credentials" and attributes["production_read_only"] is False
        if sent.startswith("sec_reject_"):
            assert attributes["error_codes"] and "query_id" not in attributes

    assert by_request["sec_reject_silver_by_analyst"][0]["attributes"]["authorization"] == "denied"
    assert by_request["sec_reject_drop"][0]["attributes"]["authorization"] == "not_evaluated"
    assert by_request["sec_contract_lookup"][0]["attributes"]["authorization"] == "granted"
    assert by_request["sec_impersonation"][0]["attributes"]["error_codes"] == ["INVALID_TOOL_ARGUMENTS"]

    text = (audit_dir / "analyst.jsonl").read_text()
    for forbidden in ["SELECT", "DROP", "1803814", "analyst-demo", "steward-demo", "67629166", "Request for Bids", "@"]:
        assert forbidden not in text, f"audit log contains {forbidden!r}"


# --------------------------------------------------------------------------------------------- Databricks state
def test_databricks_is_unchanged_after_all_mcp_tests(spark, databricks_state_at_start):
    """Runs last in this file: every Silver/Gold table version and the object list equal the state at session start."""
    assert databricks_state(spark) == databricks_state_at_start
