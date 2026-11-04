"""Data-quality rules for project_details_v3 and project_loan_transactions. "error" blocks writes; "warn" is reported.

Personal-data checks print counts only, never values. Silver `projects`, `ibrd_loans` and `ida_credits` are read for
validation only.
"""
from __future__ import annotations

import json
import re

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.project_details import transform as T

EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
PHONE = r"(?iu)\b(?:tel|t[ée]l[ée]phone|phone|fax|mobile|cell)\b[^0-9+]{0,12}\+?[0-9][0-9 ()./-]{6,}"


def _cnt(df: DataFrame, cond) -> int:
    return df.filter(cond).count()


def _norm(items: list | None) -> list | None:
    """Theme tree without NULL leaves (to_json omits NULLs), recursively; an absent child array stays absent (not [])."""
    if items is None:
        return None
    return [{k: (_norm(v) if k in ("level2", "level3") else v) for k, v in x.items() if v is not None} for x in items]


def personal_values(bronze: DataFrame) -> tuple[set[str], set[str]]:
    """Team-lead names and staff IDs from the source (kept in memory only; never printed)."""
    names, upis = set(), set()
    for (raw,) in bronze.select("raw_record").collect():
        r = json.loads(raw)
        names |= {n.strip() for n in (r.get("teamleadname") or []) if n and len(n.strip()) >= 4}
        if r.get("teamleaderupi"):
            upis.add(str(r["teamleaderupi"]).strip())
    return names, upis


def personal_data_scan(out: dict[str, DataFrame], names: set[str], upis: set[str], rep: DQReport) -> None:
    """Check that no output column or value holds team-lead names, staff ids, e-mail addresses or phone numbers."""
    bad_cols = sorted(f"{k}.{c}" for k, df in out.items() for c in df.columns if re.search(r"teamlead|upi|staff|email|phone", c, re.I))
    rep.equals("personal:no_personal_column_names", bad_cols, [])
    for k, df in out.items():
        payload = [r[0] for r in df.select(F.to_json(F.struct(*[F.col(c) for c in df.columns if c not in ("silver_processed_at",)]))).collect()]
        name_hits = sum(1 for p in payload for n in names if n in p)
        upi_hits = sum(1 for p in payload for u in upis if re.search(rf"(?<![0-9]){re.escape(u)}(?![0-9])", p))
        email_hits = sum(1 for p in payload if re.search(EMAIL, p))
        phone_hits = sum(1 for p in payload if re.search(PHONE.replace("(?iu)", ""), p, re.I))
        rep.equals(f"personal:{k}:team_lead_names_in_any_output_value (incl. nested; counts only)", name_hits, 0)
        rep.equals(f"personal:{k}:staff_ids_in_any_output_value (counts only)", upi_hits, 0)
        rep.equals(f"personal:{k}:email_addresses_in_output", email_hits, 0)
        rep.add(f"personal:{k}:phone_patterns_in_output (reported)", phone_hits, 0, phone_hits == 0, "warn")


