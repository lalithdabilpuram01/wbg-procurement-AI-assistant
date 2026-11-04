"""The records monitoring reads, from the processed document corpus (pilot_v3): procurement-plan activities with
their stage dates (only each plan stream's latest revision), their matched contract records, and ISR ratings."""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from rag.ingestion import config

from . import normalise

SHARED = config.OUTPUT_V3 / "shared"
RATINGS = ("Highly Unsatisfactory", "Unsatisfactory", "Moderately Unsatisfactory", "Moderately Satisfactory", "Satisfactory",
           "Highly Satisfactory")                                       # worst -> best
RISKS = ("Low", "Moderate", "Substantial", "High")
INDICATORS = {"Progress towards achievement of PDO": "Development objective (PDO)",
              "Overall Implementation Progress (IP)": "Implementation progress", "Overall Risk Rating": "Overall risk"}
RATING_TEXT = re.compile("(" + "|".join(re.escape(k) for k in INDICATORS) + r")[^A-Za-z]{0,6}(" + "|".join(RATINGS + RISKS) +
                         r")(?:[^A-Za-z]{0,6}(" + "|".join(RATINGS + RISKS) + "))?")
LINK_RANK = {"amount_verified": 4, "exact_actual_date_supported": 3, "candidate": 2, "conflicting": 1}
STEP_REFERENCE = re.compile(r"\b[A-Z]{2}-[A-Z0-9]{2,12}-\d{2,7}-[A-Z]{2}-[A-Z]{2,6}\b")


def clean_reference(printed: str) -> str:
    """The STEP reference when the extracted cell also caught neighbouring text ("e project. ET-RSIDP-542687-GO-RFQ")."""
    found = STEP_REFERENCE.search(printed or "")
    return found.group(0) if found else " ".join((printed or "").split())


@dataclass
class Stage:
    """One plan stage with its planned and actual dates."""

    name: str
    planned: dt.date | None
    actual: dt.date | None


@dataclass
class Package:
    """One procurement-plan activity as of its plan revision, joined to its best-matching contract record if any."""

    activity_id: str
    project_id: str
    reference: str
    description: str | None
    category: str | None
    method: str | None
    review_type: str | None
    status: str
    document_id: str
    page: int | None
    plan_version_id: str
    plan_date: dt.date | None                    # the revision the statuses are "as of"
    estimated_usd: float | None
    actual_usd: float | None
    planned_signing: dt.date | None
    actual_signing: dt.date | None               # as reported in the plan
    contract_number: str | None = None
    recorded_signing: dt.date | None = None      # from the World Bank contract records (independent of the plan)
    contract_amount_usd: float | None = None
    stages: list[Stage] = field(default_factory=list)

    @property
    def signed(self) -> dt.date | None:
        """Signing date: the one reported in the plan, else the one in the contract record."""
        return self.actual_signing or self.recorded_signing

    @property
    def slippage_days(self) -> int | None:
        return (self.signed - self.planned_signing).days if self.signed and self.planned_signing else None


@dataclass(frozen=True)
class Project:
    """Title, country and sector of a corpus project."""

    project_id: str
    title: str
    country: str | None
    sector: str | None = None


@dataclass
class Rating:
    """One ISR rating of one indicator: the previous and the current value as printed in the report."""

    document_id: str
    date: dt.date | None
    indicator: str
    previous: str | None
    current: str


def day(value) -> dt.date | None:
    """The date at the start of an ISO date or datetime value; None when it is missing or not a date."""
    try:
        return dt.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _jsonl(name: str, directory: Path) -> list[dict]:
    path = directory / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@lru_cache(maxsize=2)
def packages(directory: Path = SHARED) -> tuple[Package, ...]:
    """Activities of each plan stream's latest revision, joined to their best contract match."""
    versions = _jsonl("plan_versions", directory)
    dated = {v["plan_version_id"]: day(v["pdf"].get("revision_date") or (v.get("api") or {}).get("wds_document_date")) for v in versions}
    # Keep only the latest revision of each plan stream (by revision date, else the document date).
    latest: dict[str, str] = {}
    for v in versions:
        current = latest.get(v["plan_stream_id"])
        if current is None or (dated[v["plan_version_id"]] or dt.date.min) > (dated[current] or dt.date.min):
            latest[v["plan_stream_id"]] = v["plan_version_id"]
    keep = set(latest.values())
    links: dict[str, dict] = {}
    # Keep the strongest contract match per activity (see LINK_RANK); a match needs a contract number.
    for link in _jsonl("evidence_links", directory):
        if link.get("wb_contract_number") and LINK_RANK.get(link.get("evidence_status"), 0) > \
                LINK_RANK.get((links.get(link["activity_id"]) or {}).get("evidence_status"), 0):
            links[link["activity_id"]] = link
    out, seen = [], set()
    for a in _jsonl("plan_activities", directory):
        if a["plan_version_id"] not in keep or (a["plan_stream_id"], a["activity_reference_printed"]) in seen:
            continue
        # An activity printed twice in the same plan revision is kept once.
        seen.add((a["plan_stream_id"], a["activity_reference_printed"]))
        link = links.get(a["activity_id"], {})
        out.append(Package(
            a["activity_id"], a["project_id"], clean_reference(a["activity_reference_printed"]), a.get("description_clean"), a.get("category_family"),
            normalise.method(a.get("method_clean")), a.get("review_type"), a.get("process_status") or "blank", a["document_id"],
            a.get("pdf_page"), a["plan_version_id"], dated[a["plan_version_id"]], a.get("estimated_amount_usd"), a.get("actual_amount_usd"),
            day(a.get("signed_contract_planned")), day(a.get("signed_contract_actual")), link.get("wb_contract_number"),
            day(link.get("date_recorded_signing")), link.get("contract_amount_usd"),
            sorted((Stage(normalise.stage(name), day(d.get("planned")), day(d.get("actual"))) for name, d in (a.get("stage_dates") or {}).items()),
                   key=lambda s: s.planned or s.actual or dt.date.max)))
    return tuple(out)


@lru_cache(maxsize=2)
def ratings(directory: Path = SHARED) -> dict[str, tuple[Rating, ...]]:
    """ISR ratings per project (previous -> current), oldest report first."""
    docs = {d["document_id"]: d for d in _jsonl("documents", directory) if d["doc_type"] == "ISR"}
    found: dict[tuple[str, str], Rating] = {}
    for paragraph in _jsonl("paragraphs", directory):
        doc = docs.get(paragraph["document_id"])
        if doc is None:
            continue
        for m in RATING_TEXT.finditer(paragraph["text"]):
            label = INDICATORS[m.group(1)]
            # Two values are printed as "previous current"; a single value is the current rating.
            previous, current = (m.group(2), m.group(3)) if m.group(3) else (None, m.group(2))
            found.setdefault((doc["document_id"], label), Rating(doc["document_id"], day(doc["api"].get("wds_document_date")), label,
                                                                  previous, current))
    by_project: dict[str, list[Rating]] = defaultdict(list)
    for (document_id, _), rating in found.items():
        by_project[docs[document_id]["project_id"]].append(rating)
    return {p: tuple(sorted(r, key=lambda x: (x.date or dt.date.min, x.indicator))) for p, r in by_project.items()}


@lru_cache(maxsize=2)
def projects(directory: Path = SHARED) -> dict[str, Project]:
    """Title, country and sector of each project in the corpus (from the project selection, not the PDFs)."""
    return {r["project_id"]: Project(r["project_id"], r["api"].get("project_title") or r["project_id"], r["api"].get("country_name"),
                                     r["api"].get("sector"))
            for r in _jsonl("projects", directory)}
