"""IBRD loans (DS00047) and IDA credits (DS00001): Bronze -> two source-specific Silver tables.

Sources (CSV ingested with original column names; empty fields arrive as NULL), monthly statements as of 09/30/2026:
  workspace.wbg_bronze.bronze_ibrd_loans_ds00047    612 loans    (516 cohort projects)
  workspace.wbg_bronze.bronze_ida_credits_ds00001   2,073 credits (1,129 cohort projects)
See silver/runs/financing_discovery_20261023/FINANCING_DISCOVERY_REPORT.md.

Rules (approved 2026-10-23):
  * Grain: one row per published loan / credit number (financing commitment, NOT a procurement contract award).
  * Amounts: the 12 published US$ fields are cast exactly to decimal(22,2), signs preserved. No currency conversion,
    no reconciliation formula, no derived amount. For non-USD commitments the published US$ fields use different
    exchange-rate bases, so they must not be combined arithmetically.
  * Currency: IBRD publishes none -> NULL (not assumed USD). IDA as published.
  * Dates: MM/dd/yyyy. The 01/01/1900 placeholder (only on Agreement Signing / Effective dates of not-yet-signed or
    not-yet-effective records) -> NULL date + explicit *_is_placeholder flag; the original value stays in Bronze and in
    source_row_sha256.
  * IDA: financing_number_prefix kept as published (letters before the digits); has_repayment_schedule = both published
    repayment dates present. Prefixes are NOT classified as grants.
  * Snapshot: balances, status and "most recent" dates are as of source_end_of_period (2026-09-30), not as of approval.
  * Shared concepts use the same column names in both tables (lender-specific source names -> *_lender_usd, *_amount_usd).
"""
from __future__ import annotations

from app_config.paths import BRONZE_INPUTS_MANIFEST, SOURCE_PULLS_CACHE_MANIFEST
import json
from pathlib import Path

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from silver.common.pipeline import CATALOG, manifest
from silver.common.sourcefiles import row_hash_expr

TRANSFORM_VERSION = "financing_silver_v1.0.0"
REPO = Path(__file__).resolve().parents[2]
BRONZE_INPUTS_MANIFEST = manifest("WBG_BRONZE_INPUTS_MANIFEST", BRONZE_INPUTS_MANIFEST)
PHASE1_CACHE_MANIFEST = manifest("WBG_SOURCE_PULLS_MANIFEST", SOURCE_PULLS_CACHE_MANIFEST)
META = ("source_file", "cohort_id", "ingested_at")
DEC_AMOUNT, DEC_RATE = "decimal(22,2)", "decimal(12,6)"
PLACEHOLDER_DATE = "01/01/1900"
SNAPSHOT_SEMANTICS = "latest_monthly_snapshot_as_of_end_of_period"

# Vocabularies observed in the FULL source populations (9,538 IBRD / 11,486 IDA rows), not only the cohort.
IBRD_STATUSES = ["Approved", "Disbursing", "Disbursing&Repaying", "Effective", "Fully Cancelled", "Fully Disbursed", "Fully Repaid",
                 "Fully Transferred", "Repaying", "Signed", "Terminated"]
IDA_STATUSES = ["Approved", "Disbursing", "Disbursing&Repaying", "Effective", "Fully Cancelled", "Fully Disbursed", "Fully Repaid",
                "Repaying", "Signed", "Terminated"]
IBRD_LOAN_TYPES = ["BLNC", "BLNR", "CPL", "FSL", "GUBF", "GURB", "NPL", "SCL", "SCPD", "SCPM", "SCPY"]
IDA_CURRENCIES = ["EUR", "GBP", "JPY", "USD", "XAF", "XDR"]

