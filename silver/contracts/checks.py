"""Data-quality rules for the contract-award Silver tables. "error" blocks writes; "warn" is reported."""
from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from silver.common.dq import DQReport
from silver.contracts.transform import CONTRACT_LEVEL


def _cnt(df: DataFrame, cond) -> int:
    return df.filter(cond).count()


def check_lines(lines: DataFrame, bronze: DataFrame, projects: DataFrame, rep: DQReport) -> None:
    """Data-quality rules for the DS00005 supplier lines (contract_award_lines)."""
    n = lines.count()
    rep.equals("lines:rows_equal_bronze", n, bronze.count())
    rep.equals("lines:line_key_unique", lines.select("line_key").distinct().count(), n)
    rep.equals("lines:wb_contract_number_null", _cnt(lines, F.col("wb_contract_number").isNull()), 0)
    rep.equals("lines:wb_contract_number_numeric", _cnt(lines, ~F.col("wb_contract_number").rlike(r"^\d+$")), 0)
    rep.equals("lines:one_line_per_supplier_id_within_contract",
               lines.filter("supplier_id IS NOT NULL").groupBy("wb_contract_number", "supplier_id").count().filter("count > 1").count(), 0)
    rep.equals("lines:project_id_not_in_silver_projects", lines.join(projects, "project_id", "left_anti").count(), 0)
    rep.equals("lines:silver_projects_without_lines", projects.join(lines.select("project_id").distinct(), "project_id", "left_anti").count(), 0)
    # NULLs exactly as in Bronze (empty CSV fields)
    pairs = {"supplier_id": "Supplier ID", "supplier_amount_usd": "Supplier Contract Amount (USD)", "review_type": "Review type",
             "borrower_country_code": "Borrower Country / Economy Code", "global_practices_published": "Project Global Practice",
             "borrower_contract_reference": "Borrower Contract Reference Number", "contract_signing_date": "Contract Signing Date",
             "supplier_country_code": "Supplier Country / Economy Code"}
    rep.equals("lines:null_counts_equal_bronze", {k: _cnt(lines, F.col(k).isNull()) for k in pairs},
               {k: _cnt(bronze, F.col(f"`{v}`").isNull()) for k, v in pairs.items()})
    # dates and years
    rep.equals("lines:signing_date_parse_failures", _cnt(lines, F.col("_raw_signing_date").isNotNull() & F.col("contract_signing_date").isNull()), 0)
    rep.equals("lines:source_as_of_date_values", [str(r[0]) for r in lines.select("source_as_of_date").distinct().collect()], ["2026-10-20"])
    rep.equals("lines:calendar_year_consistent", _cnt(lines, F.year("contract_signing_date") != F.col("contract_signed_calendar_year")), 0)
    rep.equals("lines:fiscal_year_consistent (WB FY Jul-Jun)",
               _cnt(lines, (F.year("contract_signing_date") + F.when(F.month("contract_signing_date") >= 7, 1).otherwise(0)) != F.col("fiscal_year")), 0)
    rng = lines.agg(F.min("contract_signing_date"), F.max("contract_signing_date")).collect()[0]
    rep.add("lines:signing_date_range", [str(rng[0]), str(rng[1])], "reported", True, "warn")
    rep.add("lines:signing_after_as_of_date", _cnt(lines, F.col("contract_signing_date") > F.col("source_as_of_date")), 0,
            _cnt(lines, F.col("contract_signing_date") > F.col("source_as_of_date")) == 0, "warn")
    # amounts: parse, zero vs NULL, sign, reconciliation
    rep.equals("lines:amount_parse_failures", _cnt(lines, F.col("_raw_amount").isNotNull() & F.col("supplier_amount_usd").isNull()), 0)
    rep.equals("lines:amount_zero_preserved", _cnt(lines, F.col("supplier_amount_usd") == 0), _cnt(bronze, F.col("`Supplier Contract Amount (USD)`").cast("double") == 0))
    rep.equals("lines:amount_null_preserved", _cnt(lines, "is_amount_missing"), _cnt(bronze, F.col("`Supplier Contract Amount (USD)`").isNull()))
    rep.equals("lines:negative_amounts", _cnt(lines, F.col("supplier_amount_usd") < 0), 0)
    rep.equals("lines:amount_sum_equals_bronze", str(lines.agg(F.sum("supplier_amount_usd")).collect()[0][0]),
               str(bronze.agg(F.sum(F.col("`Supplier Contract Amount (USD)`").cast("decimal(22,2)"))).collect()[0][0]))
    # supplier identity
    types = {r[0]: r[1] for r in lines.groupBy("supplier_id_type").count().collect()}
    rep.add("lines:supplier_id_types", types, "reported", True, "warn")
    rep.equals("lines:anonymized_ids_are_individual_consultants",
               _cnt(lines, (F.col("supplier_id_type") == "anonymized_individual_consultant") & ((F.col("supplier_name") != "INDIVIDUAL CONSULTANT")
                                                                                              | (F.col("procurement_method") != "Individual Consultant Selection"))), 0)
    rep.add("lines:missing_supplier_id_rows (kept as published)",
            [tuple(r) for r in lines.filter("supplier_id IS NULL").select("wb_contract_number", "supplier_name", "supplier_amount_usd").collect()], "reported", True, "warn")
    rep.add("lines:supplier_counts", {"distinct_supplier_ids": lines.select("supplier_id").distinct().count(),
                                      "distinct_supplier_names": lines.select("supplier_name").distinct().count(),
                                      "ids_with_gt1_name": lines.groupBy("supplier_id").agg(F.countDistinct("supplier_name").alias("n")).filter("n > 1").count(),
                                      "names_with_gt1_id": lines.filter("supplier_id_type = 'numeric'").groupBy("supplier_name").agg(F.countDistinct("supplier_id").alias("n")).filter("n > 1").count()},
            "reported (identity resolution deferred, D-06)", True, "warn")
    # borrower country vs project (reported, never remapped)
    pj = lines.join(projects.select("project_id", F.col("country_code").alias("p_cc")), "project_id")
    rep.add("lines:borrower_code_vs_project_code", {r[0]: r[1] for r in pj.select(
        F.when(F.col("borrower_country_code").isNull(), "borrower_code_null").when(F.col("borrower_country_code") == F.col("p_cc"), "equal")
         .otherwise("different").alias("k")).groupBy("k").count().collect()}, "reported (kept as published)", True, "warn",
            "; ".join(f"{r[0]}->{r[1]} x{r[2]}" for r in pj.filter("borrower_country_code != p_cc").groupBy("borrower_country_code", "p_cc").count()
                      .orderBy(F.desc("count")).limit(8).collect()))
    rep.equals("lines:source_row_sha256_unique", lines.select("source_row_sha256").distinct().count(), n)


