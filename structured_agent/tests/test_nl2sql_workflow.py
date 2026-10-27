"""Workflow integration tests: real registry, search and SQL validator; fake Portkey and fake Databricks.

The model's replies are scripted (FakePortkey). They stand in for Claude so that the workflow's own rules can be
tested deterministically; no answer is hard-coded into the generator.
"""
from __future__ import annotations

import dataclasses
import json
import re

import httpx
import pytest

from structured_agent.config import Settings
from structured_agent.nl2sql.context import build_schema_context, render_context
from structured_agent.nl2sql.result import FailureClass, WorkflowStatus
from structured_agent.nl2sql.settings import BreakerSettings, RuntimeSettings
from structured_agent.nl2sql.tools import InProcessToolCaller
from structured_agent.nl2sql.workflow import NlToSqlWorkflow
from structured_agent.observability import AuditEvent, Telemetry
from structured_agent.tests.nl2sql_fakes import (TEST_API_KEY, EchoColumnsExecutor, FakePortkey, completion, message_text,
                                                 offline_claude_client, sql_draft)
from structured_agent.tests.test_registry_and_mcp import registry_available

pytestmark = pytest.mark.skipif(not registry_available(), reason="registry database not reachable")

GOLD = "workspace.wbg_gold"
SILVER = "workspace.wbg_silver"
WORKSPACE = "wbg-procurement-poc"
BID_QUESTION = "How many bid invitations were published in fiscal year 2024?"
BID_SQL = (f"SELECT publication_fiscal_year, sum(procurement_notices) AS tender_invitation_notices "
           f"FROM {GOLD}.vw_kpi_notice_activity WHERE notice_class = 'tender_invitation' "
           "AND publication_fiscal_year = :fiscal_year GROUP BY publication_fiscal_year LIMIT 10")


class ListSink:
    def __init__(self):
        self.events: list[AuditEvent] = []

    def write(self, event: AuditEvent) -> None:
        self.events.append(event)


def make_service(tmp_path_factory, executor):
    from structured_agent.service import StructuredDataService

    settings = dataclasses.replace(Settings(), audit_log_path=tmp_path_factory.mktemp("audit") / "service.jsonl")
    return StructuredDataService.from_environment(settings, executor=executor)


@pytest.fixture(scope="module")
def read_only_executor():
    return EchoColumnsExecutor(write_capable=False)


@pytest.fixture(scope="module")
def service(tmp_path_factory, read_only_executor):
    return make_service(tmp_path_factory, read_only_executor)


@pytest.fixture(autouse=True)
def reset_executor(read_only_executor):
    read_only_executor.calls.clear()
    read_only_executor.failure = None


def workflow(service, portkey: FakePortkey, principal: str = "analyst-demo", **settings) -> tuple[NlToSqlWorkflow, ListSink]:
    sink = ListSink()
    tools = InProcessToolCaller(service, principal, WORKSPACE)
    claude = offline_claude_client(portkey)
    return NlToSqlWorkflow(tools, claude, Telemetry(sink), RuntimeSettings(**settings)), sink


# ------------------------------------------------------------------------------------------------ registry context
def test_context_contains_only_authorized_metadata(service):
    tools = InProcessToolCaller(service, "analyst-demo", WORKSPACE)
    context, outcome = build_schema_context(tools, BID_QUESTION, "req_ctx", max_tables=3, max_examples=2)
    selected = {t.object_fqn for t in context.tables}
    assert outcome.status == "ok" and len(context.tables) == 3
    assert all(fqn.startswith(GOLD) for fqn in selected)                                  # analysts never get Silver
    assert f"{GOLD}.gold_procurement_notices" in selected
    names = {c.name for t in context.tables for c in t.columns}
    assert not names & {"bronze_source_file", "gold_processed_at", "transform_version"}   # lineage columns dropped
    assert "tender_invitation_notice_count" in {m.metric_id for m in context.metrics}
    for example in context.examples:                                                     # examples only use selected tables
        assert set(re.findall(r"workspace\.wbg_\w+\.\w+", example.sql)) <= selected
    rendered = render_context(context)
    assert "TABLES (use only these" in rendered and SILVER not in rendered and len(rendered) < 20_000


def test_context_includes_approved_joins_between_selected_tables(service):
    tools = InProcessToolCaller(service, "analyst-demo", WORKSPACE)
    context, _ = build_schema_context(tools, "What contract award value went to projects by IEG outcome rating?", "req_ctx2", 3, 2)
    selected = {t.object_fqn for t in context.tables}
    assert {f"{GOLD}.gold_contract_analytics", f"{GOLD}.gold_project_portfolio"} <= selected
    assert [j.relationship_id for j in context.joins] == ["gold_contracts_to_portfolio"]
    for join in context.joins:                                                           # both ends are selected tables
        assert set(re.findall(r"workspace\.wbg_\w+\.\w+", join.condition)) <= selected


