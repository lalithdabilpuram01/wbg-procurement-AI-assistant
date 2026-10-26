"""Positive and negative tests of the SQL Execution Validator (no database needed)."""
from __future__ import annotations

import json

import pytest

from structured_agent.config import QueryLimits
from structured_agent.contracts import ErrorCode
from structured_agent.metadata.sources import CURATED_DIR
from structured_agent.sql_validation.validator import SqlValidator

GOLD = "workspace.wbg_gold"
SILVER = "workspace.wbg_silver"
EXAMPLES = json.loads((CURATED_DIR / "verified_sql_examples.json").read_text())["examples"]


def codes(report) -> set[ErrorCode]:
    return {e.code for e in report.errors}


# ------------------------------------------------------------------------------------------------------------ positive
@pytest.mark.parametrize("example", EXAMPLES, ids=[e["example_id"] for e in EXAMPLES])
def test_reviewed_examples_are_approved_for_analyst(validator, analyst, example):
    report = validator.validate(example["sql"], example["parameters"], analyst)
    assert report.approved, report.errors
    assert report.validated_sql and "LIMIT" in report.validated_sql


def test_missing_limit_is_added_not_broadened(validator, analyst):
    report = validator.validate(f"SELECT project_id FROM {GOLD}.gold_project_portfolio", {}, analyst)
    assert report.approved
    assert report.applied_rewrites == ["Added LIMIT 1000"]


def test_steward_may_query_silver_and_filter_on_restricted_reference(validator, steward):
    sql = (f"SELECT count(*) AS contracts FROM {SILVER}.contract_awards "
           f"WHERE borrower_contract_reference IS NOT NULL LIMIT 1")
    assert validator.validate(sql, {}, steward).approved


def test_preaggregated_join_of_two_facts_is_approved(validator, analyst):
    sql = f"""WITH n AS (SELECT project_id, count(*) AS notices FROM {GOLD}.gold_procurement_notices GROUP BY project_id)
              SELECT c.project_id, count(*) AS contracts, max(n.notices) AS notices
              FROM {GOLD}.gold_contract_analytics AS c JOIN n ON c.project_id = n.project_id GROUP BY c.project_id LIMIT 10"""
    report = validator.validate(sql, {}, analyst)
    assert report.approved, report.errors


def test_country_context_join_with_explicit_year_is_approved(validator, analyst):
    sql = f"""SELECT p.project_id, k.gdp_growth_annual_pct FROM {GOLD}.gold_project_portfolio AS p
              JOIN {GOLD}.gold_country_context AS k ON p.wdi_country_code = k.country_code AND k.year = :year LIMIT 10"""
    assert validator.validate(sql, {"year": 2020}, analyst).approved


def test_output_units_follow_cte_aggregates(validator, analyst):
    example = next(e for e in EXAMPLES if e["example_id"] == "country_procurement_profile")
    report = validator.validate(example["sql"], {}, analyst)
    units = {c.name: c.unit for c in report.output_columns}
    assert units["contract_awards"] == "count" and units["contract_award_value_usd"] == "USD"


# ------------------------------------------------------------------------------------------- destructive and bypasses
@pytest.mark.parametrize("sql, expected", [
    (f"DROP TABLE {GOLD}.gold_project_portfolio", ErrorCode.FORBIDDEN_OPERATION),
    (f"DELETE FROM {GOLD}.gold_project_portfolio WHERE 1 = 1", ErrorCode.FORBIDDEN_OPERATION),
    (f"UPDATE {GOLD}.gold_project_portfolio SET project_name = 'x'", ErrorCode.FORBIDDEN_OPERATION),
    (f"INSERT INTO {GOLD}.gold_project_portfolio SELECT * FROM {GOLD}.gold_project_portfolio", ErrorCode.FORBIDDEN_OPERATION),
    (f"MERGE INTO {GOLD}.gold_project_portfolio t USING {GOLD}.gold_project_portfolio s ON t.project_id = s.project_id "
     "WHEN MATCHED THEN DELETE", ErrorCode.FORBIDDEN_OPERATION),
    (f"CREATE TABLE {GOLD}.x AS SELECT project_id FROM {GOLD}.gold_project_portfolio", ErrorCode.FORBIDDEN_OPERATION),
    (f"TRUNCATE TABLE {GOLD}.gold_project_portfolio", ErrorCode.FORBIDDEN_OPERATION),
    (f"GRANT SELECT ON {GOLD}.gold_project_portfolio TO `someone`", ErrorCode.FORBIDDEN_OPERATION),
    ("SET spark.sql.ansi.enabled = false", ErrorCode.FORBIDDEN_OPERATION),
    (f"OPTIMIZE {GOLD}.gold_project_portfolio", ErrorCode.FORBIDDEN_OPERATION),
    (f"SELECT project_id INTO {GOLD}.copy FROM {GOLD}.gold_project_portfolio", ErrorCode.FORBIDDEN_OPERATION),
    (f"WITH a AS (SELECT project_id FROM {GOLD}.gold_project_portfolio) INSERT INTO {GOLD}.x SELECT project_id FROM a",
     ErrorCode.FORBIDDEN_OPERATION),
    (f"SELECT project_id FROM {GOLD}.gold_project_portfolio; DROP TABLE {GOLD}.gold_project_portfolio", ErrorCode.MULTIPLE_STATEMENTS),
    (f"SeLeCt project_id FROM {GOLD}.gold_project_portfolio; dRoP TaBlE {GOLD}.gold_project_portfolio", ErrorCode.MULTIPLE_STATEMENTS),
])
def test_destructive_and_multi_statement_sql_is_rejected(validator, analyst, sql, expected):
    report = validator.validate(sql, {}, analyst)
    assert not report.approved and report.validated_sql is None
    assert expected in codes(report)