def check_contracts(contracts: DataFrame, lines: DataFrame, rep: DQReport) -> None:
    """Data-quality rules for the contract-level table built from the supplier lines."""
    n = contracts.count()
    rep.equals("contracts:rows_equal_distinct_wb_contract_numbers", n, lines.select("wb_contract_number").distinct().count())
    rep.equals("contracts:key_unique", contracts.select("wb_contract_number").distinct().count(), n)
    rep.equals("contracts:contract_level_fields_identical_across_lines",
               {c: _cnt(contracts, F.col(f"_distinct_{c}") > 1) for c in CONTRACT_LEVEL if c != "global_practices"},
               {c: 0 for c in CONTRACT_LEVEL if c != "global_practices"})
    rep.equals("contracts:supplier_line_count_total_equals_lines", contracts.agg(F.sum("supplier_line_count")).collect()[0][0], lines.count())
    rep.equals("contracts:amount_total_equals_lines_total (no double counting)",
               str(contracts.agg(F.sum("contract_amount_usd")).collect()[0][0]), str(lines.agg(F.sum("supplier_amount_usd")).collect()[0][0]))
    per_proj = contracts.groupBy("project_id").agg(F.sum("contract_amount_usd").alias("a")).join(
        lines.groupBy("project_id").agg(F.sum("supplier_amount_usd").alias("b")), "project_id")
    rep.equals("contracts:amount_equals_lines_per_project", _cnt(per_proj, ~F.col("a").eqNullSafe(F.col("b"))), 0)
    rep.add("contracts:multi_supplier", {"contracts": _cnt(contracts, "is_multi_supplier"),
                                         "lines": contracts.filter("is_multi_supplier").agg(F.sum("supplier_line_count")).collect()[0][0],
                                         "max_lines": contracts.agg(F.max("supplier_line_count")).collect()[0][0]}, "reported", True, "warn")
    rep.add("contracts:with_missing_line_amount", [r[0] for r in contracts.filter("supplier_lines_missing_amount > 0").select("wb_contract_number").collect()],
            "reported", True, "warn")
    shared = contracts.filter("contracts_sharing_reference_in_project > 1")
    rep.add("contracts:sharing_project_reference (lots/repeats/frameworks; not interpreted)",
            {"contracts": shared.count(), "keys": shared.select("project_id", "borrower_contract_reference").distinct().count(),
             "max_per_key": contracts.agg(F.max("contracts_sharing_reference_in_project")).collect()[0][0]}, "reported", True, "warn")
    rep.equals("contracts:reference_null", _cnt(contracts, F.col("borrower_contract_reference").isNull()), 0)


