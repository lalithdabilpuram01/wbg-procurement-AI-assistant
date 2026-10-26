"""Procurement-notice transform logic on synthetic Bronze-shaped rows over Databricks Connect (no table I/O)."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime

import pytest

from silver.notices import transform as T

sys.path.insert(0, str(T.REPO / "discovery"))
from wbdisc.award_notice import parse_award_notice  # noqa: E402
from wbdisc.normalize import normalize_ref, strip_html  # noqa: E402

PROV = {"bronze_input_file_sha256": "a" * 64, "records": 3, "source_population_sha256": "b" * 64,
        "source_retrieved_at": "2026-10-21T01:26:07+00:00", "source_url_note": "x", "coverage": "complete"}
CSV_COLS = ["ID", "URL", "Notice Type", "Publication Date", "Project ID", "Bid Description", "Procurement Category", "Procurement Method",
            "Deadline Date", "Country Code", "Country Name", "Region", "Sector", "Publication - Fiscal year", "Publication - Calendar year"]
AWARD_HTML = ("<div><h4>Contract Award</h4><p><b>Project: </b>P000001-X<br/><b>Bid/Contract Reference No: </b>LOT-1 / A<br/>"
              "<b>Notice Version No: </b>2</p></div><div><u><b>Awarded Bidder(s):</b></u><b>ACME &amp; SONS (123456)</b><br/>Country: Ghana</div>"
              "<div><u><b>Evaluated Bidder(s):</b></u><b>OTHER (654321)</b></div>")
REFS = ["  mr-wardip-mr-538131-cs-indv ", "ICB/01.2024", "a\u2013b", "--X--", "N/A", "", "re  bid\tno", "a\u00a0b", "x\u3000y\u2028",
        "Lote N\u00ba 7", "Lot \u21161", "\ufb01le \u00bd", "\uff21\uff22\uff0f\uff11", "\u00aa"]


@pytest.fixture(scope="module")
def spark():
    try:
        from silver.common.session import get_spark
        return get_spark()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no Databricks Connect session: {e}")


def api_row(i, typ, proj="P000001", ref="REF-1", text=None, status="Published"):
    r = {"id": f"OP{i:08d}", "notice_type": typ, "noticedate": "07-Apr-2023", "notice_status": status, "project_id": proj, "project_name": "X",
         "bid_reference_no": ref, "bid_description": "Desc  one", "submission_date": "2023-04-07T00:00:00Z", "notice_text": text,
         "_extract": {"filter_project_id": proj}}
    return (json.dumps(r), "/Volumes/x/api.jsonl", "wbg_poc_2000", datetime(2026, 10, 22))


@pytest.fixture(scope="module")
def built(spark):
    api = spark.createDataFrame([
        api_row(10, "Contract Award", ref="LOT-PKG", text=AWARD_HTML),                       # text reference differs from field reference
        api_row(11, "Invitation for Bids", ref="lot-1/a", text="<p>IFB</p>"),
        api_row(12, "Contract Award", ref="REF-2", text=AWARD_HTML.replace("LOT-1 / A", "REF-2").replace("123456", "999")),  # supplier not on contract
    ], "raw_record string, source_file string, cohort_id string, ingested_at timestamp")
    d = T.build_details(api, PROV, "t")
    csv = spark.createDataFrame([
        ("10", T.URL_PREFIX + "OP00000010", "Contract Award", "04/07/2023", "P000001", "Desc one", "Goods", "Request for Bids", "04/07/2023",
         None, "Ghana", "Africa", "A;B", "2023", "2023", "/Volumes/x/n.csv", "wbg_poc_2000", datetime(2026, 10, 22)),
        ("99", T.URL_PREFIX + "OP00000099", "Contract Award", "04/07/2023", "P000001", "Desc one", "Goods", "Request for Bids", "04/07/2023",
         None, "Ghana", "Africa", "A;B", "2023", "2023", "/Volumes/x/n.csv", "wbg_poc_2000", datetime(2026, 10, 22)),
    ], ", ".join(f"`{c}` string" for c in CSV_COLS) + ", source_file string, cohort_id string, ingested_at timestamp")
    n = T.build_notices(csv, d, PROV, "t")
    con = spark.createDataFrame([("C1", "P000001", "LOT-1/A", date(2023, 3, 1), ["123456", None]), ("C2", "P000001", " ref_2 ", date(2023, 3, 1), ["555"])],
                                "wb_contract_number string, project_id string, borrower_contract_reference string, contract_signing_date date, supplier_ids array<string>")
    cand = T.build_link_candidates(d, con, "t")
    return ({r.notice_op_id: r for r in d.collect()}, {r.notice_id: r for r in n.collect()},
            {r.candidate_key: r for r in cand.collect()}, {r.link_key: r for r in T.build_links(cand, "t").collect()})


def test_normalize_ref_matches_python(spark):
    got = spark.createDataFrame([(r,) for r in REFS], "r string").select("r", T.normalize_ref(T.F.col("r")).alias("n")).collect()
    assert {r.r: r.n for r in got} == {r: normalize_ref(r) for r in REFS}


def test_award_fields_match_python(built):
    d, _, _, _ = built
    p = parse_award_notice(AWARD_HTML)
    a = d["OP00000010"]
    assert (a.award_notice_version_no, a.award_reference_in_text, a.awarded_supplier_ids) == (p["notice_version_no"], p["reference_in_text"], p["awarded_supplier_ids"])
    assert a.awarded_supplier_ids == ["123456"] and a.award_reference_in_text_normalized == "LOT-1-A"
    assert a._notice_text_plain == strip_html(AWARD_HTML) and a.notice_text == AWARD_HTML
    assert d["OP00000011"].award_notice_version_no is None and d["OP00000011"].awarded_supplier_ids is None
    assert a.notice_date == date(2023, 4, 7) and a.notice_id == "10" and a.bid_description_normalized == "Desc one"


def test_notices_keep_identical_rows_and_flag_api(built):
    _, n, _, _ = built
    assert set(n) == {"10", "99"} and n["10"].identical_metadata_group_size == 2
    assert n["10"].has_api_detail and not n["99"].has_api_detail
    assert n["10"].notice_op_id == "OP00000010" and n["10"].sectors == ["A", "B"] and n["10"].country_code is None


def test_link_candidates_and_verified_links(built):
    _, _, cand, links = built
    c1 = cand["OP00000010:C1"]
    assert c1.matched_on_award_reference_in_text and not c1.matched_on_bid_reference_no and c1.supplier_id_confirmed and c1.matched_supplier_ids == ["123456"]
    assert cand["OP00000011:C1"].link_evidence == "reference_only" and cand["OP00000011:C1"].supplier_id_confirmed is None
    assert cand["OP00000012:C2"].supplier_id_confirmed is False                     # same reference, supplier not confirmed
    lk = links["OP00000010:C1"]
    assert set(links) == {"OP00000010:C1"} and (lk.link_cardinality, lk.ambiguity_status, lk.verification_status) == (
        "one_to_one", "unambiguous", "evidence_supported_one_to_one")
    assert c1.match_method == T.MATCH_METHOD and lk.match_method == T.MATCH_METHOD


def test_ambiguous_links_are_never_unique(spark):
    cand = spark.createDataFrame([("N1:C1", "N1", "C1"), ("N2:C1", "N2", "C1"), ("N2:C2", "N2", "C2"), ("N3:C3", "N3", "C3"), ("N3:C4", "N3", "C4")],
                                 "candidate_key string, notice_op_id string, wb_contract_number string")
    cand = cand.select("*", T.F.lit(True).alias("supplier_id_confirmed"), T.F.lit("reference_and_awarded_supplier_id").alias("link_evidence"),
                       T.F.lit(1).alias("candidate_contracts_for_notice"), T.F.lit(1).alias("candidate_notices_for_contract"),
                       T.F.lit("x").alias("silver_run_id"), T.F.current_timestamp().alias("silver_processed_at"), T.F.lit("v").alias("transform_version"))
    got = {r.link_key: (r.link_cardinality, r.ambiguity_status, r.verification_status) for r in T.build_links(cand, "t").collect()}
    assert got["N1:C1"] == ("many_notices_one_contract", "ambiguous", "evidence_supported_ambiguous")
    assert got["N2:C1"] == ("many_to_many", "ambiguous", "evidence_supported_ambiguous")
    assert got["N2:C2"] == ("one_notice_many_contracts", "ambiguous", "evidence_supported_ambiguous")
    assert {got["N3:C3"][1], got["N3:C4"][1]} == {"ambiguous"}


TEXTS = ["Contact: jane.doe@example.org", "It involves approximately 120 sites", "Intel Core i5 4 GHz 8 GB", "T\u00e9l\u00e9phone : +33 1 23 45 67 89",
         "Tel: (021) 555-0100", "station mobile de mesure", "MOBILE 0700 000 000", "no contact here", "fax.+44 20 7946 0000"]


def test_contact_flags_and_text_hash_match_python(spark):
    import hashlib
    import re
    df = spark.createDataFrame([(t,) for t in TEXTS], "t string").select(
        "t", T.F.col("t").rlike(T.EMAIL_RE).alias("e"), T.F.col("t").rlike("(?iu)" + T.PHONE_RE).alias("p"), T.F.sha2("t", 256).alias("h"))
    got = {r.t: (r.e, r.p, r.h) for r in df.collect()}
    exp = {t: (bool(re.search(T.EMAIL_RE, t)), bool(re.search(T.PHONE_RE, t, re.I)), hashlib.sha256(t.encode()).hexdigest()) for t in TEXTS}
    assert got == exp
    assert [t for t in TEXTS if exp[t][1]] == [TEXTS[3], TEXTS[4], TEXTS[6], TEXTS[8]]          # "approximately", "Intel", "station mobile" not flagged


def test_restricted_text_columns(built):
    d, _, _, _ = built
    a = d["OP00000010"]
    assert a.notice_text_access_class == "restricted" and a.notice_text_sha256 is not None and a.notice_text_contains_email is False
    assert not any("plain" in c and not c.startswith("_") for c in a.asDict())