@pytest.mark.parametrize("sql, expected", [
    ("SELECT reflect('java.lang.Runtime', 'getRuntime') AS x", ErrorCode.FORBIDDEN_FUNCTION),
    ("SELECT path FROM read_files('/Volumes/workspace/x')", ErrorCode.FORBIDDEN_FUNCTION),
    ("SELECT ai_query('model', 'prompt') AS x", ErrorCode.FORBIDDEN_FUNCTION),
    (f"SELECT project_id FROM {GOLD}.gold_project_portfolio WHERE project_id IN "
     "(SELECT java_method('java.lang.System', 'exit', 0))", ErrorCode.FORBIDDEN_FUNCTION),
    (f"SELECT project_id FROM {GOLD}.gold_project_portfolio VERSION AS OF 1", ErrorCode.UNSUPPORTED_CONSTRUCT),
    (f"SELECT project_id FROM {GOLD}.gold_project_portfolio UNION SELECT project_id FROM {SILVER}.projects",
     ErrorCode.NOT_SELECT),
    (f"WITH u AS (SELECT project_id FROM {GOLD}.gold_project_portfolio UNION SELECT project_id FROM {SILVER}.projects) "
     "SELECT project_id FROM u", ErrorCode.UNSUPPORTED_CONSTRUCT),
    (f"SELECT * FROM {GOLD}.gold_project_portfolio", ErrorCode.UNSUPPORTED_CONSTRUCT),
    (f"SELECT project_id FROM {GOLD}.gold_project_portfolio WHERE project_id = ?", ErrorCode.PARAMETER_ERROR),
    (f"SELECT ${{x}} FROM {GOLD}.gold_project_portfolio", ErrorCode.UNSUPPORTED_CONSTRUCT),
    ("SELECT x FROM delta.`/tmp/table`", ErrorCode.UNKNOWN_TABLE),
])
def test_unsafe_functions_and_constructs_are_rejected(validator, analyst, sql, expected):
    report = validator.validate(sql, {}, analyst)
    assert not report.approved and expected in codes(report)


def test_literal_containing_sql_text_is_data_not_a_statement(validator, analyst):
    sql = f"SELECT project_id FROM {GOLD}.gold_project_portfolio WHERE project_name = '; DROP TABLE x; --' LIMIT 5"
    report = validator.validate(sql, {}, analyst)
    assert report.approved and "DROP" not in report.validated_sql.split("'")[0]


def test_comment_hidden_statement_is_not_executed(validator, analyst):
    sql = f"SELECT project_id FROM {GOLD}.gold_project_portfolio LIMIT 5 -- ; DROP TABLE {GOLD}.gold_project_portfolio"
    report = validator.validate(sql, {}, analyst)
    assert report.approved and "DROP" not in report.validated_sql.upper().split("/*")[0]


