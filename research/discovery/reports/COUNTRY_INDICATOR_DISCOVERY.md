# Country-Level Indicator Discovery (Pass 2)

Run date **2026-10-22**. Scope: country-level economic and governance indicators for the approved 2,000-project PoC
cohort (D-26). **Discovery only:**
- No indicator data was downloaded or persisted.
- Existing Phase 1 files, the cohort, the Bronze inputs and the Databricks tables were not modified.

All work lives in `discovery/country_indicators/`:

| File | Contents |
|---|---|
| [`probe_indicator_metadata.py`](../country_indicators/probe_indicator_metadata.py) | 47 logged requests ([`evidence/access_log.jsonl`](../country_indicators/evidence/access_log.jsonl)). Covers indicator, source, country and archive-version metadata; row-count probes (`per_page=1`); one `mrnev=1` probe per indicator; header-only checks of two official files. From the `mrnev` probe only the *latest year with a value* per economy is kept; all values were discarded |
| [`analyze_local_coverage.py`](../country_indicators/analyze_local_coverage.py) | Offline analysis of the Phase 1 indicator files, the cohort manifest and the cohort Bronze inputs. Output: [`analysis/local_coverage.json`](../country_indicators/analysis/local_coverage.json) |
| [`metadata/`](../country_indicators/metadata/) | Official metadata responses: indicators, sources, the 296-entry country list, the WGI series list, WDI-archive concepts and versions, file headers |

## Review corrections (2026-10-22, after architect review)

**1. The eight projects outside "1,853 matched + 139 regional".**
- **Reconciliation:** the cohort has 1,861 single-country projects. 1,853 match an API economy; the other **8 are all
  Sint Maarten (`SX`)**: P167347, P167368, P167532, P172339, P172582, P172611, P172753, P179067, approved FY2019–FY2025.
  1,853 + 8 + 139 = 2,000.
- **Cause:** the original count used the Phase 1 crosswalk (`wbdisc/countries.py`), which was built for WGI and maps
  `SX` to none.
- **WDI coverage:** WDI does cover Sint Maarten as `SXM`. GDP growth and GDP per capita run to 2025; **inflation only to
  2017**.
- **Under D-31:** these 8 projects match WDI and are reported as unmatched for WGI and CPIA. The income-classification
  match is checked at download.

**2. WGI coverage.**
- **The error:** earlier versions of this report said "133 of 133 cohort economies". The denominator wrongly left out
  Sint Maarten.
- **Corrected figure:** **133 of 134** single-country cohort economies (1,853 of 1,861 projects).
- **Sint Maarten:** the WGI API has no `SX`/`SXM` rows.
- **Netherlands Antilles:** WGI does publish "Netherlands Antilles" under an empty country ID, with values 2004–2017.
  That entity was dissolved in 2010, when Sint Maarten became a separate country. Using its values for `SX` is not
  defensible, so they stay unmatched and are reported, not mapped.
- **CPIA:** likewise covers 89 of 134 (no Sint Maarten).
- **Elsewhere in the report:** corrected figures are marked *(corrected 2026-10-22)*.

## 1. What we already have (Phase 1, `discovery/samples/indicators/`)

The files were downloaded 2026-10-21 at 01:20 UTC by `scripts/s05_finance_ieg_indicators.py`, from
`api.worldbank.org/v2/country/all/indicator/<code>`. Each holds every country and year in one page; the checksums are in
`samples/MANIFEST.jsonl`.

| Series (6 each) | API source | Source `lastupdated` (at download → today) | Records per file | Values | Years with values | Economies with values | Cohort economies with values |
|---|---|---|---|---|---|---|---|
| WGI estimates `GOV_WGI_{CC,GE,PV,RL,RQ,VA}.EST` | 3 (Worldwide Governance Indicators) | 2026-09-25 → **2026-09-25** | 5,832 | 5,553–5,691 | 1996–2025 (no values in 1997, 1999, 2001: WGI was biennial then) | 205–207 | **133 of 134** (no Sint Maarten) *(corrected 2026-10-22)* |
| CPIA `IQ.CPA.{FINQ,IRAI,PADM,PROP,PUBS,TRAN}.XQ` | 2 (World Development Indicators) | 2026-10-08 → **2026-10-08** | 17,556 | 2,477 | 2005–2025 | 91 | 89 of 134 *(corrected 2026-10-22)* |