# --------------------------------------------------------------------------------------------- generation request
def test_sql_generation_request_is_grounded_and_claude_only(service):
    portkey = FakePortkey(sql_draft(BID_SQL, {"fiscal_year": 2024}))
    flow, _ = workflow(service, portkey)
    flow.run(BID_QUESTION)
    sent, body = portkey.requests[0], portkey.bodies()[0]
    assert "x-portkey-config" not in sent.headers and body["model"].startswith("@wbg-anthropic/")
    assert body["response_format"]["json_schema"]["name"] == "sql_draft"
    user_message = message_text(body["messages"][1])
    assert BID_QUESTION in user_message and f"{GOLD}." in user_message and SILVER not in user_message


# --------------------------------------------------------------------------------------------- valid and invalid SQL
def test_valid_sql_is_validated_and_executed_once(service, read_only_executor):
    portkey = FakePortkey(sql_draft(BID_SQL, {"fiscal_year": 2024}))
    flow, _ = workflow(service, portkey)
    result = flow.run(BID_QUESTION, execute=True)
    assert result.status == WorkflowStatus.ANSWERED and result.failure_class is None
    assert result.llm_calls == 1 and result.validation.approved and result.parameters == {"fiscal_year": 2024}
    assert len(read_only_executor.calls) == 1 and read_only_executor.calls[0].endswith("LIMIT 10")
    assert result.row_count == 2 and result.provenance["credential_mode"] == "read_only_principal"
    assert "not from the language model" in result.message and result.model_description_unverified == "Counts rows."


def test_validation_only_mode_never_executes(service, read_only_executor):
    flow, _ = workflow(service, FakePortkey(sql_draft(BID_SQL, {"fiscal_year": 2024})))
    result = flow.run(BID_QUESTION, execute=False)
    assert result.status == WorkflowStatus.VALIDATED_NOT_EXECUTED and read_only_executor.calls == []


def test_one_correction_after_a_hallucinated_column(service, read_only_executor):
    wrong = BID_SQL.replace("sum(procurement_notices)", "sum(notice_total)")
    portkey = FakePortkey(sql_draft(wrong, {"fiscal_year": 2024}), sql_draft(BID_SQL, {"fiscal_year": 2024}))
    flow, _ = workflow(service, portkey)
    result = flow.run(BID_QUESTION, execute=True)
    assert result.status == WorkflowStatus.ANSWERED and result.llm_calls == 2
    feedback = message_text(portkey.bodies()[1]["messages"][1])
    assert "UNKNOWN_COLUMN" in feedback and "previous SQL" in feedback
    assert len(read_only_executor.calls) == 1


@pytest.mark.parametrize("label, sql, expected_code", [
    ("hallucinated_table", f"SELECT supplier_rank FROM {GOLD}.gold_supplier_rankings LIMIT 10", "UNKNOWN_TABLE"),
    ("unsafe_join", f"SELECT sum(c.contract_amount_usd) AS v FROM {GOLD}.gold_contract_analytics AS c "
                    f"JOIN {GOLD}.gold_financing_analytics AS f ON c.project_id = f.project_id LIMIT 10", "FAN_OUT_RISK"),
    ("metric_misuse", f"SELECT sum(p50_contract_value_usd) AS v FROM {GOLD}.vw_kpi_contract_value_distribution "
                      "WHERE grouping_level = 'category' LIMIT 10", "NON_ADDITIVE_AGGREGATE"),
])
def test_recoverable_errors_get_at_most_one_correction(service, read_only_executor, label, sql, expected_code):
    portkey = FakePortkey(sql_draft(sql), sql_draft(sql))
    flow, _ = workflow(service, portkey)
    result = flow.run(BID_QUESTION, execute=True)
    assert result.status == WorkflowStatus.REJECTED and result.failure_class == FailureClass.SQL_VALIDATION_DENIED
    assert expected_code in result.validation.error_codes and result.llm_calls == 2
    assert read_only_executor.calls == []                                      # never reaches Databricks


