# D-28 to D-31 and R1–R3: Country-Level Reference Data

| | |
|---|---|
| Status | **Approved** 2026-10-22 (architect review of `COUNTRY_INDICATOR_DISCOVERY.md`) |
| Evidence | [discovery report](../../discovery/reports/COUNTRY_INDICATOR_DISCOVERY.md), [raw inventory](../../discovery/country_indicators/RAW_DATA_INVENTORY.md), [reference Bronze guide](../../databricks_upload/REF_BRONZE_INGESTION_GUIDE.md) |
| Unchanged | The 2,000-project cohort (D-26), Phase 1 raw data, the procurement Bronze inputs, and the nine Databricks Bronze tables |

## Approved acquisition

| ID | Scope | Status |
|---|---|---|
| R1 | WDI `NY.GDP.MKTP.KD.ZG`, `FP.CPI.TOTL.ZG`, `NY.GDP.PCAP.CD`, all countries, 2000–2025, official Indicators API | Downloaded 2026-10-22; 3 × 6,916 records; source vintage 2026-10-08 |
| R2 | Official historical income classification | Downloaded 2026-10-22: `OGHIST_2026_10_08.xlsx`, unchanged |
| R3 | Reuse the Phase 1 WGI (6) and CPIA (6) files unchanged | Verified unchanged against their Phase 1 checksums |
| R4, R5 | WGI uncertainty series; WDI archive vintages | **Not approved** |

## Decisions

**D-28: reference data is not filtered by the cohort.**
- Country-level datasets are kept complete: all economies and aggregates.
- They live in their own schema, `workspace.wbg_ref_bronze`, and staging folder, `databricks_upload/ref_bronze_inputs/`.
- They never mix with the cohort-bounded procurement Bronze inputs.

**D-29: regional projects get no country indicators.**
- The 139 regional or multi-country cohort projects receive no country-level indicator values. They are flagged as
  regional.
- This holds until a reliable participating-country mapping is available.
- No averaging, lead-country or borrower-row inference is used. Their contract rows carry no single-country borrower
  code.

**D-30: two-year data lag.**
- **Rule:** for an event in year Y, use indicator data for year ≤ Y−2. This is the initial PoC feature-engineering
  policy.
- **Remaining risks, documented:**
  - WDI and WGI values are the current, revised vintage, not as first published: a look-ahead bias of unknown size.
  - WGI recalculates its full history in every release.
  - CPIA's revision behaviour is not verified.
- **Vintage metadata retained:** API `lastupdated`, retrieval timestamps and checksums.
- **Income classification:** fixed when published, so the classification for the event's fiscal year is leakage-free.
  Also applying the two-year lag to it is a Silver design choice.

**D-31: source-specific country crosswalks.**
- Each source is matched against its own country list. Only the curated aliases (`ZR→CD`, `GZ→PS`, `TP→TL`, `RY→YE`)
  are applied, with no fuzzy matching.
- Unmatched or missing countries are reported explicitly: `validation/cohort_country_match_by_source.csv`.
- **Sint Maarten (`SX`):** matched for WDI and OGHIST, unmatched for WGI and CPIA. WGI's "Netherlands Antilles" rows are
  not mapped.
- **Phase 1 code:** `wbdisc/countries.py`, the WGI-oriented crosswalk, is unchanged. The source-specific rule is
  implemented in Silver.