def check_details(d: DataFrame, bronze: DataFrame, projects: DataFrame, cohort_ids: set[str], prov: dict, rep: DQReport, expected: int) -> None:
    """Data-quality rules for the project details table (one row per covered cohort project)."""
    n = d.count()
    rep.equals("details:rows_equal_bronze", n, bronze.count())
    rep.equals("details:rows_equal_manifest", n, prov["records"])
    rep.equals("details:rows_equal_expected", n, expected)
    rep.equals("details:project_id_unique", d.select("project_id").distinct().count(), n)
    rep.equals("details:project_id_null_or_bad_format", _cnt(d, F.col("project_id").isNull() | ~F.col("project_id").rlike(r"^P[0-9]{6}$")), 0)
    ids = {r[0] for r in d.select("project_id").collect()}
    rep.equals("details:cohort_membership", sorted(ids - cohort_ids)[:10], [])
    rep.equals("details:project_id_not_in_silver_projects", d.join(projects, "project_id", "left_anti").count(), 0)
    rep.equals("details:bronze_cohort_id_values", [r[0] for r in d.select("bronze_cohort_id").distinct().collect()], ["wbg_poc_2000"])
    rep.equals("details:raw_record_sha256_unique", d.select("raw_record_sha256").distinct().count(), n)
    rep.equals("details:json_keys_known (unknown keys block)", sorted({r[0] for r in d.select(F.explode("_json_keys")).collect()} - set(T.KNOWN_KEYS)), [])
    # nested counts against an independent SQL JSON parse
    for arr, col in (("theme_list", "theme_level1_count"), ("indicators", "indicator_count"), ("geo_locations", "geo_location_count"),
                     ("loan_transactions", "loan_transaction_count")):
        src_len = F.coalesce(F.json_array_length(F.get_json_object("_raw_record", f"$.{arr}")), F.lit(0))
        rep.equals(f"details:{col}_equals_source_array_length", _cnt(d, F.col(col) != src_len), 0)
    for arr, col in (("themev2_level2_exact", "themes_level2"), ("themev2_level3_exact", "themes_level3")):
        rep.equals(f"details:{col}_length_equals_source", _cnt(d, F.coalesce(F.size(col), F.lit(0)) != F.coalesce(F.json_array_length(F.get_json_object("_raw_record", f"$.{arr}")), F.lit(0))), 0)
    # theme hierarchy: counts per level and value fidelity against the source (Python parse)
    records = {r[0]: json.loads(r[1]) for r in d.select("project_id", "_raw_record").collect()}
    got = {r[0]: r[1] for r in d.select("project_id", F.to_json("themes")).collect()}
    mism = 0
    for pid, rec in records.items():
        pct = lambda x: int(x["percent"]) if x.get("percent") is not None else None  # noqa: E731
        exp = [{"code": a["code"], "name": a["name"], "seqno": int(a["seqno"]), "percent": pct(a),
                "level2": None if a.get("theme2") is None else [
                    {"code": b["code"], "name": b["name"], "seqno": int(b["seqno"]), "percent": pct(b),
                     "level3": None if b.get("theme3") is None else [{"code": c["code"], "name": c["name"], "seqno": int(c["seqno"]), "percent": pct(c)}
                                                                    for c in b["theme3"]]} for b in a["theme2"]]} for a in (rec.get("theme_list") or [])]
        g = json.loads(got[pid]) if got[pid] else []
        mism += _norm(g) != _norm(exp)
    rep.equals("details:theme_hierarchy_equals_source (codes, names, seqno, percent, order, levels 1-3)", mism, 0)
    l1 = d.select(F.explode("themes").alias("t"))
    l2 = l1.select(F.explode("t.level2").alias("t"))
    l3 = l2.select(F.explode("t.level3").alias("t"))
    rep.equals("details:theme_elements_per_level", [x.count() for x in (l1, l2, l3)], [604, 907, 1116])
    rep.equals("details:theme_percent_negative (all levels)", sum(_cnt(x, F.col("t.percent") < 0) for x in (l1, l2, l3)), 0)
    # L1 percent is published as (about) the sum of its multi-tagged L2 children, so it can exceed 100: kept as published
    rep.add("details:theme_percent_over_100_by_level (kept as published)", {f"level{i}": _cnt(x, F.col("t.percent") > 100) for i, x in enumerate((l1, l2, l3), 1)},
            "reported", True, "warn")
    # indicators / geo ordering and parsing
    rep.equals("details:indicator_index_contiguous", _cnt(d, ~F.col("results_indicators").isNull()
                                                       & (F.transform("results_indicators", lambda x: x["indicator_index"]) != F.sequence(F.lit(0), F.size("results_indicators") - 1))), 0)
    rep.equals("details:location_index_contiguous", _cnt(d, ~F.col("geo_locations").isNull()
                                                      & (F.transform("geo_locations", lambda x: x["location_index"]) != F.sequence(F.lit(0), F.size("geo_locations") - 1))), 0)
    geo = d.select("project_id", F.explode("geo_locations").alias("g"))
    rep.equals("details:coordinates_parsed_and_in_range", _cnt(geo, F.col("g.latitude").isNull() | F.col("g.longitude").isNull()
                                                               | (F.abs("g.latitude") > 90) | (F.abs("g.longitude") > 180)), 0)
    ind = d.select(F.explode("results_indicators").alias("x"))
    dates = {k: _cnt(ind, F.col(f"x.{k}.date").isNull()) for k in ("baseline", "current", "target")}
    rep.add("details:indicator_dates_null (as published: no date)", dates, "reported", True, "warn")
    rep.add("details:indicator_comments_non_null", _cnt(ind, F.col("x.baseline.comment").isNotNull() | F.col("x.current.comment").isNotNull() | F.col("x.target.comment").isNotNull()),
            "reported", True, "warn")
    rep.equals("details:p2a_date_parse_failures", _cnt(d, F.col("_raw_p2a_date").isNotNull() & F.col("p2a_updated_date").isNull()), 0)
    risk = {r[0]: r[1] for r in d.groupBy("environmental_social_risk_rating").count().collect()}
    rep.equals("details:risk_rating_vocabulary", sorted(str(k) for k in risk if k is not None and k not in T.RISK_RATINGS), [])
    rep.add("details:risk_rating_counts (latest snapshot)", {str(k): v for k, v in risk.items()}, "reported", True, "warn")
    rep.add("details:product_line_counts", {r[0]: r[1] for r in d.groupBy("product_line_code").count().collect()}, "reported", True, "warn")
    rep.equals("details:parent_project_id_format", _cnt(d, F.col("parent_project_id").isNotNull() & ~F.col("parent_project_id").rlike(r"^P[0-9]{6}$")), 0)


