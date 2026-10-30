"""The agent contracts cannot drift: the catalog, the workers, the authorization table, the evidence specifications,
the planner schema and the engine's worker registry must all name exactly the same operations."""
from __future__ import annotations

import typing

from tests.orchestrator.helpers import build_engine
from wbg_orchestrator.catalog import CATALOG, as_markdown, authorized, catalogued
from wbg_orchestrator.contracts import EvidenceRecord, InvestigationRequest, PlanningSpec, WorkerName, evidence_spec
from wbg_orchestrator.planner import DraftTask


def registered_operations() -> set[tuple[str, str]]:
    _, parts = build_engine()
    workers = parts["controller"]._workers
    return {(name.value, op) for name, worker in workers.items() for op in worker.operations}


def test_every_worker_operation_is_catalogued_authorized_and_registered():
    assert catalogued() == authorized() == registered_operations()


def test_every_operation_has_an_evidence_specification():
    assert all(evidence_spec(c.operation, requires_execution=False) for c in CATALOG)


def test_the_planner_can_name_every_worker_and_no_other():
    workers = set(typing.get_args(DraftTask.model_fields["worker"].annotation))
    assert workers == {w.value for w in WorkerName}


def test_messages_between_agents_are_closed_versioned_records():
    for model in (InvestigationRequest, EvidenceRecord, PlanningSpec):
        assert model.model_config.get("extra") == "forbid" and model.model_config.get("frozen") is True
        assert "schema_version" in model.model_fields


def test_the_catalog_renders_one_row_per_operation():
    assert as_markdown().count("\n") == len(CATALOG) + 1
