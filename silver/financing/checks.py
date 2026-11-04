"""Data-quality rules for the IBRD loans / IDA credits Silver tables. "error" blocks writes; "warn" is reported.

Anomalies are reported (and listed by financing number in known_anomalies()), never corrected or removed.
Silver `projects` is used for validation only.
"""
from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.financing import transform as T


def _cnt(df: DataFrame, cond) -> int:
    return df.filter(cond).count()


def check(df: DataFrame, bronze: DataFrame, source: str, projects: DataFrame, cohort_ids: set[str], prov: dict, rep: DQReport, expected_rows: int) -> None:
    """Run every data-quality rule for one financing table (IBRD or IDA) and add the results to `rep`."""
    spec = T.SOURCES[source]
    key, p = spec["key"], f"{source}:"
    n = df.count()
    # rows, keys, cohort
    rep.equals(p + "rows_equal_bronze", n, bronze.count())
    rep.equals(p + "rows_equal_manifest", n, prov["records"])
    rep.equals(p + "rows_equal_expected", n, expected_rows)
    rep.equals(p + "key_unique", df.select(key).distinct().count(), n)
    rep.equals(p + "key_null_or_bad_format", _cnt(df, F.col(key).isNull() | ~F.col(key).rlike(spec["key_regex"])), 0)
    rep.equals(p + "prefix_matches_key", _cnt(df, F.col("financing_number_prefix") != F.regexp_extract(key, r"^([A-Z]+)", 1)), 0)
    rep.equals(p + "project_id_null_or_bad_format", _cnt(df, F.col("project_id").isNull() | ~F.col("project_id").rlike(r"^P[0-9]{6}$")), 0)
    ids = {r[0] for r in df.select("project_id").distinct().collect()}
    rep.equals(p + "cohort_membership", sorted(ids - cohort_ids)[:10], [])
    rep.equals(p + "project_id_not_in_silver_projects", df.join(projects, "project_id", "left_anti").count(), 0)
    rep.equals(p + "bronze_cohort_id_values", [r[0] for r in df.select("bronze_cohort_id").distinct().collect()], ["wbg_poc_2000"])
    rep.equals(p + "source_row_sha256_unique", df.select("source_row_sha256").distinct().count(), n)
    rep.equals(p + "source_row_hashes_equal_bronze", df.select("source_row_sha256").exceptAll(bronze.select(T.row_hash_expr(T.source_columns(bronze)))).count(), 0)
    # snapshot and dates
    rep.equals(p + "end_of_period_values", [str(r[0]) for r in df.select("source_end_of_period").distinct().collect()], ["2026-09-30"])
    rep.equals(p + "end_of_period_parse_failures", _cnt(df, F.col("_raw_eop").isNotNull() & F.col("source_end_of_period").isNull()), 0)
    parse_fail = {}
    for src, out in T.DATES.items():
        raw = F.col(f"_raw_{out}")
        ok_placeholder = (raw == T.PLACEHOLDER_DATE) if src in T.PLACEHOLDER_DATES else F.lit(False)
        parse_fail[out] = _cnt(df, raw.isNotNull() & F.col(out).isNull() & ~ok_placeholder)
    rep.equals(p + "date_parse_failures", parse_fail, {o: 0 for o in T.DATES.values()})
    rep.equals(p + "placeholder_1900_outside_signing_effective", {out: _cnt(df, F.col(f"_raw_{out}") == T.PLACEHOLDER_DATE) for src, out in T.DATES.items()
                                                                  if src not in T.PLACEHOLDER_DATES}, {out: 0 for src, out in T.DATES.items() if src not in T.PLACEHOLDER_DATES})
    rep.equals(p + "placeholder_flags_consistent", {flag: _cnt(df, F.col(flag) != (F.col(f"_raw_{T.DATES[src]}") == T.PLACEHOLDER_DATE))
                                                    for src, flag in T.PLACEHOLDER_DATES.items()}, {flag: 0 for flag in T.PLACEHOLDER_DATES.values()})
    rep.equals(p + "placeholder_dates_are_null", _cnt(df, (F.col("agreement_signing_date_is_placeholder") & F.col("agreement_signing_date").isNotNull())
                                                      | (F.col("effective_date_is_placeholder") & F.col("effective_date_most_recent").isNotNull())), 0)
    rep.add(p + "placeholder_counts_by_status", {f"{r[0]}|signing={r[1]}|effective={r[2]}": r[3] for r in df.filter("agreement_signing_date_is_placeholder OR effective_date_is_placeholder")
                                                 .groupBy("financing_status", "agreement_signing_date_is_placeholder", "effective_date_is_placeholder").count().collect()},
            "reported", True, "warn")
    rep.equals(p + "approval_year_parse_failures", _cnt(df, (F.col("_raw_fy").isNotNull() & F.col("board_approval_fiscal_year").isNull())
                                                        | (F.col("_raw_cy").isNotNull() & F.col("board_approval_calendar_year").isNull())), 0)
    fy = F.year("board_approval_date") + F.when(F.month("board_approval_date") >= 7, 1).otherwise(0)
    rep.equals(p + "approval_years_consistent", _cnt(df, (F.col("board_approval_fiscal_year") != fy) | (F.col("board_approval_calendar_year") != F.year("board_approval_date"))), 0)
    # amounts and rates: exact, sign-preserving
    amt_fail, amt_inexact = {}, {}
    for out in spec["amounts"].values():
        raw = F.col(f"_raw_{out}")
        amt_fail[out] = _cnt(df, raw.isNotNull() & F.col(out).isNull())
        amt_inexact[out] = _cnt(df, raw.isNotNull() & ~raw.cast("decimal(38,10)").eqNullSafe(F.col(out).cast("decimal(38,10)")))
    rep.equals(p + "amount_parse_failures", amt_fail, {o: 0 for o in spec["amounts"].values()})
    rep.equals(p + "amount_exact_to_published (decimal(22,2), no rounding)", amt_inexact, {o: 0 for o in spec["amounts"].values()})
    rate = "interest_rate" if source == "ibrd" else "service_charge_rate"
    rep.equals(p + f"{rate}_parse_or_precision_failures", _cnt(df, F.col("_raw_rate").isNotNull() & ~F.col("_raw_rate").cast("decimal(38,10)").eqNullSafe(F.col(rate).cast("decimal(38,10)"))), 0)
    rep.equals(p + "non_negative_fields_have_no_negatives (CHECK-constraint compatible)", {c: _cnt(df, F.col(c) < 0) for c in T.NON_NEGATIVE}, {c: 0 for c in T.NON_NEGATIVE})
    neg = {c: _cnt(df, F.col(c) < 0) for c in spec["amounts"].values() if c not in T.NON_NEGATIVE}
    rep.add(p + "published_negative_values (kept; not constrained)", {c: v for c, v in neg.items() if v}, "reported", True, "warn")
    # vocabularies
    rep.equals(p + "status_vocabulary", sorted(r[0] for r in df.select("financing_status").distinct().collect() if r[0] not in spec["statuses"]), [])
    if source == "ibrd":
        rep.equals(p + "loan_type_vocabulary", sorted(str(r[0]) for r in df.select("loan_type").distinct().collect() if r[0] not in T.IBRD_LOAN_TYPES), [])
        rep.add(p + "currency_of_commitment_published", _cnt(df, F.col("currency_of_commitment").isNotNull()), 0,
                _cnt(df, F.col("currency_of_commitment").isNotNull()) == 0, "warn", "IBRD currency is not published (NULL, not assumed USD)")
    else:
        bad_ccy = sorted(str(r[0]) for r in df.select("currency_of_commitment").distinct().collect() if r[0] not in T.IDA_CURRENCIES)
        rep.equals(p + "currency_vocabulary", bad_ccy, [])
        rep.equals(p + "repayment_dates_both_or_neither", _cnt(df, F.col("first_repayment_date").isNull() != F.col("last_repayment_date").isNull()), 0)
        rep.equals(p + "has_repayment_schedule_consistent", _cnt(df, F.col("has_repayment_schedule") != (F.col("first_repayment_date").isNotNull() & F.col("last_repayment_date").isNotNull())), 0)
        rep.add(p + "repayment_schedule_by_prefix (prefixes not classified)", {f"{r[0]}|{r[1]}": r[2] for r in df.groupBy("financing_number_prefix", "has_repayment_schedule").count().collect()},
                "reported", True, "warn")
    # NULLs exactly as Bronze blanks (placeholder dates add NULLs, counted separately)
    pairs = {out: src for src, out in spec["amounts"].items()} | {"currency_of_commitment": "Currency of Commitment", "project_name_source": "Project Name",
                                                                  "borrower_name": "Borrower", "country_code": "Country / Economy Code"}
    rep.equals(p + "null_counts_equal_bronze (non-date)", {k: _cnt(df, F.col(k).isNull()) for k in pairs}, {k: _cnt(bronze, F.col(f"`{v}`").isNull()) for k, v in pairs.items()})
    exp_dates = {out: _cnt(bronze, F.col(f"`{src}`").isNull()) + (_cnt(bronze, F.col(f"`{src}`") == T.PLACEHOLDER_DATE) if src in T.PLACEHOLDER_DATES else 0)
                 for src, out in T.DATES.items()}
    rep.equals(p + "null_counts_equal_bronze (dates: blank + placeholder)", {o: _cnt(df, F.col(o).isNull()) for o in T.DATES.values()}, exp_dates)
    rep.equals(p + "no_project_level_aggregates (one row per instrument)", df.groupBy(key).count().filter("count > 1").count(), 0)


