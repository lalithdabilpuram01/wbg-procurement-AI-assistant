# Country-Level Reference Data: Raw Inventory

Acquired **2026-10-22** under approvals R1, R2, R3 and D-28 to D-31. See
[decision record](../../docs/decisions/D-28_TO_D-31_COUNTRY_REFERENCE_DATA.md) and
[discovery report](../reports/COUNTRY_INDICATOR_DISCOVERY.md).

All data is **country-level reference data**. It is kept apart from the procurement Bronze inputs and is not filtered by
the 2,000-project cohort (D-28). Nothing has been uploaded to Databricks.

**How the raw files are kept**
- Every downloaded response body is stored byte-for-byte as received.
- Each has a sidecar `<file>.meta.json` holding:
  - the request URL and parameters, and the final URL
  - retrieval start and finish times
  - the HTTP status and response headers
  - the size and SHA-256
  - for API responses, the page header (`total`, `lastupdated`, `sourceid`)
- Every request is logged in `evidence/access_log.jsonl`: 56 requests in this folder, of which 47 are the earlier
  metadata probes and 9 are downloads.

**Scripts** (all in this folder):

| Script | What it does |
|---|---|
| `r01_download_reference_data.py` | Downloads; never overwrites existing files |
| `v01_validate_reference_data.py` | Validates; read-only |
| `s01_stage_ref_bronze_inputs.py` | Byte-copies files into `databricks_upload/ref_bronze_inputs/`, verifying SHA-256 |

## 1. Files

### R1: WDI series (`raw/wdi/`), downloaded 2026-10-22 18:21 UTC

The WDI source record says `lastupdated: 2026-10-08`. Every observation file's page header carries the same value: that
is the **source vintage** of these values.

| File | Source URL (API parameters) | Records | Bytes | SHA-256 |
|---|---|---|---|---|
| `observations_NY.GDP.MKTP.KD.ZG_2000-2025.json` | `https://api.worldbank.org/v2/country/all/indicator/NY.GDP.MKTP.KD.ZG?format=json&date=2000:2025&per_page=20000` | 6,916 | 1,516,146 | `07bf08cd83944766f7287d4811097102c1e2eacdd4ceccc7774a12c0b20cfd03` |
| `observations_FP.CPI.TOTL.ZG_2000-2025.json` | `.../indicator/FP.CPI.TOTL.ZG?format=json&date=2000:2025&per_page=20000` | 6,916 | 1,597,780 | `25b55dac49ae80c8c645d0f1d93eda4a675c35945c8944238b6cb4e86fd2368a` |
| `observations_NY.GDP.PCAP.CD_2000-2025.json` | `.../indicator/NY.GDP.PCAP.CD?format=json&date=2000:2025&per_page=20000` | 6,916 | 1,543,130 | `c99f5c6d1493c8770b1aaef0373a77556e05b5d6e6fba6468f3376cc874c10d6` |
| `indicator_NY.GDP.MKTP.KD.ZG.json` | `https://api.worldbank.org/v2/indicator/NY.GDP.MKTP.KD.ZG?format=json` | 1 | 909 | `ed7703d841d4dde2d1cb858f0dd53229fe3e841f72abb17dbc8c950ad20c88ce` |
| `indicator_FP.CPI.TOTL.ZG.json` | `.../indicator/FP.CPI.TOTL.ZG?format=json` | 1 | 632 | `df9371a3b6c01e55a3f28991e62e1b1ebbe0cd2a7da94456be9938c7a2962233` |
| `indicator_NY.GDP.PCAP.CD.json` | `.../indicator/NY.GDP.PCAP.CD?format=json` | 1 | 1,044 | `96520c3a22b1f9da1c7456349a6e70b1643db88d427671faced17cb8b9eaf907` |
| `source_2_wdi.json` | `https://api.worldbank.org/v2/sources/2?format=json` | 1 | 236 | `26d3ed8cf01ffed2995a9997940398bf5a287a1ee595fad75983f877d2805bba` |
| `countries.json` | `https://api.worldbank.org/v2/country?format=json&per_page=400` | 296 | 113,954 | `c9acf3932785191a649cbc04c3056af4cf64a236e5c3b49d49be25ddb0440bce` |

