"""Planning Brief steps for a new project, over the read-only Gold tables (fixed SQL; no generated SQL, no approval).

Operations:
  match_similar_projects -> SIMILAR_PROJECT records (at most 8), ranked by country, sector, description similarity
                            (projects with documents) and size; the best ones with documents become the document scope
  market_benchmarks      -> STRUCTURED_RESULT records per planned category: methods used (awards, median amount, median
                            days from approval to signing), review types and supplier origin; country level, or the
                            region when the country has fewer than MIN_AWARDS awards
  governance_context     -> STRUCTURED_RESULT: the country's latest CPIA, WGI and income classification
The profile comes from the structured request (`WorkerContext.planning`), never from message text.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ml.serving.gold import GOLD, RunSql, country_region, project_sector

from ..contracts import (AgentTask, ErrorCategory, EvidenceKind, EvidenceRecord, ExecutionError, Fact, NumericUse,
                         PlanningSpec, ProjectProfile, SourceSystem, TaskState, ValidationStatus, WorkerName, new_id)
from .base import TicketAuthority, WorkerContext, WorkerOutput
from .prediction import LookupFailed

SemanticScores = Callable[[str], dict[str, float]]      # description -> {project_id: similarity} (projects with documents)
MAX_SIMILAR, MAX_DOCUMENT_SCOPE, MIN_DOCUMENT_SCOPE = 8, 5, 2
MIN_AWARDS = 20
YEARS_OF_HISTORY = 10
PORTFOLIO, CONTRACTS, COUNTRIES = (f"{GOLD}.gold_project_portfolio", f"{GOLD}.gold_contract_analytics",
                                   f"{GOLD}.gold_country_context")

CANDIDATES_SQL = (f"SELECT project_id, project_name, wdi_country_code, region_name, approval_fiscal_year, project_status_snapshot, "
                  f"current_total_commitment_usd_snapshot, contract_award_count, ieg_outcome_rating FROM {PORTFOLIO} "
                  f"WHERE region_name = :region_name LIMIT 500")
_SCOPE = {"country": "wdi_country_code = :country_code", "region": "region_name = :region_name"}
_FILTER = "{scope} AND procurement_category = :category AND contract_fiscal_year >= :since_fy"
TEMPLATES = {
    "methods": ("SELECT procurement_method, COUNT(*) AS contract_awards, MEDIAN(contract_amount_usd) AS median_contract_amount_usd, "
                "MEDIAN(days_from_board_approval_to_signing) AS median_days_from_approval_to_signing FROM " + CONTRACTS +
                " WHERE " + _FILTER + " GROUP BY procurement_method ORDER BY contract_awards DESC LIMIT 10"),
    "review_types": ("SELECT review_type, COUNT(*) AS contract_awards FROM " + CONTRACTS + " WHERE " + _FILTER +
                     " GROUP BY review_type ORDER BY contract_awards DESC LIMIT 10"),
    "supplier_origin": ("SELECT supplier_country_relation, COUNT(*) AS contract_awards FROM " + CONTRACTS + " WHERE " + _FILTER +
                        " GROUP BY supplier_country_relation ORDER BY contract_awards DESC LIMIT 10"),
}
GOVERNANCE_SQL = (f"SELECT year, cpia_irai_overall, cpia_public_sector_mgmt_cluster, cpia_transparency_corruption, "
                  f"wgi_government_effectiveness_est, wgi_regulatory_quality_est, wgi_control_of_corruption_est, "
                  f"income_group_fy_starting_in_year FROM {COUNTRIES} WHERE country_code = :country_code ORDER BY year DESC LIMIT 3")


@dataclass(frozen=True)
class SimilarProject:
    project_id: str
    name: str
    country: str
    sector: str
    approval_fiscal_year: int | None
    status: str | None
    commitment_usd: float | None
    contract_awards: int | None
    ieg_rating: str | None
    score: float
    reasons: tuple[str, ...]
    has_documents: bool
    document_scope: bool = False


def _size_closeness(planned: float | None, actual: Any) -> float:
    if not planned or actual is None or float(actual) <= 0:
        return 0.0
    return max(0.0, 1 - abs(math.log10(float(actual) / planned)) / 2)   # 1 at the same size, 0 at 100x apart


def _float(value: Any) -> float | None:
    return None if value is None else float(value)              # Databricks returns DECIMAL columns as Decimal


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


def rank_similar(profile: ProjectProfile, candidates: list[dict[str, Any]], sector_of: Callable[[str], str],
                 semantic: dict[str, float]) -> list[SimilarProject]:
    """Top MAX_SIMILAR by 0.35 country + 0.30 sector + 0.25 description similarity + 0.10 size; then the document scope."""
    best_semantic = max(semantic.values(), default=0.0) or 1.0
    ranked = []
    for c in candidates:
        pid, sector = c["project_id"], sector_of(c["project_id"])
        same_country, same_sector = c.get("wdi_country_code") == profile.country_code, sector == profile.sector
        text = semantic.get(pid, 0.0) / best_semantic
        size = _size_closeness(profile.total_cost_usd, c.get("current_total_commitment_usd_snapshot"))
        reasons = tuple(r for r, hit in (("same country", same_country), ("same sector", same_sector),
                                         ("similar description", text >= 0.93), ("similar size", size >= 0.75)) if hit)
        ranked.append(SimilarProject(
            project_id=pid, name=c.get("project_name") or pid, country=c.get("wdi_country_code") or "regional", sector=sector,
            approval_fiscal_year=_int(c.get("approval_fiscal_year")), status=c.get("project_status_snapshot"),
            commitment_usd=_float(c.get("current_total_commitment_usd_snapshot")), contract_awards=_int(c.get("contract_award_count")),
            ieg_rating=c.get("ieg_outcome_rating"), score=round(0.35 * same_country + 0.30 * same_sector + 0.25 * text + 0.10 * size, 3),
            reasons=reasons, has_documents=pid in semantic))
    ranked.sort(key=lambda p: (-p.score, p.project_id))
    top = ranked[:MAX_SIMILAR]
    scope = [p.project_id for p in top if p.has_documents][:MAX_DOCUMENT_SCOPE]
    if len(scope) < MIN_DOCUMENT_SCOPE:                                  # lessons need documents: add the closest that have them
        extra = sorted((p for p in ranked if p.has_documents and p.project_id not in scope), key=lambda p: -semantic[p.project_id])
        additions = extra[:MIN_DOCUMENT_SCOPE + 1 - len(scope)]
        top += [p for p in additions if p not in top]
        scope += [p.project_id for p in additions]
    return [SimilarProject(**{**p.__dict__, "document_scope": p.project_id in scope}) for p in top]


class PlanningWorker:
    name = WorkerName.PLANNING
    operations = frozenset({"match_similar_projects", "market_benchmarks", "governance_context"})

    def __init__(self, run_sql: RunSql, authority: TicketAuthority, semantic_scores: SemanticScores,
                 sector_of: Callable[[str], str] = project_sector, today: Callable[[], dt.date] = dt.date.today):
        self._run_sql, self._authority, self._semantic = run_sql, authority, semantic_scores
        self._sector_of, self._today = sector_of, today

    def run(self, task: AgentTask, context: WorkerContext) -> WorkerOutput:
        self._authority.verify(context.ticket, task)
        if task.operation not in self.operations:
            return _failed(task, ErrorCategory.AUTHORIZATION, "unknown_operation", "The operation is not offered by this worker.")
        if not isinstance(context.planning, PlanningSpec):
            return _failed(task, ErrorCategory.VALIDATION, "planning_missing", "A Planning Brief needs the project profile.")
        try:
            evidence = {"match_similar_projects": self._similar, "market_benchmarks": self._market,
                        "governance_context": self._governance}[task.operation](task, context.planning)
        except LookupFailed as failed:
            return _failed(task, ErrorCategory.EXECUTION, str(failed)[:80], "The project records could not be read.")
        if not evidence:
            return _failed(task, ErrorCategory.NO_EVIDENCE, "no_records", "No comparable records were found for this country.")
        return WorkerOutput(TaskState.SUCCEEDED, evidence=tuple(evidence))

    # ------------------------------------------------------------------------------------------ operations
    def _similar(self, task: AgentTask, spec: PlanningSpec) -> list[EvidenceRecord]:
        profile = spec.profile
        region = country_region(profile.country_code, self._run_sql)
        candidates = self._run_sql(CANDIDATES_SQL, {"region_name": region})
        projects = rank_similar(profile, candidates, self._sector_of, self._semantic(profile.description))
        return [_record(task, EvidenceKind.SIMILAR_PROJECT, (PORTFOLIO,), f"Similar project {p.project_id}: {p.name}"[:300], (
            Fact(name="project_id", value=p.project_id), Fact(name="project_name", value=p.name[:200]),
            Fact(name="country", value=p.country), Fact(name="sector", value=p.sector[:200]),
            Fact(name="approval_fiscal_year", value=str(p.approval_fiscal_year or "unknown")),
            Fact(name="status", value=str(p.status or "unknown")),
            Fact(name="commitment_usd", value=str(round(p.commitment_usd)) if p.commitment_usd else "unknown", unit="USD"),
            Fact(name="contract_awards", value=str(p.contract_awards if p.contract_awards is not None else "unknown")),
            Fact(name="ieg_outcome_rating", value=str(p.ieg_rating or "not rated")),
            Fact(name="similarity", value=str(round(100 * p.score)), unit="%"),
            Fact(name="match", value="; ".join(p.reasons) or "same region"),
            Fact(name="documents", value="yes" if p.has_documents else "no"),
            Fact(name="document_scope", value="yes" if p.document_scope else "no")),
            f"{p.name} ({p.project_id}, {p.country}, {p.sector}): {'; '.join(p.reasons) or 'same region'}.",
            project_ids=(p.project_id,), provenance=(("template", "similar_projects"), ("region", region),
                                                     ("sql_sha256", _sha(CANDIDATES_SQL)), ("params_sha256", _sha(p.project_id))))
            for p in projects]

    def _market(self, task: AgentTask, spec: PlanningSpec) -> list[EvidenceRecord]:
        profile, since = spec.profile, self._today().year - YEARS_OF_HISTORY
        region: str | None = None
        records = []
        for category in dict.fromkeys(p.procurement_category for p in spec.packages):
            scope, params = "country", {"country_code": profile.country_code, "category": category, "since_fy": since}
            methods = self._run_sql(TEMPLATES["methods"].format(scope=_SCOPE[scope]), params)
            if sum(int(r["contract_awards"]) for r in methods) < MIN_AWARDS:             # too few: use the region
                region = region or country_region(profile.country_code, self._run_sql)
                scope, params = "region", {"region_name": region, "category": category, "since_fy": since}
                methods = self._run_sql(TEMPLATES["methods"].format(scope=_SCOPE[scope]), params)
            where = profile.country_code if scope == "country" else region
            for template, rows in (("methods", methods),
                                   ("review_types", self._run_sql(TEMPLATES["review_types"].format(scope=_SCOPE[scope]), params)),
                                   ("supplier_origin", self._run_sql(TEMPLATES["supplier_origin"].format(scope=_SCOPE[scope]), params))):
                if rows:
                    records.append(_benchmark(task, template, category, scope, where, since, rows, params))
        return records

    def _governance(self, task: AgentTask, spec: PlanningSpec) -> list[EvidenceRecord]:
        rows = self._run_sql(GOVERNANCE_SQL, {"country_code": spec.profile.country_code})
        if not rows:
            return []
        latest = rows[0]
        facts = tuple(Fact(name=k, value=str(round(v, 2) if isinstance(v, float) else v)) for k, v in latest.items() if v is not None)
        return [_record(task, EvidenceKind.STRUCTURED_RESULT, (COUNTRIES,),
                        f"Governance context of {spec.profile.country_code}, {latest.get('year')} (gold_country_context)", facts,
                        "Country Policy and Institutional Assessment (CPIA, 1-6) and Worldwide Governance Indicators (WGI, about "
                        "-2.5 to 2.5): " + "; ".join(f"{f.name} {f.value}" for f in facts) + ".",
                        provenance=(("template", "governance"), ("sql_sha256", _sha(GOVERNANCE_SQL))))]


# ---------------------------------------------------------------------------------------------- helpers
LABELS = {"methods": "Procurement methods used", "review_types": "Review types", "supplier_origin": "Supplier origin"}


def _benchmark(task: AgentTask, template: str, category: str, scope: str, where: str, since: int, rows: list[dict],
               params: dict) -> EvidenceRecord:
    key = next(iter(rows[0]))                                                # the grouped column comes first
    facts = []
    for row in rows[:10]:
        values = "; ".join(f"{k.replace('_', ' ')} {_number(v)}" for k, v in row.items() if k != key and v is not None)
        facts.append(Fact(name=str(row[key] or "Unknown")[:120], value=values[:200]))
    title = f"{LABELS[template]} for {category} contracts in {where} since FY{since} ({scope} level, gold_contract_analytics)"
    return _record(task, EvidenceKind.STRUCTURED_RESULT, (CONTRACTS,), title[:300], tuple(facts),
                   title + ": " + " | ".join(f"{f.name}: {f.value}" for f in facts),
                   provenance=(("template", template), ("category", category), ("scope", scope),
                               ("sql_sha256", _sha(TEMPLATES[template])), ("params_sha256", _sha(repr(sorted(params.items())))))
                   )


def _number(value: Any) -> str:
    return str(round(float(value))) if isinstance(value, (float, Decimal)) else str(value)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _record(task: AgentTask, kind: EvidenceKind, objects: tuple[str, ...], citation: str, facts: tuple[Fact, ...], summary: str,
            project_ids: tuple[str, ...] = (), provenance: tuple[tuple[str, str], ...] = ()) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=new_id("ev"), investigation_id=task.investigation_id, task_id=task.task_id,
        source_system=SourceSystem.DATABRICKS_PUBLIC, kind=kind, project_ids=project_ids, source_objects=objects,
        source_version="gold:fixed_template", citation=citation, provenance=provenance,
        validation_status=ValidationStatus.VERIFIED, numeric_use=NumericUse.APPROVED, facts=facts[:25], summary=summary[:1500])


def _failed(task: AgentTask, category: ErrorCategory, code: str, message: str) -> WorkerOutput:
    return WorkerOutput(TaskState.FAILED, error=ExecutionError(
        error_id=new_id("err"), investigation_id=task.investigation_id, task_id=task.task_id, node="execute",
        category=category, code=code[:80], safe_message=message))
