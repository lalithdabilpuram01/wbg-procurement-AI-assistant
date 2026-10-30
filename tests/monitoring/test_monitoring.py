"""Execution & Monitoring on a small, hand-made corpus (and one check on the real one): normalisation, latest plan
revisions, benchmarks from history, each warning rule, and the citations behind every warning."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from monitoring import data, normalise, watch

PROJECT = "P000001"


@pytest.mark.parametrize("printed, canonical", [
    ("Request for Quota tions", "Request for Quotations"), ("Demande de prix", "Request for Quotations"),
    ("Solicitud de ofertas", "Request for Bids"), ("Sélection fondée sur la qualité et le coût", "Quality and Cost-Based Selection"),
    ("Rtioenqsuest for Quota", "Request for Quotations"), ("Individuel", "Individual Consultant Selection"), (None, None),
])
def test_methods_in_any_language_or_broken_layout_are_recognised(printed, canonical):
    assert normalise.method(printed) == canonical


def test_stage_names_survive_broken_spacing():
    assert normalise.stage("Draft Bidding Docum ent / Justification") == "Draft Bidding Document / Justification"
    assert normalise.stage("Teams of Reference") == "Terms of Reference"


# ------------------------------------------------------------------------------------------- fixture corpus
def activity(n: int, status: str, planned: str, actual: str | None = None, *, version: str = "v2", category: str = "WORK",
             estimated: float | None = 100.0, actual_usd: float | None = None, stages: dict | None = None, project: str = PROJECT) -> dict:
    return {"activity_id": f"a{n}", "project_id": project, "activity_reference_printed": f"REF-{n}", "description_clean": f"package {n}",
            "category_family": category, "method_clean": "Request for Bids", "review_type": "prior", "process_status": status,
            "document_id": f"plan-{version}", "pdf_page": 3, "plan_version_id": f"{project}:S:{version}", "plan_stream_id": f"{project}:S",
            "estimated_amount_usd": estimated, "actual_amount_usd": actual_usd, "signed_contract_planned": planned,
            "signed_contract_actual": actual, "stage_dates": stages or {}}


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    history = [activity(100 + i, "signed", "2020-01-01", f"2020-01-{1 + i:02d}", project="P999999") for i in range(20)]  # 0-19 days late
    peers = [activity(200 + 10 * k + i, "signed", "2020-01-01", "2020-01-01", project=f"P99999{k}") for k in range(3) for i in range(5)]
    rows = {
        "plan_versions": [{"plan_version_id": f"{PROJECT}:S:v1", "plan_stream_id": f"{PROJECT}:S", "pdf": {"revision_date": "2022-01-01"}},
                          {"plan_version_id": f"{PROJECT}:S:v2", "plan_stream_id": f"{PROJECT}:S", "pdf": {"revision_date": "2023-06-30"}},
                          *({"plan_version_id": f"{p}:S:v2", "plan_stream_id": f"{p}:S", "pdf": {"revision_date": "2021-01-01"}}
                            for p in ("P999999", "P999990", "P999991", "P999992"))],
        "plan_activities": history + peers + [
            activity(1, "under/pending implementation", "2023-05-01"),                         # 60 days overdue
            activity(2, "under/pending implementation", "2020-01-01"),                         # stale: 3.5 years
            activity(3, "signed", "2022-01-01", "2022-12-01"),                                  # 334 days late
            activity(4, "signed", "2023-01-01", "2023-01-05", estimated=100.0, actual_usd=150.0),
            activity(5, "under/pending implementation", "2024-01-01",
                     stages={"Draft Bidding Docum ent / Justification": {"planned": "2023-01-01", "actual": "2023-01-02"},
                             "Specific Procurement Notice / Invitation": {"planned": "2023-03-01"}}),
            activity(6, "signed", "2023-02-01", "2023-02-01"),
            activity(7, "cancelled", "2023-03-01"), activity(8, "cancelled", "2023-03-01"), activity(9, "cancelled", "2023-03-01"),
            activity(1, "signed", "2023-05-01", "2023-05-01", version="v1"),                    # an older revision: ignored
        ],
        "evidence_links": [{"activity_id": "a6", "wb_contract_number": "C6", "evidence_status": "amount_verified",
                            "date_recorded_signing": "2023-04-15", "contract_amount_usd": 100.0}],
        "projects": [{"project_id": PROJECT, "api": {"project_title": "Test Roads", "country_name": "Testland"}}],
        "documents": [{"document_id": "isr-1", "doc_type": "ISR", "project_id": PROJECT, "api": {"wds_document_date": "2023-05-01"}}],
        "paragraphs": [{"document_id": "isr-1", "text": "Overall Implementation Progress (IP) Moderately Satisfactory Moderately "
                                                         "Unsatisfactory Overall Risk Rating Substantial High"}],
    }
    for name, records in rows.items():
        (tmp_path / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    return tmp_path


def test_only_the_latest_plan_revision_counts_and_contracts_are_joined(corpus):
    packages = {p.activity_id: p for p in data.packages(corpus) if p.project_id == PROJECT}
    assert packages["a1"].status == "under/pending implementation"                    # v2, not the v1 "signed" row
    assert packages["a6"].recorded_signing.isoformat() == "2023-04-15" and packages["a6"].contract_number == "C6"
    assert [s.name for s in packages["a5"].stages] == ["Draft Bidding Document / Justification",
                                                       "Specific Procurement Notice / Invitation"]


def test_each_warning_rule_fires_with_its_evidence(corpus):
    result = watch.watch(PROJECT, corpus)
    by_code = {}
    for w in result.warnings:
        by_code.setdefault(w.code, []).append(w)
    assert [w.reference for w in by_code["overdue_signing"]] == ["REF-1"] and by_code["overdue_signing"][0].value == 60
    assert by_code["overdue_signing"][0].severity == "high"                            # history: 75% were signed within ~15 days
    assert [w.reference for w in by_code["stale_plan_entry"]] == ["REF-2"]
    assert {w.reference for w in by_code["slipped_signing"]} == {"REF-3"}
    assert [w.reference for w in by_code["stalled_stage"]] == ["REF-5"] and "Specific Procurement Notice" in by_code["stalled_stage"][0].message
    assert [w.reference for w in by_code["cost_overrun"]] == ["REF-4"]
    assert [w.reference for w in by_code["plan_out_of_date"]] == ["REF-6"]
    assert {w.code for w in result.warnings} >= {"rating_downgraded", "rating_unsatisfactory", "high_risk", "many_cancellations"}
    assert all(w.citations for w in result.warnings)
    assert result.as_of == "2023-06-30" and result.summary["overdue"] == 1 and result.summary["stale_plan_entries"] == 1
    assert (result.title, result.country) == ("Test Roads", "Testland")


def test_the_project_list_counts_packages_and_names_projects(corpus):
    listed = {p["project_id"]: p for p in watch.watched_projects(corpus)}
    assert listed[PROJECT] == {"project_id": PROJECT, "title": "Test Roads", "country": "Testland", "packages": 9}
    assert listed["P999999"]["title"] is None and listed["P999999"]["packages"] == 20


def test_benchmarks_fall_back_to_wider_groups(corpus):
    table = watch.slippage_benchmarks(corpus)
    assert table[("*", "*")].n >= 20 and ("WORK", "Request for Bids") in table


REAL = data.SHARED / "plan_activities.jsonl"


@pytest.mark.skipif(not REAL.exists(), reason="the processed corpus is not on this machine")
def test_the_real_corpus_gives_cited_warnings_for_a_pilot_project():
    result = watch.watch("P148850")
    assert result.summary["packages"] > 50 and result.warnings
    assert all(w.citations and w.citations[0].startswith(("Procurement plan", "ISR")) for w in result.warnings)