Response format: a JSON array `[page_header, [records...]]`. Each observation record has `indicator{id,value}`,
`country{id,value}`, `countryiso3code`, `date`, `value`, `unit`, `obs_status` and `decimal`.

`countries.json` is used for code resolution only. Its `incomeLevel` and `lendingType` are **today's values** and must
not be used as historical features.

### R2: historical income classification (`raw/income_classification/`)

| File | Source URL | Retrieved (UTC) | Bytes | SHA-256 |
|---|---|---|---|---|
| `OGHIST_2026_10_08.xlsx` (name taken from the server's `Content-Disposition`) | `https://ddh-openapi.worldbank.org/resources/DR0095334/download` | 2026-10-22 18:21:55 | 115,957 | `a4024eb8052150155034559daf053aabbc16336d8290efeb5629e6eeb6515a60` |

The workbook is unchanged and has five sheets:

| Sheet | Rows × columns | Notes |
|---|---|---|
| `Parameters` | 10 × 2 | **Stale:** still shows FY21 / July 2020 thresholds. Do not use |
| `Thresholds` | 47 × 55 | Threshold history |
| `Country Analytical History` | 235 × 254 | The classification. Header rows 5 (fiscal year FY89–FY27) and 6 (GNI data year 1987–2025); 223 economy rows (ISO3 code, name); values `L`, `LM`, `UM`, `H`, `..` = not classified, `LM*` = footnoted Yemen FY89–90, blank. Three note rows, including a `YUGf` row and the official note: *"The historical classifications shown are as published on July 1 of each fiscal year."* |
| `Operational Category Change` | 487 × 11 | Operational income category changes |
| `Country Indebtedness History` | 241 × 28 | Discontinued April 2006 |

### R3: reused Phase 1 files (`discovery/samples/indicators/`, unchanged)

| Files | Records each | Source vintage (`lastupdated`) | Downloaded | Integrity |
|---|---|---|---|---|
| WGI `GOV_WGI_{CC,GE,PV,RL,RQ,VA}.EST.jsonl.gz` (API source 3) | 5,832 | 2026-09-25 | 2026-10-21 01:20 UTC | SHA-256 matches `samples/MANIFEST.jsonl` (6/6) |
| CPIA `IQ.CPA.{FINQ,IRAI,PADM,PROP,PUBS,TRAN}.XQ.jsonl.gz` (API source 2) | 17,556 | 2026-10-08 | 2026-10-21 01:20 UTC | SHA-256 matches (6/6) |

The R3 format differs from R1: JSON Lines, gzip-compressed, one API record per line, without the page header (that was
saved in the Phase 1 manifest notes).

## 2. Validation results

Full output: `validation/reference_validation.json` and `validation/cohort_country_match_by_source.csv`.

| Check | GDP growth | Inflation | GDP per capita |
|---|---|---|---|
| V1 records = API `total`, one page | 6,916 = 6,916 ✓ | ✓ | ✓ |
| V1 `lastupdated` = WDI source record (2026-10-08) | ✓ | ✓ | ✓ |
| V2 indicator code on every record | ✓ | ✓ | ✓ |
| V2 duplicate (country, year) | 0 | 0 | 0 |
| V2 every entity has exactly 2000–2025 | ✓ (266 entities) | ✓ | ✓ |
| V3 entities resolved against `countries.json` | 266/266 (217 economies, 49 aggregates) | ✓ | ✓ |
| V4 missing values, economies, 2000–2025 | 5.10% | **16.75%** | 3.79% |
| V4 economies with no value at all | Gibraltar, North Korea, British Virgin Islands | 24, including **Somalia and Marshall Islands** | Gibraltar, North Korea, British Virgin Islands |
| Economies with a value for 2022 / 2023 / 2024 / 2025 | 209 / 204 / 200 / 186 | 179 / 177 / 176 / 169 | 209 / 204 / 200 / 186 |
| Consistency with the 2026-10-22 coverage probe (latest year per economy) | 0 differences | 0 | 0 |
| `obs_status`, `unit` | empty on all records | empty | empty |

**OGHIST**
- 223 economy codes, 0 duplicates.
- Fiscal years FY1989–FY2027, contiguous (39 columns).
- Six codes are historical entities not in today's API list: `ANT` (Netherlands Antilles), `CSK`, `MYT`, `SUN`,
  `TWN`, `YUG`. They are reported, not mapped.

**R3:** all 12 Phase 1 indicator files are unchanged.

**Cohort coverage** (source-specific crosswalks, D-31)
- **What is checked:** the 1,861 single-country projects across 134 economies. The 139 regional projects are excluded by
  rule (D-29).
- **Matching method:** curated aliases only (`ZR→CD`, `GZ→PS`, `TP→TL`, `RY→YE`), then lookup in each source's own
  country list.

| Source | Matched economies / projects | Unmatched or no values (explicit) |
|---|---|---|
| WDI GDP growth | 134 / 1,861 | — |
| WDI inflation | 132 / 1,830 | No values: Somalia (21 projects), Marshall Islands (10) |
| WDI GDP per capita | 134 / 1,861 | — |
| WGI `GE.EST` | 133 / 1,853 | Unmatched: Sint Maarten (8). WGI's "Netherlands Antilles" rows are not mapped |
| CPIA | 89 / 1,426 | Not covered: 45 economies (435 projects). Structural: CPIA covers IDA countries only |
| Income classification | 134 / 1,861 | — (gaps by year below) |

**Missing values for cohort economies** (country-years)

| Series | Missing 2000–2025 | Missing 2005–2025 | Gaps |
|---|---|---|---|
| GDP growth | 1.78% | 1.46% | South Sudan 19 years; Djibouti 2000–2013; Sint Maarten 2000–2009; Kosovo 2000–2008; Yemen 2019–2025; Afghanistan, Lebanon |
| GDP per capita | 1.26% | 1.03% | Similar pattern |
| Inflation | 7.26% | 5.65% | Somalia and Marshall Islands all years; Tuvalu 25 years; Argentina 19; Sint Maarten 14; Zimbabwe 13; Yemen 2015–2025; DR Congo 2017–2025; Myanmar 2020–2025; and others |

Income-classification gaps for cohort economies, FY2005–FY2027. *Corrected 2026-10-23:* the workbook distinguishes `..`
(published as not classified) from an empty cell (no entry published); the first version of this table merged the two.

| Economy | Fiscal years with no classification |
|---|---|
| South Sudan | FY2005–2012 (country formed 2011) |
| Sint Maarten | FY2005–2011 (country formed 2010) |
| Kosovo | FY2005–2009 |
| Serbia, Montenegro | FY2005–2007 |
| Tuvalu | FY2005–2010 |
| Ethiopia | **FY2026**: an empty cell (no entry), not `..` |

All the other rows above are `..`. Outside the cohort, Venezuela has empty cells for FY2022–FY2026 (classified LM in FY2027).

## 3. Revisions and publication lag

| Source | Vintage held | Revision behaviour | Publication lag observed in this vintage | Implication under D-30 (two-year lag) |
|---|---|---|---|---|
| WDI (R1) | 2026-10-08 | Values are revised in later releases. This file holds **only the 2026-10-08 vintage**, not the values as first published. The archive (API source 57) was not downloaded (R5 not approved) | As of 2026-10-08, the latest year (2025) is present for 186 of 217 economies for GDP and 169 for inflation. Earlier years are progressively more complete | A feature for an event in year Y uses data year ≤ Y−2, which is usually published. **Remaining risk:** the values used are the revised 2026-10-08 values, not what was known at the time. This is a look-ahead bias of unknown size; it can only be measured with archive vintages |
| WGI (R3) | 2026-09-25 | **The whole history is recalculated each release** (official) | 2025 values published September 2026 | Y−2 is published by the event date. Revision risk as for WDI; no WGI vintages exist in the API |
| CPIA (R3) | 2026-10-08 (via WDI) | Not verified | 2025 values present by October 2026 | Y−2 rule applies; revision behaviour to be checked |
| Income classification (R2) | file dated 2026-10-08 | **Not revised**: "as published on July 1 of each fiscal year" | Assigned at the start of each fiscal year | Leakage-free when the fiscal year of the event date is used. Applying the two-year lag as well is conservative, not required: a Silver design choice |

**Vintage metadata retained for every value**
- `lastupdated` from the page header (WDI)
- the Phase 1 manifest `lastupdated` (WGI, CPIA)
- the file date in the OGHIST file name
- retrieval timestamps and SHA-256 in the sidecars and the staging manifest

The Bronze guide carries all of these into ingestion columns.