def check_transactions(t: DataFrame, details: DataFrame, ibrd: DataFrame, ida: DataFrame, rep: DQReport, expected: int) -> dict:
    """Data-quality rules for the loan transactions; returns how their references match the IBRD and IDA tables."""
    n = t.count()
    rep.equals("transactions:rows_equal_expected", n, expected)
    rep.equals("transactions:rows_equal_sum_of_source_arrays", n, details.agg(F.sum("loan_transaction_count")).collect()[0][0])
    rep.equals("transactions:transaction_key_unique", t.select("transaction_key").distinct().count(), n)
    rep.equals("transactions:index_contiguous_per_project (original order)",
               t.groupBy("project_id").agg(F.min("transaction_index").alias("mn"), F.max("transaction_index").alias("mx"), F.count("*").alias("c"))
                .filter("mn != 0 OR mx != c - 1").count(), 0)
    rep.equals("transactions:projects_subset_of_details", t.join(details.select("project_id"), "project_id", "left_anti").count(), 0)
    rep.equals("transactions:financing_number_is_reference_without_hyphen", _cnt(t, F.col("financing_number") != F.regexp_replace("financier_loan", "-", "")), 0)
    rep.equals("transactions:null_core_fields", _cnt(t, F.col("financier_loan").isNull() | F.col("transaction_type").isNull() | F.col("amount_published").isNull()
                                                      | F.col("transaction_period_published").isNull()), 0)
    rep.equals("transactions:transaction_type_vocabulary", sorted(r[0] for r in t.select("transaction_type").distinct().collect() if r[0] not in T.TRANSACTION_TYPES), [])
    rep.equals("transactions:amount_unambiguous_numeric", _cnt(t, ~F.col("_amount_unambiguous")), 0)
    rep.equals("transactions:amount_exact_to_published (no rounding)", _cnt(t, ~F.col("amount_published").cast("decimal(38,10)").eqNullSafe(F.col("amount").cast("decimal(38,10)"))), 0)
    rep.equals("transactions:date_parse_failures (non-placeholder)", _cnt(t, ~F.col("transaction_date_is_placeholder") & F.col("transaction_date").isNull()), 0)
    rep.equals("transactions:placeholder_rows", _cnt(t, "transaction_date_is_placeholder"), 1)
    rep.equals("transactions:period_time_component_always_midnight", _cnt(t, ~F.col("transaction_period_published").rlike(r"T00:00:00(\.000)?Z$")), 0)
    rep.add("transactions:negative_amounts_by_type (kept)", {r[0]: r[1] for r in t.filter("amount < 0").groupBy("transaction_type").count().collect()}, "reported", True, "warn")
    rep.add("transactions:identical_rows (kept, not de-duplicated)", {"rows_in_groups_gt1": _cnt(t, "identical_transaction_group_size > 1"),
                                                                       "rows_beyond_first": t.filter("identical_transaction_group_size > 1")
                                                                       .groupBy("project_id", "financier_loan", "transaction_period_published", "transaction_type", "amount_published")
                                                                       .agg(F.count("*").alias("c")).agg(F.sum(F.col("c") - 1)).collect()[0][0]}, "reported", True, "warn")
    rep.add("transactions:dated_after_financing_snapshot_2026_09_30", _cnt(t, "transaction_date > DATE'2026-09-30'"), "reported", True, "warn")
    fin = ibrd.select(F.col("loan_number").alias("financing_number"), F.col("project_id").alias("fin_project"), F.lit("ibrd_loans").alias("fin_table")) \
        .unionByName(ida.select(F.col("credit_number").alias("financing_number"), F.col("project_id").alias("fin_project"), F.lit("ida_credits").alias("fin_table")))
    refs = t.select("project_id", "financier_loan", "financing_number").distinct().join(fin, "financing_number", "left")
    matched = refs.filter("fin_table IS NOT NULL")
    rep.equals("transactions:references_matching_financing_tables", matched.count(), 227)
    rep.equals("transactions:matched_references_same_project", _cnt(matched, F.col("fin_project") != F.col("project_id")), 0)
    unmatched = sorted(r[0] for r in refs.filter("fin_table IS NULL").select("financier_loan").collect())
    rep.equals("transactions:unmatched_references_preserved (not forced into IBRD/IDA)", unmatched, ["IBRD-99510", "TF-A9875", "TF-C3832"])
    return {"unmatched_references": unmatched, "matched_references": matched.count(), "distinct_references": refs.count()}
