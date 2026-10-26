"""IEG ratings transform logic on synthetic Bronze-shaped rows over Databricks Connect (no table I/O)."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from silver.ieg import transform as T

PROV = {"bronze_input_file_sha256": "a" * 64, "records": 5, "source_population_sha256": "b" * 64,
        "source_retrieved_at": "2026-10-21T01:20:08+00:00", "source_url_note": "x", "source_population_records": 5}
COLS = ["As of Date", "Project ID", "Project Name", "WB Region", "Country / Economy", "Country / Economy Lending Group",
        "Country / Economy FCS Status", "Country / Economy FCS Lending Group", "Practice Group", "Global Practice", "Agreement Type",
        "Lending Instrument Type", "Approval FY", "Final Closing FY", "Evaluation Type", "Outcome", "Quality at Entry",
        "Quality of Supervision", "Bank Performance", "M&E Quality", "Evaluation FY"]


@pytest.fixture(scope="module")
def spark():
    try:
        from silver.common.session import get_spark
        return get_spark()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no Databricks Connect session: {e}")


def row(pid, outcome="Satisfactory", qae="Moderately Satisfactory", qos="Highly Satisfactory", bp="Moderately Unsatisfactory",
        me="Substantial", ev_type="ICRR", approval="2015", closing="2022", evaluation="2023"):
    vals = {"As of Date": "10/18/2026", "Project ID": pid, "Project Name": "Synthetic", "WB Region": "R", "Country / Economy": "C",
            "Country / Economy Lending Group": "IDA", "Country / Economy FCS Status": "non-FCS", "Country / Economy FCS Lending Group": "IDA non-FCS",
            "Practice Group": "PG", "Global Practice": "GP", "Agreement Type": "IDA", "Lending Instrument Type": "IPF", "Approval FY": approval,
            "Final Closing FY": closing, "Evaluation Type": ev_type, "Outcome": outcome, "Quality at Entry": qae, "Quality of Supervision": qos,
            "Bank Performance": bp, "M&E Quality": me, "Evaluation FY": evaluation}
    return tuple(vals[c] for c in COLS) + ("/Volumes/x/ieg.csv", "wbg_poc_2000", datetime(2026, 10, 22))


@pytest.fixture(scope="module")
def built(spark):
    schema = ", ".join(f"`{c}` string" for c in COLS) + ", source_file string, cohort_id string, ingested_at timestamp"
    df = spark.createDataFrame([
        row("P000001"),
        row("P000002", outcome="Highly Unsatisfactory", qae="Not Rated", qos=None, bp="Highly Satisfactory", me="Not Rated", ev_type="PPAR"),
        row("P000003", outcome="Not Rated", me="Negligible", ev_type="PCR", closing="2027", evaluation="2024"),   # evaluated before final closing
        row("P000004", outcome="Moderately Satisfactory", qae="Excellent", me="High", closing=None),                # out-of-vocabulary text
        row("P000005", outcome="Moderately Unsatisfactory", evaluation="20X5"),                                     # FY parse failure
    ], schema)
    return {r.project_id: r for r in T.build(df, PROV, "t").collect()}


def test_ordinal_scores_and_text_preserved(built):
    a = built["P000001"]
    assert (a.outcome_rating, a.outcome_score) == ("Satisfactory", 5)
    assert (a.quality_at_entry_score, a.quality_of_supervision_score, a.bank_performance_score, a.me_quality_score) == (4, 6, 3, 3)
    assert a.outcome_satisfactory is True
    b = built["P000002"]
    assert (b.outcome_score, b.bank_performance_score, b.outcome_satisfactory) == (1, 6, False)
    assert b.quality_at_entry_rating == "Not Rated" and b.quality_at_entry_score is None      # unrated -> NULL score, text kept
    assert b.quality_of_supervision_rating is None and b.quality_of_supervision_score is None  # blank -> NULL
    assert b.me_quality_rating == "Not Rated" and b.me_quality_score is None
    c = built["P000003"]
    assert c.outcome_score is None and c.outcome_satisfactory is None and c.me_quality_score == 1


def test_scale_endpoints_are_lowest_one_and_highest_max():
    assert min(T.SIX_POINT, key=T.SIX_POINT.get) == "Highly Unsatisfactory" and T.SIX_POINT["Highly Satisfactory"] == 6
    assert T.FOUR_POINT_ME["Negligible"] == 1 and T.FOUR_POINT_ME["High"] == 4 == max(T.FOUR_POINT_ME.values())
    assert sorted(T.SIX_POINT.values()) == list(range(1, 7)) and sorted(T.FOUR_POINT_ME.values()) == list(range(1, 5))


def test_vocabulary_violation_flagged_not_scored(built):
    d = built["P000004"]
    assert d.quality_at_entry_rating == "Excellent" and d.quality_at_entry_score is None and d._vocab_ok_quality_at_entry is False
    assert built["P000001"]._vocab_ok_quality_at_entry is True and built["P000002"]._vocab_ok_quality_of_supervision is True


def test_fiscal_years_lag_and_precedes_flag(built):
    a, c, d, e = built["P000001"], built["P000003"], built["P000004"], built["P000005"]
    assert (a.evaluation_fiscal_year, a.final_closing_fiscal_year, a.evaluation_lag_fiscal_years, a.evaluation_precedes_final_closing) == (2023, 2022, 1, False)
    assert (c.evaluation_lag_fiscal_years, c.evaluation_precedes_final_closing) == (-3, True)
    assert d.final_closing_fiscal_year is None and d.evaluation_lag_fiscal_years is None and d.evaluation_precedes_final_closing is None
    assert e.evaluation_fiscal_year is None and e._raw_eval_fy == "20X5"                      # parse failure surfaced for the checks


def test_availability_not_inferred_and_lineage(built):
    a = built["P000001"]
    assert all(r.rating_available_from_date is None for r in built.values())                  # never inferred from the FY
    assert a.source_as_of_date == date(2026, 10, 18) and a.rating_snapshot_semantics == "latest_evaluation_as_of_source_date"
    assert a.evaluation_type_name == "Implementation Completion and Results Report Review" and built["P000003"].evaluation_type_name is None
    assert len({r.source_row_sha256 for r in built.values()}) == 5 and a.source_dataset == "DS00053"