# ------------------------------------------------------------------------------------------------ registry and policy
def test_unknown_table_and_column(validator, analyst):
    unknown_table = validator.validate(f"SELECT project_id FROM {GOLD}.gold_supplier_rankings", {}, analyst)
    assert ErrorCode.UNKNOWN_TABLE in codes(unknown_table)
    unknown_column = validator.validate(f"SELECT contract_value FROM {GOLD}.gold_contract_analytics", {}, analyst)
    assert ErrorCode.UNKNOWN_COLUMN in codes(unknown_column) and unknown_column.errors[0].recoverable


@pytest.mark.parametrize("sql", [
    f"SELECT project_id FROM {SILVER}.projects",                                          # Silver: steward only
    "SELECT project_id FROM workspace.wbg_bronze.bronze_projects_v3",                    # Bronze: never registered
    "SELECT table_name FROM workspace.information_schema.tables",                        # catalog metadata
    f"SELECT project_id FROM {GOLD}.gold_project_portfolio WHERE project_id IN (SELECT project_id FROM {SILVER}.projects)",
])
def test_unauthorized_objects_are_rejected_even_when_nested(validator, analyst, sql):
    report = validator.validate(sql, {}, analyst)
    assert not report.approved and ErrorCode.UNAUTHORIZED_OBJECT in codes(report)


def test_principal_without_roles_cannot_query_anything(validator, guest):
    report = validator.validate(f"SELECT project_id FROM {GOLD}.gold_project_portfolio LIMIT 1", {}, guest)
    assert ErrorCode.UNAUTHORIZED_OBJECT in codes(report)


@pytest.mark.parametrize("sql", [
    f"SELECT supplier_name FROM {SILVER}.contract_award_lines LIMIT 5",
    f"SELECT count(*) AS n FROM {SILVER}.contract_award_lines WHERE supplier_name LIKE '%x%'",
    f"SELECT notice_text FROM {SILVER}.procurement_notice_details LIMIT 1",
    f"WITH d AS (SELECT contract_description AS d FROM {SILVER}.contract_awards) SELECT d FROM d LIMIT 5",
])
def test_prohibited_columns_are_rejected_for_every_role(validator, steward, sql):
    report = validator.validate(sql, {}, steward)
    assert not report.approved and ErrorCode.PROHIBITED_COLUMN in codes(report)


def test_restricted_identifier_cannot_be_output_even_aggregated(validator, steward):
    report = validator.validate(f"SELECT count(DISTINCT supplier_id) AS n FROM {SILVER}.contract_award_lines", {}, steward)
    assert ErrorCode.UNAUTHORIZED_COLUMN in codes(report)


def test_lineage_columns_are_not_available_to_analysts(validator, analyst):
    report = validator.validate(f"SELECT bronze_source_file FROM {GOLD}.gold_contract_analytics LIMIT 1", {}, analyst)
    assert ErrorCode.UNAUTHORIZED_COLUMN in codes(report)


# ------------------------------------------------------------------------------------------- joins and double counting
def test_fact_to_fact_join_is_rejected_as_fan_out(validator, analyst):
    sql = f"""SELECT sum(c.contract_amount_usd) AS v FROM {GOLD}.gold_contract_analytics AS c
              JOIN {GOLD}.gold_financing_analytics AS f ON c.project_id = f.project_id WHERE f.lender = 'IDA'"""
    report = validator.validate(sql, {}, analyst)
    assert ErrorCode.FAN_OUT_RISK in codes(report)


def test_two_facts_through_a_shared_parent_are_incompatible_grains(validator, analyst):
    sql = f"""SELECT p.project_id, count(*) AS n FROM {GOLD}.gold_contract_analytics AS c
              JOIN {GOLD}.gold_project_portfolio AS p ON c.project_id = p.project_id
              JOIN {GOLD}.gold_financing_analytics AS f ON f.project_id = p.project_id GROUP BY p.project_id"""
    report = validator.validate(sql, {}, analyst)
    assert ErrorCode.INCOMPATIBLE_GRAIN in codes(report)


def test_summing_the_one_side_after_a_one_to_many_join_is_rejected(validator, analyst):
    sql = f"""SELECT sum(p.current_total_commitment_usd_snapshot) AS v FROM {GOLD}.gold_contract_analytics AS c
              JOIN {GOLD}.gold_project_portfolio AS p ON c.project_id = p.project_id"""
    report = validator.validate(sql, {}, analyst)
    assert ErrorCode.FAN_OUT_RISK in codes(report)


