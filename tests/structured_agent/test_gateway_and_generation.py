"""Execution gateway, result validation and bounded correction, with test-double executors and generators.

The doubles are clearly not Databricks or an LLM: they let the gateway's own rules be tested deterministically.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from structured_agent.config import Settings
from structured_agent.contracts import ErrorCode, ToolError
from structured_agent.execution.executor import CredentialCheck, QueryTimeout, RawResult
from structured_agent.execution.gateway import ExecutionGateway
from structured_agent.generation import (ClaudeProvider, GeneratedSql, PortkeyProvider, ProviderNotConfigured, SchemaContext,
                                         TemplateSqlGenerator, generate_with_correction)
from structured_agent.registry.vector_index import InvalidVectorError, NumpyCosineIndex, validate_vector

SQL = "SELECT project_id, notice_count FROM workspace.wbg_gold.gold_project_portfolio WHERE project_id = :p LIMIT 5"


class FakeExecutor:
    def __init__(self, write_capable: bool, rows=None, delay: float = 0.0):
        self.write_capable = write_capable
        self.rows = rows if rows is not None else [{"project_id": "P1", "notice_count": 3}]
        self.delay = delay
        self.calls: list[str] = []

    def check_credential(self) -> CredentialCheck:
        return CredentialCheck(self.write_capable, "test double")

    def execute(self, sql, parameters, timeout_seconds) -> RawResult:
        self.calls.append(sql)
        if self.delay > timeout_seconds:
            raise QueryTimeout(f"Query exceeded {timeout_seconds} s and was interrupted")
        return RawResult([("project_id", "string"), ("notice_count", "bigint")], self.rows)


def gateway(bundle, validator, executor, allow_dev: bool) -> ExecutionGateway:
    settings = dataclasses.replace(Settings(), allow_dev_owner_credentials=allow_dev)
    return ExecutionGateway(settings, bundle, validator, executor)


# ------------------------------------------------------------------------------------------------- credential gate
def test_write_capable_credentials_are_refused_by_default(bundle, validator, analyst):
    executor = FakeExecutor(write_capable=True)
    result = gateway(bundle, validator, executor, allow_dev=False).execute(SQL, {"p": "P1"}, analyst)
    assert result.errors[0].code == ErrorCode.CREDENTIAL_NOT_READ_ONLY and executor.calls == []


def test_dev_mode_results_are_labelled_not_production_read_only(bundle, validator, analyst):
    result = gateway(bundle, validator, FakeExecutor(write_capable=True), allow_dev=True).execute(SQL, {"p": "P1"}, analyst)
    assert result.status == "ok"
    assert result.provenance.credential_mode == "dev_owner_credentials" and result.provenance.production_read_only is False
    assert any("NOT by database permissions" in note for note in result.limitations)


def test_read_only_principal_needs_no_flag(bundle, validator, analyst):
    result = gateway(bundle, validator, FakeExecutor(write_capable=False), allow_dev=False).execute(SQL, {"p": "P1"}, analyst)
    assert result.provenance.credential_mode == "read_only_principal" and result.provenance.production_read_only


# ------------------------------------------------------------------------------------------------ gateway decisions
def test_rejected_sql_never_reaches_the_executor(bundle, validator, analyst):
    executor = FakeExecutor(write_capable=False)
    result = gateway(bundle, validator, executor, allow_dev=False).execute(
        "DROP TABLE workspace.wbg_gold.gold_project_portfolio", {}, analyst)
    assert result.status == "rejected" and executor.calls == []


def test_only_the_validated_sql_is_executed(bundle, validator, analyst):
    executor = FakeExecutor(write_capable=False)
    result = gateway(bundle, validator, executor, allow_dev=False).execute(SQL.replace(" LIMIT 5", ""), {"p": "P1"}, analyst)
    assert executor.calls == [result.report.validated_sql] and executor.calls[0].endswith("LIMIT 1000")


def test_timeout_returns_a_typed_error(bundle, validator, analyst):
    executor = FakeExecutor(write_capable=False, delay=10_000)
    result = gateway(bundle, validator, executor, allow_dev=False).execute(SQL, {"p": "P1"}, analyst)
    assert result.status == "error" and result.errors[0].code == ErrorCode.EXECUTION_TIMEOUT and result.rows == []


def test_contact_details_in_results_are_withheld(bundle, validator, analyst):
    executor = FakeExecutor(write_capable=False, rows=[{"project_id": "write to someone@example.org", "notice_count": 1}])
    result = gateway(bundle, validator, executor, allow_dev=False).execute(SQL, {"p": "P1"}, analyst)
    assert result.errors[0].code == ErrorCode.RESULT_VALIDATION_FAILED and result.rows == []


# ------------------------------------------------------------------------------------------------ bounded correction
class ScriptedGenerator:
    """Test double (NOT an LLM): returns a wrong column first, then the corrected query when given feedback."""

    name, is_llm = "scripted test double", False

    def __init__(self, second_sql: str):
        self.second_sql = second_sql
        self.calls = 0

    def generate(self, context, feedback: list[ToolError] | None = None) -> GeneratedSql:
        self.calls += 1
        sql = self.second_sql if feedback else SQL.replace("notice_count", "notices_total")
        return GeneratedSql(sql, {"p": "P1"}, self.name, False)


def empty_context() -> SchemaContext:
    return SchemaContext(question="q", candidates=[], column_types={}, metrics=[], examples=[])


def test_one_correction_for_a_recoverable_error(validator, analyst):
    generator = ScriptedGenerator(second_sql=SQL)
    outcome = generate_with_correction(generator, empty_context(), validator, analyst)
    assert generator.calls == 2 and len(outcome.attempts) == 2 and outcome.final[1].approved


def test_correction_is_bounded_to_one_attempt(validator, analyst):
    generator = ScriptedGenerator(second_sql=SQL.replace("notice_count", "still_wrong"))
    outcome = generate_with_correction(generator, empty_context(), validator, analyst)
    assert generator.calls == 2 and not outcome.final[1].approved


def test_security_failures_are_not_retried(validator, analyst):
    class Destructive(ScriptedGenerator):
        def generate(self, context, feedback=None):
            self.calls += 1
            return GeneratedSql("DROP TABLE workspace.wbg_gold.gold_project_portfolio", {}, self.name, False)
    generator = Destructive(second_sql="")
    outcome = generate_with_correction(generator, empty_context(), validator, analyst)
    assert generator.calls == 1 and not outcome.final[1].approved


def test_template_generator_is_labelled_deterministic(bundle):
    context = SchemaContext(question="q", candidates=[], column_types={}, metrics=[], examples=bundle.examples)
    generated = TemplateSqlGenerator("headline_totals").generate(context)
    assert generated.is_llm is False and "no LLM" in generated.generator


def test_external_providers_fail_closed_without_configuration(monkeypatch):
    monkeypatch.delenv("WBG_ALLOW_EXTERNAL_LLM", raising=False)
    with pytest.raises(ProviderNotConfigured):
        ClaudeProvider()
    with pytest.raises(ProviderNotConfigured):
        PortkeyProvider()


# --------------------------------------------------------------------------------------------------- vector index
def test_invalid_vectors_are_rejected():
    with pytest.raises(InvalidVectorError):
        validate_vector(np.zeros(4), 4)
    with pytest.raises(InvalidVectorError):
        validate_vector(np.ones(3), 4)
    with pytest.raises(InvalidVectorError):
        validate_vector(np.array([1.0, np.nan, 0, 0]), 4)


def test_vector_search_only_ranks_allowed_documents():
    index = NumpyCosineIndex("m", 2, ["a", "b"], np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
    hits = index.search(np.array([1.0, 0.0]), allowed_doc_ids={"b"}, top_k=5)
    assert [h.doc_id for h in hits] == ["b"]


def test_registry_embedding_model_loads_without_any_network(monkeypatch):
    """Worst case: sentence_transformers already imported before offline mode is set; all sockets blocked."""
    import socket

    import sentence_transformers  # noqa: F401  (deliberately imported first)

    from structured_agent.registry.vector_index import EmbeddingModel

    attempts: list[str] = []

    def refuse(self, address):
        attempts.append(str(address))
        raise ConnectionRefusedError("network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    assert EmbeddingModel("Alibaba-NLP/gte-base-en-v1.5").dimensions == 768 and attempts == []


def test_missing_registry_embedding_model_gives_a_controlled_error():
    from structured_agent.registry.vector_index import EmbeddingModel

    with pytest.raises(RuntimeError, match="requires approval"):
        EmbeddingModel("Alibaba-NLP/model-that-is-not-cached").dimensions