Missing values among cohort economies for 2005–2025:

| | WGI | CPIA |
|---|---|---|
| Missing country-years | 0.2–0.4% | **43.8%** |

- **CPIA gaps are structural.** CPIA is published for IDA-eligible countries only. 427 single-country cohort projects
  are in economies with no CPIA value in any year: 405 IBRD and 22 "Not classified".
- **WGI rows without a country ID:** 243 rows (9 territories × 27 years, e.g. Anguilla, Jersey, Taiwan, China) have
  an empty country ID. **Corrected 2026-10-22:** one of them, "Netherlands Antilles", is the former entity that included
  Sint Maarten (values 2004–2017). It is not a valid source for `SX`; see "Review corrections".

**Reuse verdict: reuse both as-is.** Each source's `lastupdated` today equals the value at download, so the local files
are the current release. No re-download is needed.
- **Government effectiveness (candidate 4)** is already local as `GOV_WGI_GE.EST`.
- **CPIA** is also published as a dedicated API source 31 (last updated 2026-08-03). The copy we hold comes from WDI
  (source 2).

## 2. Candidate indicators: verified against official metadata

For every series, the API's `unit` field is empty; the unit is stated in the official series name. Frequency is
annual: one row per economy and year.

| # | Code | Official name | Source (API id) | Unit | Rows, all years / 2000–2025 | Last updated | Cohort economies with any value | Latest year (cohort economies) |
|---|---|---|---|---|---|---|---|---|
| 1 | `NY.GDP.MKTP.KD.ZG` | GDP growth (annual %) | WDI (2) | % change, constant 2015 US$ series | 17,556 / 6,916 | 2026-10-08 | **134 / 134** *(corrected)* | 2025 for 130 (including Sint Maarten). Older: South Sudan 2015, Yemen 2018, Afghanistan and Lebanon 2024 (**45 projects**) |
| 2 | `FP.CPI.TOTL.ZG` | Inflation, consumer prices (annual %) | WDI (2); originates from IMF International Financial Statistics | % change in CPI | 17,556 / 6,916 | 2026-10-08 | **132 / 134** *(corrected)* (none for Somalia or Marshall Islands) | 2025 for 119. Older: DR Congo 2016, Yemen 2014, Tuvalu 2011, **Sint Maarten 2017**, Myanmar and Eswatini 2019, Sudan, Micronesia and Zimbabwe 2022, others 2024 (**167 projects** affected, including the 8 Sint Maarten projects) |
| 3 | `NY.GDP.PCAP.CD` | GDP per capita (current US$) | WDI (2) | current US$ | 17,556 / 6,916 | 2026-10-08 | **134 / 134** *(corrected)* | Same pattern as GDP growth (Sint Maarten to 2025) |
| 4 | `GOV_WGI_GE.EST` (**local already**) | Government Effectiveness: governance estimate (approx. −2.5 to +2.5) | WGI (3) | standardised estimate | 5,832 / 5,400 | 2026-09-25 | **133 / 134** *(corrected)*: no Sint Maarten | 2025 for all 133 covered |
| 4b | `GOV_WGI_GE.SC`, `.SC_LB`, `.SC_UB`, `.SE`, `.SR` | Score (0–100), 90% confidence-interval bounds, standard error, number of sources | WGI (3) | as named | 5,832 each | 2026-09-25 | 133 / 134 *(corrected)* | — |
| — | `GOV_WGI_GE.PER.RNK`, `.STD.ERR`, `.NO.SRC` | Old-style codes | — | — | **Invalid** (API error 120) | — | — | Not published under these codes; use `.SC`, `.SE`, `.SR` |
| 5 | Income classification | World Bank income groups (L / LM / UM / H) by fiscal year | DDH file `OGHIST_2026_10_08.xlsx` (115,957 bytes; header check only) | category | all economies × fiscal years since the late 1980s (exact range unverified until download) | 2026-10-08 (file name) | not yet measured | — |
| (opt.) | `NY.GNP.PCAP.CD` | GNI per capita, Atlas method (current US$) | WDI (2) | current US$ | 17,556 / 6,916 | 2026-10-08 | 134 / 134 *(corrected)* | The basis of the income classification. Not needed if OGHIST is used |

