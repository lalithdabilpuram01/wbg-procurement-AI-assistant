"""The upload folders of the ETL job: one folder per dataset, and the Bronze table(s) each one feeds.

Procurement datasets (World Bank Finances One CSVs, Projects / procnotices API JSON Lines) are cut to the 2,000-project
PoC cohort on the way in (row selection only, bytes unchanged; D-26). Reference datasets (WDI, WGI, CPIA, income
groups) are country-level and never cut (D-28).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Dataset:
    folder: str                 # upload folder, also the staged folder
    tables: tuple[str, ...]     # Bronze tables fed by this folder (schema.table)
    kind: str                   # csv | jsonl | series | wdi_observations | wdi_indicator | json_response | xlsx
    key: str | None = None      # cohort selection key; None = reference data, not cut
    coverage: str = "complete"  # complete: every cohort project can appear; partial: a Phase 1 cluster only
    series: bool = False        # True: every file in the folder is part of the table; False: the newest file wins
    description: str = ""

    @property
    def cohort_bounded(self) -> bool:
        return self.key is not None


PROCUREMENT = (
    Dataset("projects_v3", ("wbg_bronze.bronze_projects_v3",), "jsonl", "id",
            description="search.worldbank.org/api/v3/projects population, 39 selected fields"),
    Dataset("projects_v3_allfields", ("wbg_bronze.bronze_projects_v3_allfields",), "jsonl", "id", "partial",
            description="v3 projects fl=* (ISR/ICR ratings, impagency, indicators, loan_transactions)"),
    Dataset("contract_awards_ds00005", ("wbg_bronze.bronze_contract_awards_ds00005",), "csv", "Project ID",
            description="Finances One DS00005 Contract Awards in Investment Project Financing (FY2020+), bulk CSV"),
    Dataset("contract_awards_legacy_ds01004", ("wbg_bronze.bronze_contract_awards_legacy_ds01004",), "csv", "Project ID",
            description="Finances One DS01004 Major Contract Awards (prior review, FY2001-FY2016, frozen), bulk CSV"),
    Dataset("procurement_notices_ds00979", ("wbg_bronze.bronze_procurement_notices_ds00979",), "csv", "Project ID",
            description="Finances One DS00979 Procurement Notices (metadata only: no notice text or bid reference), bulk CSV"),
    Dataset("procurement_notices_api", ("wbg_bronze.bronze_procurement_notices_api",), "jsonl", "project_id", "partial",
            description="search.worldbank.org/api/v2/procnotices with notice_text and bid_reference_no (no contact fields)"),
    Dataset("ieg_ratings_ds00053", ("wbg_bronze.bronze_ieg_ratings_ds00053",), "csv", "Project ID",
            description="Finances One DS00053 IEG Project Performance Ratings (latest evaluation per project), bulk CSV"),
    Dataset("ibrd_loans_ds00047", ("wbg_bronze.bronze_ibrd_loans_ds00047",), "csv", "Project ID",
            description="Finances One DS00047 IBRD Statement of Loans and Guarantees, latest snapshot, bulk CSV"),
    Dataset("ida_credits_ds00001", ("wbg_bronze.bronze_ida_credits_ds00001",), "csv", "Project ID",
            description="Finances One DS00001 IDA Statement of Credits, Grants and Guarantees, latest snapshot, bulk CSV"),
)

REFERENCE = (
    Dataset("wdi_indicator_observations", ("wbg_ref_bronze.wdi_indicator_observations", "wbg_ref_bronze.wdi_api_responses"),
            "wdi_observations", series=True, description="WDI /v2/country/all/indicator/<code> responses, 2000-2025"),
    Dataset("wdi_indicator_metadata", ("wbg_ref_bronze.wdi_indicator_metadata",), "wdi_indicator", series=True,
            description="WDI /v2/indicator/<code> responses"),
    Dataset("wdi_source_metadata", ("wbg_ref_bronze.wdi_source_metadata",), "json_response", description="WDI /v2/sources/2"),
    Dataset("wdi_country_metadata", ("wbg_ref_bronze.wdi_country_metadata",), "json_response", description="WDI /v2/country"),
    Dataset("income_classification_history", ("wbg_ref_bronze.income_classification_history",), "xlsx",
            description="World Bank historical income classification workbook (OGHIST)"),
    Dataset("wgi_estimates", ("wbg_ref_bronze.wgi_estimates",), "series", series=True,
            description="Worldwide Governance Indicators estimates (GOV_WGI_*.EST), JSON Lines"),
    Dataset("cpia_ratings", ("wbg_ref_bronze.cpia_ratings",), "series", series=True,
            description="CPIA ratings (IQ.CPA.*.XQ), JSON Lines"),
)

DATASETS = PROCUREMENT + REFERENCE
BRONZE_TABLES = tuple(t for d in DATASETS for t in d.tables)
COHORT_ID = "wbg_poc_2000"
REFERENCE_COHORT_ID = "reference_unfiltered"

# Default API endpoint per reference series, recorded when an uploaded file is not one of the Phase 1 downloads.
SERIES_URLS = {
    "wdi_indicator_observations": "https://api.worldbank.org/v2/country/all/indicator/{code}?format=json&date=2000:2025&per_page=20000",
    "wdi_indicator_metadata": "https://api.worldbank.org/v2/indicator/{code}?format=json",
    "wdi_source_metadata": "https://api.worldbank.org/v2/sources/2?format=json",
    "wdi_country_metadata": "https://api.worldbank.org/v2/country?format=json&per_page=400",
    "income_classification_history": "https://ddh-openapi.worldbank.org/resources/DR0095334/download",
    "wgi_estimates": "https://api.worldbank.org/v2/country/all/indicator/GOV_WGI_{code}?source=3&format=json&per_page=20000",
    "cpia_ratings": "https://api.worldbank.org/v2/country/all/indicator/{code}?format=json&per_page=20000",
}


def by_folder(folder: str) -> Dataset:
    return next(d for d in DATASETS if d.folder == folder)


def series_code(folder: str, file_name: str) -> str | None:
    """The indicator a reference file holds, from its name (as the Phase 1 downloads were named)."""
    stem = file_name.split(".jsonl")[0].removesuffix(".json")
    if folder == "wdi_indicator_observations":
        return stem.removeprefix("observations_").rsplit("_", 1)[0]
    if folder == "wdi_indicator_metadata":
        return stem.removeprefix("indicator_")
    if folder == "wgi_estimates":
        return stem.removeprefix("GOV_WGI_")
    if folder == "cpia_ratings":
        return stem
    return None
