"""Data-quality rules for the procurement-notice Silver tables. "error" blocks writes; "warn" is reported.

Parity checks compare the Spark implementations in transform.py with the Phase 1 Python implementations in
discovery/wbdisc (normalize_ref, strip_html, parse_award_notice) on every record.
"""
from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.notices import transform as T

EMAIL = re.compile(T.EMAIL_RE)
PHONE = re.compile(T.PHONE_RE, re.I)

sys.path.insert(0, str(T.REPO / "discovery"))
from wbdisc.award_notice import parse_award_notice  # noqa: E402
from wbdisc.normalize import normalize_ref, strip_html  # noqa: E402


def _cnt(df: DataFrame, cond) -> int:
    return df.filter(cond).count()


def _ws(s: str | None) -> str | None:
    return None if s is None else re.sub(r"\s+", " ", s).strip()


def check_notices(n: DataFrame, bronze: DataFrame, projects: DataFrame, details: DataFrame, prov: dict, rep: DQReport) -> None:
    rows = n.count()
    rep.equals("notices:rows_equal_bronze", rows, bronze.count())
    rep.equals("notices:rows_equal_manifest", rows, prov["records"])
    rep.equals("notices:notice_id_unique", n.select("notice_id").distinct().count(), rows)
    rep.equals("notices:notice_id_null_or_non_numeric", _cnt(n, F.col("notice_id").isNull() | ~F.col("notice_id").rlike(r"^\d+$")), 0)
    rep.equals("notices:url_equals_prefix_plus_op_id (OP + ID zero-padded to 8)", _cnt(n, F.col("notice_url") != F.concat(F.lit(T.URL_PREFIX), F.col("notice_op_id"))), 0)
    rep.equals("notices:op_id_format", _cnt(n, ~F.col("notice_op_id").rlike(r"^OP\d{8}$")), 0)
    types = {r[0]: r[1] for r in n.groupBy("notice_type").count().collect()}
    rep.equals("notices:notice_types_known", sorted(set(types) - set(T.NOTICE_TYPES)), [])
    rep.add("notices:notice_type_counts", types, "reported", True, "warn")
    # dates and years as published
    rep.equals("notices:publication_date_parse_failures", _cnt(n, F.col("_raw_publication_date").isNotNull() & F.col("publication_date").isNull()), 0)
    rep.equals("notices:deadline_date_parse_failures", _cnt(n, F.col("_raw_deadline_date").isNotNull() & F.col("deadline_date").isNull()), 0)
    rep.equals("notices:publication_date_null", _cnt(n, F.col("publication_date").isNull()), 0)
    rep.equals("notices:calendar_year_consistent", _cnt(n, F.year("publication_date") != F.col("publication_calendar_year")), 0)
    rep.equals("notices:fiscal_year_consistent (WB FY Jul-Jun)",
               _cnt(n, (F.year("publication_date") + F.when(F.month("publication_date") >= 7, 1).otherwise(0)) != F.col("publication_fiscal_year")), 0)
    rep.equals("notices:published_after_source_retrieval", _cnt(n, F.col("publication_date") > F.to_date("source_retrieved_at")), 0)
    rng = n.agg(F.min("publication_date"), F.max("publication_date"), F.min("deadline_date"), F.max("deadline_date")).collect()[0]
    rep.add("notices:date_ranges (publication min/max, deadline min/max)", [str(x) for x in rng], "reported", True, "warn")
    rel = {r[0]: r[1] for r in n.groupBy(F.when(F.col("deadline_date") < F.col("publication_date"), "deadline_before_publication")
                                         .when(F.col("deadline_date") == F.col("publication_date"), "deadline_equals_publication")
                                         .otherwise("deadline_after_publication")).count().collect()}
    rep.add("notices:deadline_vs_publication (kept as published)", rel, "reported", True, "warn",
            "Deadline Date equals Publication Date on every award notice: not a real deadline for awards")
    rep.add("notices:deadline_before_publication_by_type",
            {r[0]: r[1] for r in n.filter(F.col("deadline_date") < F.col("publication_date")).groupBy("notice_type").count().collect()}, "reported", True, "warn")
    # NULLs exactly as in Bronze (empty CSV fields)
    pairs = {"bid_description": "Bid Description", "procurement_category": "Procurement Category", "procurement_method": "Procurement Method",
             "country_code": "Country Code", "country_name": "Country Name", "sectors_published": "Sector", "region_name_source": "Region"}
    rep.equals("notices:null_counts_equal_bronze", {k: _cnt(n, F.col(k).isNull()) for k in pairs}, {k: _cnt(bronze, F.col(f"`{v}`").isNull()) for k, v in pairs.items()})
    rep.equals("notices:sectors_array_when_published", _cnt(n, F.col("sectors_published").isNotNull() & F.col("sectors").isNull()), 0)
    rep.equals("notices:source_row_sha256_unique", n.select("source_row_sha256").distinct().count(), rows)
    rep.equals("notices:source_row_hashes_equal_bronze",
               n.select("source_row_sha256").exceptAll(bronze.select(T.row_hash_expr(T.source_columns(bronze)))).count(), 0)
    # projects and cohort
    rep.equals("notices:project_id_not_in_silver_projects", n.join(projects, "project_id", "left_anti").count(), 0)
    rep.equals("notices:silver_projects_without_notices", projects.join(n.select("project_id").distinct(), "project_id", "left_anti").count(), 0)
    rep.equals("notices:cohort_id_values", [r[0] for r in n.select("bronze_cohort_id").distinct().collect()], ["wbg_poc_2000"])
    cc = n.join(projects.select("project_id", F.col("country_code").alias("p_cc"), "geography_type"), "project_id")
    rep.add("notices:country_code_vs_project (kept as published, never filled)",
            {"blank_in_source": _cnt(cc, F.col("country_code").isNull()),
             "blank_in_source_single_country_projects": _cnt(cc, F.col("country_code").isNull() & (F.col("geography_type") == "single_country")),
             "equal_to_project": _cnt(cc, F.col("country_code") == F.col("p_cc")),
             "differs": {f"{r[0]}->{r[1]}": r[2] for r in cc.filter(F.col("country_code") != F.col("p_cc")).groupBy("country_code", "p_cc").count().collect()}},
            "reported", True, "warn")
    # duplicates and API coverage
    dup = n.filter("identical_metadata_group_size > 1")
    rep.add("notices:identical_except_id (separate notices, kept)",
            {"rows": dup.count(), "groups": dup.select("bid_description", "project_id", "notice_type", "publication_date", "deadline_date",
                                                       "procurement_method", "procurement_category", "country_code", "country_name",
                                                       "region_name_source", "sectors_published").distinct().count(),
             "by_type": {r[0]: r[1] for r in dup.groupBy("notice_type").count().collect()}, "max_group": dup.agg(F.max("identical_metadata_group_size")).collect()[0][0]},
            "reported", True, "warn")
    api_projects = details.select("project_id").distinct()
    in_scope = n.join(api_projects, "project_id", "left_semi")
    rep.equals("notices:api_detail_covers_every_notice_of_its_projects", {"in_scope": in_scope.count(), "with_detail": _cnt(in_scope, "has_api_detail")},
               {"in_scope": in_scope.count(), "with_detail": in_scope.count()})
    rep.equals("notices:has_api_detail_only_in_api_projects", _cnt(n.join(api_projects, "project_id", "left_anti"), "has_api_detail"), 0)