SOURCES: dict[str, dict] = {
    "ibrd": {
        "bronze": f"{CATALOG}.wbg_bronze.bronze_ibrd_loans_ds00047", "bronze_short": "bronze_ibrd_loans_ds00047", "dataset": "DS00047",
        "lender": "IBRD", "key_src": "Loan Number", "key": "loan_number", "key_regex": r"^IBRD[A-Z]?[0-9]+$", "status_src": "Loan Status",
        "statuses": IBRD_STATUSES,
        "amounts": {"Original Principal Amount (US$)": "original_principal_amount_usd", "Cancelled Amount (US$)": "cancelled_amount_usd",
                    "Undisbursed Amount (US$)": "undisbursed_amount_usd", "Disbursed Amount (US$)": "disbursed_amount_usd",
                    "Repaid to IBRD (US$)": "repaid_to_lender_usd", "Due to IBRD (US$)": "due_to_lender_usd",
                    "Exchange Adjustment (US$)": "exchange_adjustment_usd", "Borrower's Obligation (US$)": "borrower_obligation_usd",
                    "Sold 3rd Party (US$)": "sold_third_party_usd", "Repaid 3rd Party (US$)": "repaid_third_party_usd",
                    "Due 3rd Party (US$)": "due_third_party_usd", "Loans Held (US$)": "held_amount_usd"},
    },
    "ida": {
        "bronze": f"{CATALOG}.wbg_bronze.bronze_ida_credits_ds00001", "bronze_short": "bronze_ida_credits_ds00001", "dataset": "DS00001",
        "lender": "IDA", "key_src": "Credit Number", "key": "credit_number", "key_regex": r"^IDA[A-Z]?[0-9]+$", "status_src": "Credit Status",
        "statuses": IDA_STATUSES,
        "amounts": {"Original Principal Amount (US$)": "original_principal_amount_usd", "Cancelled Amount (US$)": "cancelled_amount_usd",
                    "Undisbursed Amount (US$)": "undisbursed_amount_usd", "Disbursed Amount (US$)": "disbursed_amount_usd",
                    "Repaid to IDA (US$)": "repaid_to_lender_usd", "Due to IDA (US$)": "due_to_lender_usd",
                    "Exchange Adjustment (US$)": "exchange_adjustment_usd", "Borrowers Obligation (US$)": "borrower_obligation_usd",
                    "Sold 3rd Party (US$)": "sold_third_party_usd", "Repaid 3rd Party (US$)": "repaid_third_party_usd",
                    "Due 3rd Party (US$)": "due_third_party_usd", "Credits Held (US$)": "held_amount_usd"},
    },
}
# Fields with 0 negative values in both full populations: safe for a non-negative CHECK constraint. Undisbursed (rounding
# -0.01), due, obligation, held and exchange adjustment have valid published negatives and are NOT constrained.
NON_NEGATIVE = ["original_principal_amount_usd", "cancelled_amount_usd", "disbursed_amount_usd", "repaid_to_lender_usd",
                "sold_third_party_usd", "repaid_third_party_usd", "due_third_party_usd"]
DATES = {"First Repayment Date": "first_repayment_date", "Last Repayment Date": "last_repayment_date",
         "Agreement Signing Date": "agreement_signing_date", "Board Approval Date": "board_approval_date",
         "Effective Date (Most Recent)": "effective_date_most_recent", "Closed Date (Most Recent)": "closed_date_most_recent",
         "Last Disbursement Date": "last_disbursement_date"}
PLACEHOLDER_DATES = {"Agreement Signing Date": "agreement_signing_date_is_placeholder", "Effective Date (Most Recent)": "effective_date_is_placeholder"}


def source_columns(df: DataFrame) -> list[str]:
    return [c for c in df.columns if c not in META]


def load_provenance(source: str) -> dict:
    m = json.loads(BRONZE_INPUTS_MANIFEST.read_text())
    e = next(f for f in m["files"] if f["bronze_table"] == SOURCES[source]["bronze_short"])
    src_name = Path(e["source_file"]).name
    pull = next(json.loads(ln) for ln in PHASE1_CACHE_MANIFEST.read_text().splitlines() if src_name in ln)
    return {"bronze_input_file_sha256": e["output_sha256"], "records": int(e["records_selected"]), "source_population_sha256": e["source_sha256"],
            "source_retrieved_at": pull["extracted_at_utc"], "source_url_note": pull["source"], "source_population_records": int(e["source_records"])}