def test_fan_out_hidden_in_a_cte_is_still_detected(validator, analyst):
    sql = f"""WITH joined AS (SELECT p.current_total_commitment_usd_snapshot AS commitment FROM {GOLD}.gold_contract_analytics AS c
                             JOIN {GOLD}.gold_project_portfolio AS p ON c.project_id = p.project_id)
              SELECT sum(commitment) AS v FROM joined"""
    report = validator.validate(sql, {}, analyst)
    assert ErrorCode.FAN_OUT_RISK in codes(report)


@pytest.mark.parametrize("sql", [
    f"SELECT c.project_id FROM {GOLD}.gold_contract_analytics AS c CROSS JOIN {GOLD}.gold_project_portfolio AS p",
    f"SELECT c.project_id FROM {GOLD}.gold_contract_analytics AS c JOIN {GOLD}.gold_project_portfolio AS p ON c.project_name = p.project_name",
    f"SELECT c.project_id FROM {GOLD}.gold_contract_analytics AS c JOIN {GOLD}.gold_project_portfolio AS p ON c.contract_fiscal_year > p.approval_fiscal_year",
])
def test_unapproved_joins_are_rejected(validator, analyst, sql):
    report = validator.validate(sql, {}, analyst)
    assert ErrorCode.UNAPPROVED_JOIN in codes(report)


def test_country_context_join_without_year_is_rejected(validator, analyst):
    sql = f"""SELECT p.project_id, k.gdp_growth_annual_pct FROM {GOLD}.gold_project_portfolio AS p
              JOIN {GOLD}.gold_country_context AS k ON p.wdi_country_code = k.country_code"""
    report = validator.validate(sql, {}, analyst)
    assert ErrorCode.MISSING_REQUIRED_FILTER in codes(report)


# ------------------------------------------------------------------------------------------------- business rules
def test_subtotal_view_requires_one_grouping_level(validator, analyst):
    report = validator.validate(f"SELECT sum(contract_awards) AS n FROM {GOLD}.vw_kpi_contract_value_distribution", {}, analyst)
    assert ErrorCode.MISSING_REQUIRED_FILTER in codes(report)


def test_sum_of_percentiles_is_rejected(validator, analyst):
    sql = f"SELECT sum(p50_contract_value_usd) AS x FROM {GOLD}.vw_kpi_contract_value_distribution WHERE grouping_level = 'category'"
    assert ErrorCode.NON_ADDITIVE_AGGREGATE in codes(validator.validate(sql, {}, analyst))


def test_contract_values_and_financing_are_never_added(validator, analyst):
    sql = f"""SELECT contract_award_amount_usd + current_total_commitment_usd_snapshot AS total
              FROM {GOLD}.gold_project_portfolio LIMIT 5"""
    assert ErrorCode.MIXED_MEASURE_FAMILIES in codes(validator.validate(sql, {}, analyst))


# --------------------------------------------------------------------------------------------------- limits and budget
def test_limit_above_maximum_is_rejected(validator, analyst):
    report = validator.validate(f"SELECT project_id FROM {GOLD}.gold_project_portfolio LIMIT 50000", {}, analyst)
    assert ErrorCode.LIMIT_EXCEEDED in codes(report) and report.errors[0].details["maximum"] == 1000


def test_complexity_budget(bundle, policy, analyst):
    strict = SqlValidator(bundle, policy, QueryLimits(max_joins=0))
    example = next(e for e in EXAMPLES if e["example_id"] == "contracts_by_ieg_rating")
    assert ErrorCode.COMPLEXITY_EXCEEDED in codes(strict.validate(example["sql"], {}, analyst))


def test_scan_budget(bundle, policy, analyst):
    strict = SqlValidator(bundle, policy, QueryLimits(max_scanned_rows=100_000))
    report = strict.validate(f"SELECT count(*) AS n FROM {GOLD}.gold_procurement_notices", {}, analyst)
    assert ErrorCode.SCAN_BUDGET_EXCEEDED in codes(report)


def test_parameters_must_match_placeholders(validator, analyst):
    sql = f"SELECT project_id FROM {GOLD}.gold_project_portfolio WHERE project_id = :project_id LIMIT 1"
    assert ErrorCode.PARAMETER_ERROR in codes(validator.validate(sql, {}, analyst))
    assert ErrorCode.PARAMETER_ERROR in codes(validator.validate(sql, {"project_id": "P1", "extra": 1}, analyst))


def test_parsing_alone_never_approves(validator, analyst):
    report = validator.validate(f"SELECT project_id FROM {SILVER}.projects LIMIT 1", {}, analyst)
    assert report.checks[0].passed and not report.approved