def check_details(d: DataFrame, bronze: DataFrame, projects: DataFrame, notices: DataFrame, prov: dict, rep: DQReport) -> dict:
    rows = d.count()
    rep.equals("details:rows_equal_bronze", rows, bronze.count())
    rep.equals("details:rows_equal_manifest", rows, prov["records"])
    rep.equals("details:notice_op_id_unique", d.select("notice_op_id").distinct().count(), rows)
    rep.equals("details:notice_op_id_format", _cnt(d, F.col("notice_op_id").isNull() | ~F.col("notice_op_id").rlike(r"^OP\d{8}$")), 0)
    rep.equals("details:raw_record_sha256_unique", d.select("raw_record_sha256").distinct().count(), rows)
    rep.equals("details:json_keys_known", sorted(r[0] for r in d.select(F.explode("_json_keys")).distinct().collect() if r[0] not in T.API_KEYS), [])
    rep.equals("details:section_marker_absent_from_text", _cnt(d, "_has_section_marker"), 0)
    rep.equals("details:extract_filter_equals_project_id", _cnt(d, ~F.col("extract_filter_project_id").eqNullSafe(F.col("project_id"))), 0)
    rep.equals("details:project_id_not_in_silver_projects", d.join(projects, "project_id", "left_anti").count(), 0)
    rep.add("details:projects_covered", d.select("project_id").distinct().count(), "reported (Phase 1 cluster sample; partial coverage)", True, "warn")
    rep.equals("details:notice_date_parse_failures", _cnt(d, F.col("_raw_noticedate").isNotNull() & F.col("notice_date").isNull()), 0)
    rep.equals("details:submission_date_parse_failures", _cnt(d, F.col("_raw_submission_date").isNotNull() & F.col("submission_date").isNull()), 0)
    rep.equals("details:deadline_date_parse_failures", _cnt(d, F.col("_raw_deadline").isNotNull() & F.col("submission_deadline_date").isNull()), 0)
    rep.equals("details:submission_date_equals_notice_date", _cnt(d, F.col("submission_date") != F.col("notice_date")), 0)
    rep.add("details:missing_notice_date", [tuple(r) for r in d.filter("notice_date IS NULL").select("notice_op_id", "notice_type", "notice_status").collect()],
            "reported", True, "warn")
    rep.add("details:status_by_type", {f"{r[0]}|{r[1]}": r[2] for r in d.groupBy("notice_type", "notice_status").count().collect()}, "reported", True, "warn")
    nulls = {c: _cnt(d, F.col(c).isNull()) for c in ("bid_reference_no", "notice_text", "bid_description", "procurement_group", "notice_language",
                                                    "project_country_name", "submission_deadline_date")}
    rep.add("details:null_counts (kept as published)", nulls, "reported", True, "warn")
    # relation to DS00979
    j = d.join(notices.select("notice_id", F.col("notice_type").alias("n_type"), F.col("project_id").alias("n_project"),
                              F.col("publication_date").alias("n_date"), F.col("procurement_method").alias("n_method"),
                              F.col("bid_description_normalized").alias("n_desc")), "notice_id", "left")
    rep.equals("details:in_ds00979", _cnt(j, F.col("n_type").isNotNull()), 20296)
    rep.add("details:not_in_ds00979 (kept)", [tuple(r) for r in j.filter("n_type IS NULL").select("notice_op_id", "notice_type", "notice_status").orderBy("notice_op_id").collect()],
            "reported", True, "warn")
    m = j.filter("n_type IS NOT NULL")
    rep.equals("details:fields_agree_with_ds00979 (type, project, date, method)",
               {"type": _cnt(m, F.col("notice_type") != F.col("n_type")), "project": _cnt(m, F.col("project_id") != F.col("n_project")),
                "date": _cnt(m, ~F.col("notice_date").eqNullSafe(F.col("n_date"))),
                "method": _cnt(m, ~F.coalesce(F.col("procurement_method_name"), F.lit("")).eqNullSafe(F.coalesce(F.col("n_method"), F.lit(""))))},
               {"type": 0, "project": 0, "date": 0, "method": 0})
    rep.add("details:description_differs_from_ds00979",
            [tuple(r) for r in m.filter(~F.coalesce(F.col("bid_description_normalized"), F.lit("")).eqNullSafe(F.coalesce(F.col("n_desc"), F.lit(""))))
             .select("notice_op_id", "bid_description", "n_desc").collect()], "reported", True, "warn")
    # Python parity (wbdisc) on every record
    rows_py = d.select("notice_op_id", "notice_type", "bid_reference_no", "bid_reference_normalized", "award_reference_in_text",
                       "award_reference_in_text_normalized", "award_notice_version_no", "awarded_supplier_ids", "is_step_award_template",
                       F.sha2("_notice_text_plain", 256).alias("plain_sha"), "notice_text", "bid_description", "bid_description_normalized",
                       "notice_text_sha256", "notice_text_contains_email", "notice_text_contains_phone", "notice_text_access_class").collect()
    bad: Counter = Counter()
    examples: dict[str, list] = {}

    def miss(k: str, ex) -> None:
        bad[k] += 1
        examples.setdefault(k, []).append(ex) if len(examples.get(k, [])) < 3 else None

    for r in rows_py:
        if normalize_ref(r.bid_reference_no) != r.bid_reference_normalized:
            miss("bid_reference_normalized", (r.notice_op_id, r.bid_reference_no, r.bid_reference_normalized))
        if _ws(r.bid_description) != r.bid_description_normalized:
            miss("bid_description_normalized", (r.notice_op_id,))
        plain = strip_html(r.notice_text) if r.notice_text is not None else None
        if (hashlib.sha256(plain.encode("utf-8")).hexdigest() if plain is not None else None) != r.plain_sha:
            miss("notice_text_plain", (r.notice_op_id,))
        t = r.notice_text
        got_c = (r.notice_text_sha256, r.notice_text_contains_email, r.notice_text_contains_phone, r.notice_text_access_class)
        exp_c = (None, None, None, None) if t is None else (hashlib.sha256(t.encode("utf-8")).hexdigest(), bool(EMAIL.search(t)), bool(PHONE.search(t)), "restricted")
        if got_c != exp_c:
            miss("notice_text_hash_and_contact_flags", (r.notice_op_id, got_c[1:], exp_c[1:]))
        if r.notice_type == "Contract Award":
            p = parse_award_notice(r.notice_text)
            got = {"is_step_template": r.is_step_award_template, "version": r.award_notice_version_no, "reference": r.award_reference_in_text,
                   "reference_normalized": r.award_reference_in_text_normalized, "awarded_ids": r.awarded_supplier_ids}
            exp = {"is_step_template": p["is_step_template"], "version": p.get("notice_version_no"), "reference": p.get("reference_in_text"),
                   "reference_normalized": normalize_ref(p.get("reference_in_text")), "awarded_ids": p.get("awarded_supplier_ids")}
            for k in exp:
                if got[k] != exp[k]:
                    miss(f"award_{k}", (r.notice_op_id, got[k], exp[k]))
    rep.equals("details:spark_python_parity (normalize_ref, award-notice fields, whitespace, text hash, contact flags) mismatches",
               {k: v for k, v in bad.items() if k != "notice_text_plain"}, {}, detail=str({k: v for k, v in examples.items() if k != "notice_text_plain"})[:600])
    rep.add("details:internal_plain_text_differs_from_python_strip_html (entity subset; not published, used only to locate award fields)",
            bad.get("notice_text_plain", 0), "reported", True, "warn")
    rep.add("details:contact_data_in_notice_text (D-14; whole column restricted)",
            {"email": _cnt(d, "notice_text_contains_email"), "phone": _cnt(d, "notice_text_contains_phone"),
             "either": _cnt(d, "notice_text_contains_email OR notice_text_contains_phone"), "texts": _cnt(d, "notice_text IS NOT NULL")}, "reported", True, "warn")
    # duplicates / repeated references (reported, never merged)
    it = d.filter("identical_text_group_size > 1")
    rep.add("details:identical_text_publications (same project, type and full text; kept)",
            {"rows": it.count(), "groups": it.select("project_id", "notice_type", "notice_text").distinct().count(),
             "by_type": {r[0]: r[1] for r in it.groupBy("notice_type").count().collect()}}, "reported", True, "warn")
    refs = d.filter("bid_reference_normalized IS NOT NULL").groupBy("project_id", "bid_reference_normalized").agg(F.count("*").alias("n"),
                                                                                                                F.array_sort(F.collect_list("notice_type")).alias("types"))
    rep.add("details:notices_sharing_a_reference_in_project (not interpreted)",
            {"groups": refs.filter("n > 1").count(), "rows": refs.filter("n > 1").agg(F.sum("n")).collect()[0][0],
             "tender_refs_with_gt1_tender_notice": refs.filter(F.size(F.filter("types", lambda t: t.isin(*T.TENDER_TYPES))) > 1).count()},
            "reported", True, "warn")
    ver = {str(r[0]): r[1] for r in d.filter("notice_type = 'Contract Award'").groupBy("award_notice_version_no").count().collect()}
    rep.add("details:award_notice_version_no (notice revisions, NOT contract amendments)", ver, "reported", True, "warn")
    return {"rows_for_evidence": rows_py}