@pytest.mark.parametrize("sql, expected_code, failure", [
    (f"SELECT project_id FROM {SILVER}.projects LIMIT 5", "UNAUTHORIZED_OBJECT", FailureClass.AUTHORIZATION_DENIED),
    ("SELECT project_id FROM workspace.wbg_bronze.bronze_projects_v3 LIMIT 5", "UNAUTHORIZED_OBJECT", FailureClass.AUTHORIZATION_DENIED),
    (f"DROP TABLE {GOLD}.gold_project_portfolio", "FORBIDDEN_OPERATION", FailureClass.SQL_VALIDATION_DENIED),
    (f"SELECT project_id FROM {GOLD}.gold_project_portfolio LIMIT 1; DELETE FROM {GOLD}.gold_project_portfolio",
     "MULTIPLE_STATEMENTS", FailureClass.SQL_VALIDATION_DENIED),
])
def test_security_failures_are_refused_without_correction(service, read_only_executor, sql, expected_code, failure):
    flow, _ = workflow(service, FakePortkey(sql_draft(sql)))
    result = flow.run(BID_QUESTION, execute=True)
    assert result.status == WorkflowStatus.REJECTED and result.failure_class == failure
    assert expected_code in result.validation.error_codes and result.llm_calls == 1
    assert read_only_executor.calls == []


def test_model_declining_is_cannot_answer(service, read_only_executor):
    flow, _ = workflow(service, FakePortkey(sql_draft("", answerable=False, explanation="No table holds bidder names.")))
    result = flow.run("Which bidders lost the tender?", execute=True)
    assert result.status == WorkflowStatus.CANNOT_ANSWER and result.validation is None and read_only_executor.calls == []


def test_ambiguous_question_asks_for_clarification_before_any_llm_call(service):
    portkey = FakePortkey()
    flow, _ = workflow(service, portkey)
    result = flow.run("What is the total value for Ethiopia?", execute=True)
    assert result.status == WorkflowStatus.NEEDS_CLARIFICATION and result.llm_calls == 0 and portkey.requests == []
    assert result.clarification_options


# ------------------------------------------------------------------------------------------------ provider failures
@pytest.mark.parametrize("reply, failure", [
    (httpx.Response(429, headers={"retry-after": "7"}), FailureClass.PROVIDER_RATE_LIMITED),
    (httpx.Response(503), FailureClass.PROVIDER_UNAVAILABLE),
    (httpx.Response(401), FailureClass.PROVIDER_AUTH_ERROR),
    (httpx.ReadTimeout("timed out"), FailureClass.PROVIDER_TIMEOUT),
])
def test_provider_failures_end_the_request_without_retry(service, read_only_executor, reply, failure):
    portkey = FakePortkey(reply)
    flow, _ = workflow(service, portkey)
    result = flow.run(BID_QUESTION, execute=True)
    assert result.status == WorkflowStatus.FAILED and result.failure_class == failure
    assert len(portkey.requests) == 1 and result.llm_calls == 1 and read_only_executor.calls == []
    if failure == FailureClass.PROVIDER_RATE_LIMITED:
        assert result.retry_after_seconds == 7


def test_no_grok_fallback_reply_is_ever_used(service, read_only_executor):
    portkey = FakePortkey(completion(sql_draft(BID_SQL, {"fiscal_year": 2024}), model="grok-3", provider="x-ai"))
    flow, _ = workflow(service, portkey)
    result = flow.run(BID_QUESTION, execute=True)
    assert result.failure_class == FailureClass.UNEXPECTED_PROVIDER and result.proposed_sql is None
    assert read_only_executor.calls == [] and all("x-portkey-config" not in r.headers for r in portkey.requests)


# ------------------------------------------------------------------------------------------ resilience in the flow
def test_request_rate_limit_rejects_before_any_work(service):
    portkey = FakePortkey(sql_draft(BID_SQL, {"fiscal_year": 2024}))
    flow, _ = workflow(service, portkey, requests_per_minute=1)
    flow.run(BID_QUESTION)
    second = flow.run(BID_QUESTION)
    assert second.failure_class == FailureClass.RATE_LIMITED and second.retry_after_seconds > 0
    assert len(portkey.requests) == 1


def test_total_deadline_is_enforced(service):
    portkey = FakePortkey()
    flow, _ = workflow(service, portkey, total_deadline_seconds=0.5)
    result = flow.run(BID_QUESTION)
    assert result.failure_class == FailureClass.DEADLINE_EXCEEDED and portkey.requests == []


def test_llm_breaker_opens_after_repeated_provider_failures(service):
    portkey = FakePortkey(httpx.Response(503), httpx.Response(503))
    flow, sink = workflow(service, portkey, llm_breaker=BreakerSettings(2, 60, 30, 300))
    flow.run(BID_QUESTION)
    flow.run(BID_QUESTION)
    third = flow.run(BID_QUESTION)
    assert third.failure_class == FailureClass.CIRCUIT_OPEN and third.retry_after_seconds > 0
    assert len(portkey.requests) == 2                                         # fail fast: no third upstream call
    transitions = [e.attributes for e in sink.events if e.event_type == "circuit_breaker.transition"]
    assert transitions == [{"breaker": "llm.claude_only", "from_state": "closed", "to_state": "open"}]