def check_legacy(legacy: DataFrame, bronze: DataFrame, projects: DataFrame, ds5_contracts: DataFrame, rep: DQReport) -> None:
    """Data-quality rules for the DS01004 legacy contracts (contract_awards_legacy)."""
    n = legacy.count()
    rep.equals("legacy:rows_equal_bronze", n, bronze.count())
    rep.equals("legacy:contract_number_unique", legacy.select("contract_number").distinct().count(), n)
    rep.equals("legacy:contract_number_numeric", _cnt(legacy, ~F.col("contract_number").rlike(r"^\d+$")), 0)
    rep.equals("legacy:project_id_not_in_silver_projects", legacy.join(projects, "project_id", "left_anti").count(), 0)
    rep.equals("legacy:signing_date_parse_failures", _cnt(legacy, F.col("_raw_signing_date").isNotNull() & F.col("contract_signing_date").isNull()), 0)
    rep.equals("legacy:fiscal_year_consistent", _cnt(legacy, (F.year("contract_signing_date") + F.when(F.month("contract_signing_date") >= 7, 1).otherwise(0)) != F.col("fiscal_year")), 0)
    rep.equals("legacy:calendar_year_consistent", _cnt(legacy, F.year("contract_signing_date") != F.col("contract_signed_calendar_year")), 0)
    rep.equals("legacy:amount_parse_failures", _cnt(legacy, F.col("_raw_amount").isNotNull() & F.col("contract_amount_usd").isNull()), 0)
    rep.equals("legacy:amount_zero_preserved", _cnt(legacy, F.col("contract_amount_usd") == 0), _cnt(bronze, F.col("`Supplier Contract Amount (US$)`").cast("double") == 0))
    rep.equals("legacy:negative_amounts", _cnt(legacy, F.col("contract_amount_usd") < 0), 0)
    rep.equals("legacy:amount_sum_equals_bronze", str(legacy.agg(F.sum("contract_amount_usd")).collect()[0][0]),
               str(bronze.agg(F.sum(F.col("`Supplier Contract Amount (US$)`").cast("decimal(22,2)"))).collect()[0][0]))
    rep.equals("legacy:null_counts_equal_bronze", {"borrower_contract_reference": _cnt(legacy, F.col("borrower_contract_reference").isNull()),
                                                   "procurement_method": _cnt(legacy, F.col("procurement_method").isNull())},
               {"borrower_contract_reference": _cnt(bronze, F.col("`Borrower Contract Reference Number`").isNull()),
                "procurement_method": _cnt(bronze, F.col("`Procurement Method`").isNull())})
    rep.equals("legacy:published_text_preserved (normalised copy only collapses whitespace)",
               _cnt(legacy, F.regexp_replace("contract_description", r"\s+", "") != F.regexp_replace("contract_description_normalized", r"\s+", "")), 0)
    rng = legacy.agg(F.min("contract_signing_date"), F.max("contract_signing_date")).collect()[0]
    rep.add("legacy:signing_date_range", [str(rng[0]), str(rng[1])], "reported", True, "warn")
    rep.equals("legacy:source_as_of_date_values", [str(r[0]) for r in legacy.select("source_as_of_date").distinct().collect()], ["2022-03-11"])
    ov_num = legacy.select(F.col("contract_number").alias("n")).intersect(ds5_contracts.select(F.col("wb_contract_number").alias("n"))).count()
    rep.add("legacy:contract_numbers_shared_with_ds00005", ov_num, 0, ov_num == 0, "warn", "0 = no evidence of the same contract in both sources")
    ov_ref = legacy.filter("borrower_contract_reference IS NOT NULL").select("project_id", "borrower_contract_reference").intersect(
        ds5_contracts.select("project_id", "borrower_contract_reference"))
    rep.add("legacy:project_reference_pairs_also_in_ds00005 (different contract numbers and dates; references are reused)",
            [tuple(r) for r in ov_ref.collect()], "reported, not merged", True, "warn")
    rep.add("legacy:text_fields_with_embedded_line_breaks", {"contract_description": _cnt(legacy, F.col("contract_description").contains("\n")),
                                                             "supplier_text": _cnt(legacy, F.col("supplier_text").contains("\n")),
                                                             "project_name_source": _cnt(legacy, F.col("project_name_source").contains("\n"))},
            "kept as published; *_normalized collapses whitespace", True, "warn")
    rep.equals("legacy:source_row_sha256_unique", legacy.select("source_row_sha256").distinct().count(), n)