Row totals include empty values. Missing values within years (as opposed to the latest year available) can only be
measured after download; this is validation check V4 in §5.

**Government effectiveness: the appropriate series**
- **Choice:** `GOV_WGI_GE.EST`, from API source 3. It is the WGI's primary estimate. `GE.SC` is the same construct on a
  0–100 scale, and `.SE` and `.SR` give its uncertainty and number of sources.
- **History:** the WGI covers 1996–2025, annually from 2002.
- **Revisions:** the official page says *"Historical estimates have been recalculated back to 1996 to ensure full
  consistency over time."* So every past value in the API is the 2026-release value, not the value as published at
  the time. The API has no WGI vintages; WDI vintages exist (source 57), but they don't cover WGI.

**Income classification: historically appropriate values**
- **The country endpoint can't be used.** API `/v2/country` returns only *today's* income level, e.g. 384 cohort
  projects are in economies currently "Low income". Assigning it to past years would be wrong.
- **The official rule** (Data Help Desk article 378834):
  - Countries are classified on **July 1** each year, using Atlas GNI per capita for the **previous calendar year**.
  - The groupings "remain fixed for the entire fiscal year".
  - Later revisions to GNI don't change a grouping already assigned.
- **The historical file:** `https://ddh-openapi.worldbank.org/resources/DR0095334/download` serves
  `OGHIST_2026_10_08.xlsx` (HTTP 200, 115,957 bytes, `application/octet-stream`). It gives the classification in force
  for each economy and fiscal year, plus the threshold history.
- **The current file:** `CLASS_2026_10_08.xlsx` (75,486 bytes) is at `.../DR0095333/download`.
- **How to apply it:** map each event date to its World Bank fiscal year (July–June), then take that fiscal year's
  classification. This has no leakage by construction, because the value is fixed when it is assigned.
- **Not yet checked:** the file's exact sheet layout. It's checked at ingestion (V6).

**As-published values for WDI series (leakage control)**
- **What exists:** API source 57, *WDI Database Archives*, has dimensions Country, Series, Time and Version.
- **Vintages:** it lists **142 versions** (1989 Apr → 2026 Jul), with 2–10 releases a year since 2010.
- **Probe results:** count probes for all four WDI candidates at versions 202607 and 201007 returned 19,564 rows each.
  That covers every economy and every year 1960–2025, empty values included.
- **Not yet measured:** whether each old vintage actually holds values for these series.

## 3. Relevance to the 2,000 cohort projects