def test_validation_denials_never_trip_the_llm_breaker(service):
    sql = f"SELECT project_id FROM {SILVER}.projects LIMIT 5"
    portkey = FakePortkey(*[sql_draft(sql) for _ in range(4)])
    flow, _ = workflow(service, portkey, llm_breaker=BreakerSettings(2, 60, 30, 300))
    results = [flow.run(BID_QUESTION) for _ in range(4)]
    assert all(r.failure_class == FailureClass.AUTHORIZATION_DENIED for r in results)
    assert flow.controls.llm_breaker.state.value == "closed"


def test_databricks_failure_is_not_retried_and_trips_its_breaker(service, read_only_executor):
    read_only_executor.failure = RuntimeError("engine error with data 'sensitive-value-123'")
    portkey = FakePortkey(*[sql_draft(BID_SQL, {"fiscal_year": 2024}) for _ in range(2)])
    flow, _ = workflow(service, portkey, databricks_breaker=BreakerSettings(1, 60, 60, 600))
    first = flow.run(BID_QUESTION, execute=True)
    second = flow.run(BID_QUESTION, execute=True)
    assert first.failure_class == FailureClass.EXECUTION_FAILED and len(read_only_executor.calls) == 1
    assert second.failure_class == FailureClass.CIRCUIT_OPEN and len(read_only_executor.calls) == 1
    assert "sensitive-value-123" not in first.model_dump_json()


def test_write_capable_credential_is_refused_by_the_gateway(tmp_path_factory):
    executor = EchoColumnsExecutor(write_capable=True)
    service = make_service(tmp_path_factory, executor)
    flow, _ = workflow(service, FakePortkey(sql_draft(BID_SQL, {"fiscal_year": 2024})))
    result = flow.run(BID_QUESTION, execute=True)
    assert result.status == WorkflowStatus.EXECUTION_REFUSED and executor.calls == []


# ----------------------------------------------------------------------------------------------------- no leakage
def test_no_credentials_prompts_sql_or_rows_in_audit_events(service):
    flow, sink = workflow(service, FakePortkey(sql_draft(BID_SQL, {"fiscal_year": 2024})))
    result = flow.run(BID_QUESTION, execute=True)
    logged = json.dumps([e.model_dump() for e in sink.events])
    for secret in (TEST_API_KEY, BID_QUESTION, "SELECT", "tender_invitation'", "fiscal_year"):
        assert secret not in logged
    assert TEST_API_KEY not in result.model_dump_json()
    events = {e.event_type for e in sink.events}
    assert {"nl2sql.llm_call", "nl2sql.result"} <= events


# ---------------------------------------------------------------------------------- reviewed examples as references
def test_reviewed_example_sql_passes_through_the_workflow(service):
    """Reference check only: each verified template, proposed as the model's reply, is approved by the validator.
    The generator is not given these answers; the replies here are scripted test inputs."""
    verified = [e for e in service.bundle.examples if e.verification_status == "verified"]
    assert len(verified) >= 10
    for example in verified:
        flow, _ = workflow(service, FakePortkey(sql_draft(example.sql_text, example.parameters)))
        result = flow.run(example.question, execute=False)
        if result.status == WorkflowStatus.NEEDS_CLARIFICATION:
            continue  # discovery asked first; the example SQL never reached the model
        assert result.status == WorkflowStatus.VALIDATED_NOT_EXECUTED, (example.example_id, result.validation)


# ----------------------------------------------------------------------------------- provider diagnostics (offline)
def test_provider_400_is_diagnosable_without_leaking_its_message(service, read_only_executor):
    echo = f"model rejected input: {BID_QUESTION}"
    reply = httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": echo}})
    flow, sink = workflow(service, FakePortkey(reply))
    result = flow.run(BID_QUESTION, execute=True)
    assert result.failure_class == FailureClass.PROVIDER_BAD_REQUEST
    assert result.provider_status_code == 400 and result.provider_error_type == "invalid_request_error"
    assert echo not in result.model_dump_json() and read_only_executor.calls == []
    llm_event = next(e for e in sink.events if e.event_type == "nl2sql.llm_call")
    assert llm_event.attributes["provider_status_code"] == 400
    assert llm_event.attributes["provider_error_type"] == "invalid_request_error"
    assert echo not in json.dumps([e.model_dump() for e in sink.events])


def test_truncated_reply_is_reported_and_never_validated(service, read_only_executor):
    reply = completion('{"answerable": true, "sql": "SELECT', finish_reason="length")
    flow, _ = workflow(service, FakePortkey(reply))
    result = flow.run(BID_QUESTION, execute=True)
    assert result.failure_class == FailureClass.MODEL_OUTPUT_TRUNCATED and result.validation is None
    assert read_only_executor.calls == []