def check_links(cand: DataFrame, links: DataFrame, details: DataFrame, contracts: DataFrame, rep: DQReport) -> dict:
    n = cand.count()
    rep.equals("candidates:key_unique", cand.select("candidate_key").distinct().count(), n)
    rep.equals("candidates:notice_in_details", cand.join(details, "notice_op_id", "left_anti").count(), 0)
    rep.equals("candidates:contract_in_contract_awards", cand.join(contracts, "wb_contract_number", "left_anti").count(), 0)
    rep.equals("candidates:same_project_both_sides",
               cand.join(contracts.select("wb_contract_number", F.col("project_id").alias("cp")), "wb_contract_number").filter("cp != project_id").count(), 0)
    rep.equals("candidates:reference_equal_after_normalisation",
               _cnt(cand, ~(F.col("matched_reference_normalized").eqNullSafe(T.normalize_ref(F.col("borrower_contract_reference")))
                            & (F.col("matched_reference_normalized").eqNullSafe(T.normalize_ref(F.col("bid_reference_no")))
                               | F.col("matched_reference_normalized").eqNullSafe(T.normalize_ref(F.col("award_reference_in_text")))))), 0)
    rep.equals("candidates:supplier_confirmation_only_on_award_notices", _cnt(cand, (F.col("notice_type") != "Contract Award") & F.col("supplier_id_confirmed").isNotNull()), 0)
    # contract reference normalisation parity (all contracts)
    cref = contracts.select("borrower_contract_reference", T.normalize_ref(F.col("borrower_contract_reference")).alias("n")).distinct().collect()
    bad = [(r[0], r[1]) for r in cref if normalize_ref(r[0]) != r[1]]
    rep.equals("contracts:reference_normalisation_spark_python_parity_mismatches", len(bad), 0, detail=str(bad[:3]))
    rep.add("candidates:by_type_and_evidence", {f"{r[0]}|{r[1]}": r[2] for r in cand.groupBy("notice_type", "link_evidence").count().collect()}, "reported", True, "warn")
    # evidence-supported links
    lk = links.count()
    rep.equals("links:subset_of_candidates", links.select("link_key").exceptAll(cand.select(F.col("candidate_key").alias("link_key"))).count(), 0)
    rep.equals("links:rows_equal_supplier_confirmed_candidates", lk, _cnt(cand, "supplier_id_confirmed"))
    rep.equals("links:award_notices_only", _cnt(links, F.col("notice_type") != "Contract Award"), 0)
    rep.equals("links:matched_supplier_ids_non_empty", _cnt(links, F.size("matched_supplier_ids") < 1), 0)
    rep.equals("links:matched_ids_are_contract_supplier_ids",
               links.select("wb_contract_number", F.explode("matched_supplier_ids").alias("sid"))
               .join(contracts.select("wb_contract_number", F.explode("supplier_ids").alias("sid")), ["wb_contract_number", "sid"], "left_anti").count(), 0)
    rep.equals("links:matched_ids_are_awarded_ids",
               links.select("notice_op_id", F.explode("matched_supplier_ids").alias("sid"))
               .join(details.select("notice_op_id", F.explode("awarded_supplier_ids").alias("sid")), ["notice_op_id", "sid"], "left_anti").count(), 0)
    rep.equals("links:status_consistent (unambiguous only for one_to_one; counts agree with cardinality)",
               _cnt(links, ~(((F.col("link_cardinality") == "one_to_one") == (F.col("ambiguity_status") == "unambiguous"))
                             & ((F.col("ambiguity_status") == "unambiguous") == (F.col("verification_status") == "evidence_supported_one_to_one"))
                             & ((F.col("supported_contracts_for_notice") == 1) == F.col("link_cardinality").isin("one_to_one", "many_notices_one_contract"))
                             & ((F.col("supported_notices_for_contract") == 1) == F.col("link_cardinality").isin("one_to_one", "one_notice_many_contracts")))), 0)
    rep.equals("links:match_method_and_evidence_recorded", _cnt(links, F.col("match_method").isNull() | (F.col("link_evidence") != "reference_and_awarded_supplier_id")), 0)
    card = {f"{r[0]}|{r[1]}": r[2] for r in links.groupBy("link_cardinality", "ambiguity_status").count().collect()}
    rep.add("links:cardinality_and_ambiguity (pairs)", card, "reported", True, "warn")
    # coverage, contract side
    api_projects = details.select("project_id").distinct()
    cin = contracts.join(api_projects, "project_id", "left_semi").select("wb_contract_number")
    vc = links.groupBy("wb_contract_number").agg(F.count("*").alias("v"), F.max((F.col("ambiguity_status") == "unambiguous").cast("int")).alias("u"))
    cc = cand.groupBy("wb_contract_number").agg(F.count("*").alias("c"))
    st = cin.join(vc, "wb_contract_number", "left").join(cc, "wb_contract_number", "left").select(
        F.when(F.col("u") == 1, "one_to_one_unambiguous")
        .when(F.col("v") == 1, "single_supported_notice_shared_with_other_contracts (ambiguous)")
        .when(F.col("v") > 1, "multiple_supported_notices (ambiguous)")
        .when(F.col("c") >= 1, "reference_candidates_only (not supported by supplier ID)").otherwise("no_candidate_notice").alias("s"))
    contract_cov = {r[0]: r[1] for r in st.groupBy("s").count().collect()}
    contract_cov["contracts_outside_api_projects (not assessable: no reference in DS00979)"] = contracts.count() - cin.count()
    rep.add("links:contract_coverage", contract_cov, "reported", True, "warn")
    aw = details.filter("notice_type = 'Contract Award'").select("notice_op_id", "bid_reference_normalized", "award_reference_in_text_normalized")
    nv = links.groupBy("notice_op_id").agg(F.count("*").alias("v"), F.max((F.col("ambiguity_status") == "unambiguous").cast("int")).alias("u"))
    nc = cand.groupBy("notice_op_id").agg(F.count("*").alias("c"))
    ns = aw.join(nv, "notice_op_id", "left").join(nc, "notice_op_id", "left").select(
        F.when(F.col("u") == 1, "one_to_one_unambiguous")
        .when(F.col("v") == 1, "single_supported_contract_shared_with_other_notices (ambiguous)")
        .when(F.col("v") > 1, "multiple_supported_contracts (ambiguous)")
        .when(F.col("c") >= 1, "reference_candidates_only (not supported by supplier ID)")
        .when(F.col("bid_reference_normalized").isNull() & F.col("award_reference_in_text_normalized").isNull(), "no_reference")
        .otherwise("no_contract_with_reference").alias("s"))
    notice_cov = {r[0]: r[1] for r in ns.groupBy("s").count().collect()}
    rep.add("links:award_notice_coverage", notice_cov, "reported", True, "warn")
    return {"contract_coverage": contract_cov, "award_notice_coverage": notice_cov, "link_cardinality_and_ambiguity": card}


def check_contact_data_outside_text(out: dict[str, DataFrame], rep: DQReport) -> None:
    """D-14: only details.notice_text (masked) may contain e-mail addresses; phone-like patterns elsewhere are reported."""
    email, phone = {}, {}
    for k, df in out.items():
        cols = [f.name for f in df.schema.fields if f.dataType.simpleString() == "string" and f.name != "notice_text"]
        row = df.select(*[F.sum(F.col(c).rlike(T.EMAIL_RE).cast("int")).alias(f"e_{c}") for c in cols],
                        *[F.sum(F.col(c).rlike("(?iu)" + T.PHONE_RE).cast("int")).alias(f"p_{c}") for c in cols]).collect()[0].asDict()
        email.update({f"{k}.{c[2:]}": v for c, v in row.items() if c.startswith("e_") and v})
        phone.update({f"{k}.{c[2:]}": v for c, v in row.items() if c.startswith("p_") and v})
    rep.equals("d14:no_email_addresses_outside_masked_notice_text", email, {})
    rep.add("d14:phone_like_patterns_outside_notice_text (reviewed: not contact data)", phone, "reported", True, "warn")