| Question | Finding |
|---|---|
| Countries represented | 155 country codes: **1,861 single-country projects** and **139 regional or multi-country projects** (21 regional codes, e.g. `3E` Eastern and Southern Africa 40, `3W` Western and Central Africa 38, `1W` World 10) |
| Reliable code matching | 1,853 projects (133 economies) match an API economy, using the curated aliases `ZR→CD` (26), `GZ→PS` (21), `TP→TL` (4), `RY→YE` (1). **Sint Maarten `SX` (8 projects)** is the remainder: 1,853 + 8 + 139 = 2,000. The Phase 1 crosswalk (`countries.py`) maps `SX` to none because it was built for WGI. Under D-31 (source-specific crosswalks) `SX` matches WDI (`SXM`); it stays unmatched for WGI and CPIA. Kosovo `XK` matches directly *(corrected 2026-10-22)* |
| Regional projects | Their contract rows carry **no** single-country borrower code (0 of 17,893 rows), and project records list no participating countries. **No defensible country mapping exists, so country features stay null with an explicit `regional` flag.** A participation list could later come from project documents; that is out of scope |
| Years that matter | Approval FY 2007–2027 (5th percentile 2013, median 2020). Contracts signed 2019–2026. Notices published 2008–2026 (97% from 2018 on). With a 1–2 year publication lag and up to 3-year trend windows, the needed indicator years are **2002–2025**; we recommend **2000–2025** |
| Historical values available | WDI and WGI: yes for the window. CPIA: 2005–2025 for IDA-eligible countries only |
| Missing data | WGI ≤ 0.4% for covered economies, plus 8 Sint Maarten projects with no WGI. CPIA 43.8%. Latest-year gaps for GDP growth affect 45 projects and inflation 167 (§2). Regional projects: 139 with no country value. Within-window gaps for new series are measured after download |
| Contract rows | 4,773 contract rows of single-country projects have a blank borrower country code (Phase 1 issue). Take the country from the project record in Silver, never by guessing |

**Publication timing and leakage**

| Source | When year Y becomes available | Revised later? | Leakage-safe use |
|---|---|---|---|
| WDI (GDP growth, inflation, GDP per capita) | Several releases a year (archive calendar). First estimates of Y are expected during Y+1, with revisions after that. Not yet measured: R5/V7 would measure it from the vintages | Yes | **Preferred:** values from the latest WDI-archive version published before the prediction date. **Fallback:** current vintage, restricted to year ≤ prediction year − 2, with revision risk documented |
| WGI | Late September of Y+1 (2026 release: file dated 2026-09-20, API 2026-09-25, covering 2025) | Yes: the whole history is recalculated each release | Year ≤ prediction year − 1 only if the prediction date is on or after 1 October, else ≤ prediction year − 2. Residual revision leakage can't be removed through the API |
| CPIA | Inferred: mid Y+1. The WDI copy, updated 2026-10-08, already holds 2025 values; the dedicated CPIA source 31 was updated 2026-08-03 | Not verified | Same date rule as WGI. Revision behaviour to be checked |
| Income classification | Assigned 1 July for the fiscal year | No (official) | Use the classification of the event date's fiscal year. No leakage |

Today's API `incomeLevel` or `lendingType` must never be used as a historical feature.

## 4. ML usefulness

We make no claim that any indicator improves accuracy. Every effect below is a hypothesis to be tested with ablation
on the cohort.

| Indicator | What it measures | Procurement predictions it could support | Available before the prediction date? | Missing-data limits | Equivalent already held | Recommendation |
|---|---|---|---|---|---|---|
| GDP growth | Real economic momentum | Tender duration, bidder participation and single-bidder risk, contract delay or cancellation, project outcome | Yes, with the lag rule or archive vintages | 4 cohort economies with stale latest years (45 projects); regional projects null | None (Phase 1 has no macro series) | **Include** |
| Inflation (CPI) | Price instability | Price escalation, re-bidding or cancellation, bid-price anomalies, contract-value deviations | Yes, with the lag rule or archive | **Weakest coverage:** none for Somalia or Marshall Islands; stale for 12 more economies (159 projects); hyperinflation outliers need robust scaling in Silver | None | **Include**, with a missing-value flag |
| GDP per capita | Income level and price level (current US$, exchange-rate sensitive) | Normalising contract amounts by economy size (price anomaly), market depth | Yes, with the lag rule or archive | Same as GDP growth | Partly overlaps income classification | **Include** (cheap; continuous complement to the income group) |
| Government effectiveness (`GE.EST`) | Perceived quality of public services, civil service and policy implementation | Implementation delays, procurement-process duration, project outcome | Yes, with the WGI lag rule (revision caveat) | ≤ 0.4% in the window; St Maarten and regional projects null | **Already local**; overlaps CPIA public sector management (`IQ.CPA.PUBS.XQ`) for IDA countries | **Reuse** the local copy. Optionally add `GE.SE` for uncertainty |
| Income classification (historical) | Official income group in force for the fiscal year | Segmenting models and KPIs; context for lending terms | Yes (fixed 1 July, no revisions) | Measured at ingestion; regional projects null | Partly: v3 `projectfinancialtype` (IDA/IBRD) is a project attribute; API `incomeLevel` is today's value only | **Include** |
| GNI per capita, Atlas | Income measure behind the classification | Same as GDP per capita | Yes | Same as GDP | Redundant with OGHIST and GDP per capita | **Do not include** (optional) |
| CPIA (6 local) | Bank staff assessment of policies and institutions | Same as GE, for IDA countries | Yes (mid Y+1) | 43.8% structurally missing, not at random | Local | **Reuse**, only with an `is_cpia_covered` flag; never impute for IBRD countries |