def known_anomalies(df: DataFrame, source: str, projects: DataFrame) -> dict:
    """Financing numbers per anomaly class (reported separately; source values unchanged). Silver projects: validation only."""
    key = T.SOURCES[source]["key"]
    ids = lambda d: sorted(r[0] for r in d.select(key).collect())  # noqa: E731
    p = projects.select("project_id", F.col("country_code").alias("p_country"), "geography_type", F.col("board_approval_date").alias("p_approval"),
                        F.col("closing_date").alias("p_closing"))
    j = df.join(p, "project_id", "left")
    out = {
        "negative_values": {c: ids(df.filter(F.col(c) < 0)) for c in T.SOURCES[source]["amounts"].values() if df.filter(F.col(c) < 0).count()},
        "agreement_signing_date_placeholder": ids(df.filter("agreement_signing_date_is_placeholder")),
        "effective_date_placeholder": ids(df.filter("effective_date_is_placeholder")),
        "last_disbursement_after_end_of_period": ids(df.filter("last_disbursement_date > source_end_of_period")),
        "last_disbursement_more_than_183_days_after_closed_date": ids(df.filter("last_disbursement_date > date_add(closed_date_most_recent, 183)")),
        "approved_before_project_board_approval": ids(j.filter("board_approval_date < p_approval")),
        "approved_after_project_board_approval_count": j.filter("board_approval_date > p_approval").count(),
        "closed_date_differs_from_project_closing_count": j.filter("closed_date_most_recent != p_closing").count(),
        "original_principal_zero": ids(df.filter("original_principal_amount_usd = 0")),
        "projects_with_multiple_instruments": {r[0]: r[1] for r in df.groupBy("project_id").count().filter("count > 1").collect()},
        "country_differs_from_project": {
            "curated_alias_or_other_single_country": ids(j.filter("country_code != p_country AND geography_type != 'regional_multicountry'")),
            "regional_project_borrower_countries (D-29 evidence; not applied)":
                {r[0]: sorted(r[1]) for r in j.filter("geography_type = 'regional_multicountry'").groupBy("project_id").agg(F.collect_set("country_code")).collect()},
        },
    }
    return out