def _c(name: str) -> Column:
    return F.col(f"`{name}`")


def _date(name: str) -> Column:
    d = F.to_date(_c(name), "MM/dd/yyyy")
    return F.when(_c(name) == PLACEHOLDER_DATE, F.lit(None).cast("date")).otherwise(d) if name in PLACEHOLDER_DATES else d


def build(bronze: DataFrame, source: str, prov: dict, run_id: str) -> DataFrame:
    spec = SOURCES[source]
    cols = source_columns(bronze)
    key = _c(spec["key_src"])
    head = [key.alias(spec["key"]), F.regexp_extract(key, r"^([A-Z]+)", 1).alias("financing_number_prefix"), F.lit(spec["lender"]).alias("lender"),
            _c("Project ID").alias("project_id"), _c("Project Name").alias("project_name_source"), _c("Region").alias("region_name_source"),
            _c("Country / Economy Code").alias("country_code"), _c("Country / Economy").alias("country_name"), _c("Borrower").alias("borrower_name")]
    if source == "ibrd":
        terms = [_c("Guarantor Country / Economy Code").alias("guarantor_country_code"), _c("Guarantor").alias("guarantor_name"),
                 _c("Loan Type").alias("loan_type"), _c("Interest Rate").cast(DEC_RATE).alias("interest_rate")]
        raw_terms = [_c("Interest Rate").alias("_raw_rate")]
    else:
        terms = [_c("Service Charge Rate").cast(DEC_RATE).alias("service_charge_rate"),
                 (_c("First Repayment Date").isNotNull() & _c("Last Repayment Date").isNotNull()).alias("has_repayment_schedule")]
        raw_terms = [_c("Service Charge Rate").alias("_raw_rate")]
    amounts = [_c(src).cast(DEC_AMOUNT).alias(out) for src, out in spec["amounts"].items()]
    dates: list[Column] = []
    for src, out in DATES.items():
        dates.append(_date(src).alias(out))
        if src in PLACEHOLDER_DATES:
            dates.append(F.coalesce(_c(src) == PLACEHOLDER_DATE, F.lit(False)).alias(PLACEHOLDER_DATES[src]))
    return bronze.select(
        *head, *terms, _c(spec["status_src"]).alias("financing_status"), _c("Currency of Commitment").alias("currency_of_commitment"),
        *amounts, *dates,
        _c("Board approval - Fiscal year").cast("int").alias("board_approval_fiscal_year"),
        _c("Board approval - Calendar year").cast("int").alias("board_approval_calendar_year"),
        F.to_date(_c("End of Period"), "MM/dd/yyyy").alias("source_end_of_period"), F.lit(SNAPSHOT_SEMANTICS).alias("financing_snapshot_semantics"),
        F.lit(spec["dataset"]).alias("source_dataset"), row_hash_expr(cols).alias("source_row_sha256"),
        F.lit(spec["bronze"]).alias("bronze_table"), F.col("source_file").alias("bronze_source_file"),
        F.col("cohort_id").alias("bronze_cohort_id"), F.col("ingested_at").alias("bronze_ingested_at"),
        F.lit(prov["bronze_input_file_sha256"]).alias("bronze_input_file_sha256"),
        F.lit(prov["source_population_sha256"]).alias("source_population_sha256"),
        F.to_timestamp(F.lit(prov["source_retrieved_at"])).alias("source_retrieved_at"),
        F.lit(run_id).alias("silver_run_id"), F.current_timestamp().alias("silver_processed_at"), F.lit(TRANSFORM_VERSION).alias("transform_version"),
        # validation-only
        *[_c(src).alias(f"_raw_{out}") for src, out in spec["amounts"].items()], *[_c(src).alias(f"_raw_{out}") for src, out in DATES.items()],
        *raw_terms, _c("End of Period").alias("_raw_eop"), _c("Board approval - Fiscal year").alias("_raw_fy"),
        _c("Board approval - Calendar year").alias("_raw_cy"),
    )


def drop_validation(df: DataFrame) -> DataFrame:
    return df.drop(*[c for c in df.columns if c.startswith("_")])