## 5. Recommended ingestion plan (awaiting approval)

Country-level reference data is kept **separate** from the project-level Bronze tables. We suggest a new schema,
`workspace.wbg_ref_bronze`; the nine existing `wbg_bronze` tables are unchanged.

- **Countries:** load all economies and aggregates unfiltered, as country-year reference data (pending D-28). Cohort
  filtering happens in Silver.
- **Format:** raw files are kept as received, JSON or XLSX; nothing is converted.

| Step | Dataset | Request | Year range | API requests | Records (est.) | Raw size (est.) | Raw format | New Bronze table |
|---|---|---|---|---|---|---|---|---|
| R1 | WDI current vintage: GDP growth, inflation, GDP per capita | `country/all/indicator/<code>?format=json&date=2000:2025&per_page=10000` | 2000–2025 | 3 (+3 metadata snapshots) | 20,748 (6,916 × 3, including empty values) | ≈ 5.8 MB JSON (about 0.25 MB gzip) | JSONL, one API record per line | `ref_wdi_indicators_current` |
| R2 | Historical income classification | `ddh-openapi.worldbank.org/resources/DR0095334/download` | all fiscal years in the file | 1 | ≈ 220 economies × ≈ 40 fiscal years (estimate; sheet layout to be confirmed) | 116 KB | XLSX, kept as is | `ref_income_classification_history` (sheet rows as strings, plus sheet and row-number columns) |
| R3 | Local WGI (6 `.EST`) and CPIA (6) | none: reuse the Phase 1 files | 1996–2025 / 2005–2025 | 0 | 34,992 + 105,336 | 0.33 + 0.43 MB gzip (existing) | JSONL.gz (existing) | `ref_wgi_estimates`, `ref_cpia_ratings` |
| R4 (optional) | WGI GE uncertainty `GOV_WGI_GE.SE` | same pattern as R1, `source=3` | 1996–2025 | 1 | 5,832 | ≈ 1.6 MB | JSONL | into `ref_wgi_estimates` |
| R5 (conditional, D-30) | WDI archive vintages for the 3 R1 series: the last version published before 1 October of each year, 2008–2026 | `sources/57/country/all/series/<code>/version/<v>/time/all` | 1960–2025 per vintage | about 57 (19 vintages × 3 series) | about 1.1 M rows (57 × 19,564); fewer if a time filter works, to be verified | about 300 MB JSON at the observed ≈ 280 bytes per row; about 115 MB if limited to 2000–2025 | JSONL | `ref_wdi_indicators_vintage` |

**Recommended first approval: R1, R2 and R3.** That's about 6 MB, 4–7 requests, and makes all five candidates usable.

Ingestion rules:
- R1, R2 and R4 go through a new extraction script with its own access log and manifest (source URL, `lastupdated`,
  record counts, bytes, SHA-256, extraction timestamp).
- No Phase 1 file is touched, and R3 reads the existing Phase 1 files without re-downloading them.

**Validation checks**

| # | Check |
|---|---|
| V1 | Records received equal the API page-header `total` (6,916 per R1 series); `lastupdated` is recorded and equals the source's `lastupdated` |
| V2 | Unique (indicator, country, year); years within the requested range; the only gaps are the documented WGI 1997/1999/2001 |
| V3 | Every country ID resolves against the saved API country list (economies vs. aggregates). The 9 blank-ID WGI territories are reported, not dropped |
| V4 | Missing values per indicator by year, for all economies and for the 133 cohort economies, compared with the latest-year probe in §2 |
| V5 | Cohort coverage report: projects with all, partial or no country features; regional projects (139) and St Maarten (8) listed explicitly |
| V6 | OGHIST: sheet names and header rows recorded. One classification per economy and fiscal year; values within the documented group codes; fiscal years contiguous; cohort economies covered for FY2005–FY2027 |
| V7 | R5 only: for each vintage, report non-empty values per series. Cross-check the latest vintage against R1 |
| V8 | SHA-256 of each raw file recorded; re-running the probe shows an unchanged `lastupdated` before upload |

## 6. Decisions needed

| ID | Decision | Recommendation |
|---|---|---|
| D-28 | Load country indicators unfiltered as reference data | **Approved 2026-10-22** |
| D-29 | Mapping rule for regional or multi-country projects (139) | **Approved 2026-10-22**: null country features with a `regional` flag, until a reliable participating-country mapping is available |
| D-30 | Leakage policy for revisable indicators | **Approved 2026-10-22**: a two-year data lag as the initial PoC policy, for every country-level indicator. Remaining revision risks are documented, and source vintage metadata is retained. R5 (archive vintages) is not approved |
| D-31 | Per-source country crosswalk (extends D-07) | **Approved 2026-10-22**: source-specific crosswalks with explicit reporting of unmatched countries |

Approved 2026-10-22 together with R1–R3. Recorded in `docs/decisions/D-28_TO_D-31_COUNTRY_REFERENCE_DATA.md`.

## 7. Official sources consulted

| Source | URL |
|---|---|
| API documentation | https://datahelpdesk.worldbank.org/knowledgebase/articles/889392 |
| API call structure (parameters `date`, `per_page`, `mrv`, `mrnev`, `gapfill`, `source`) | https://datahelpdesk.worldbank.org/knowledgebase/articles/898581 |
| API base and sources list | https://api.worldbank.org/v2/ and https://api.worldbank.org/v2/sources (sources 2, 3, 31, 57 confirmed) |
| Indicator metadata | `https://api.worldbank.org/v2/indicator/<code>?format=json` (saved under `country_indicators/metadata/`) |
| Income classification | https://datahelpdesk.worldbank.org/knowledgebase/articles/906519 and https://datahelpdesk.worldbank.org/knowledgebase/articles/378834 |
| Classification files | https://ddh-openapi.worldbank.org/resources/DR0095334/download (historical) and https://ddh-openapi.worldbank.org/resources/DR0095333/download (current) |
| Worldwide Governance Indicators | https://www.worldbank.org/en/publication/worldwide-governance-indicators (2026 update, 1996–2025; dataset file `wgidataset_with_sourcedata-2026.xlsx`, Last-Modified 2026-09-20) |
| World Bank Open Data | https://data.worldbank.org/ |

---

## Acquisition update (2026-10-22, after approval)

R1–R3 and D-28 to D-31 were approved and carried out:
- **Downloaded files, validation results, and revision and lag notes:**
  [`country_indicators/RAW_DATA_INVENTORY.md`](../country_indicators/RAW_DATA_INVENTORY.md).
- **Proposed reference Bronze schema and upload order:**
  [`databricks_upload/REF_BRONZE_INGESTION_GUIDE.md`](../../databricks_upload/REF_BRONZE_INGESTION_GUIDE.md).
- **Decision record:**
  [`docs/decisions/D-28_TO_D-31_COUNTRY_REFERENCE_DATA.md`](../../docs/decisions/D-28_TO_D-31_COUNTRY_REFERENCE_DATA.md).

Measured after download:
- **WDI latest-year coverage:** identical to the probe in §2 (0 differences).
- **OGHIST:** covers all 134 single-country cohort economies, FY1989–FY2027.
