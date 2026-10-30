# Schema Discovery & Data Profiling

Run date 2026-10-20. Every statistic below was computed by
[`scripts/p01_profile_structured.py`](../scripts/p01_profile_structured.py) and
[`wbdisc/profiling.py`](../wbdisc/profiling.py), with supporting numbers from `p02`–`p06`. Full column-level outputs are
in [`profiling/*.profile.md|json`](../profiling/) and the observed schemas in [`schemas/*.schema.json`](../schemas/).

**How missingness is reported.** Every column gets four separate percentages, and missing values are never treated as zero:
- **null**: key absent or JSON null
- **empty**: empty string
- **sentinel**: placeholder tokens observed in WB data, such as `NIL`, `N/A`, `Not Rated`, `null` (literal string), `1900-01-01…`
- **populated**: everything else

All CSVs were read with `dtype=str, keep_default_na=False`. Pandas never coerced `"NA"` or `""` to NaN.

**Issue classification.** Each issue is tagged **[SOURCE]** (present in the published data), **[PARSE]** (introduced by
our extraction, and fixed or flagged), or **[API]** (a service behaviour).

---

## 1. Profiled datasets

| Dataset | Rows profiled | Columns | Sample type |
|---|---:|---:|---|
| DS00005 contract awards (bulk) | 295,058 | 21 | **Full population** |
| DS00005 contract awards (API) | 1,000 | 21 (+`_extract`) | 8 FY strata × 5 random blocks of 25 |
| DS01004 legacy contract awards | 146,802 | 17 | Full population |
| Projects v3 | 28,193 | 39 selected | Full population |
| Projects v3 `fl=*` | 276 | 70 | Cluster projects |
| Projects v2 (frozen) | 22,729 / 500 | 52 / 491 | Population / random offsets |
| Procurement notices (cluster) | 30,977 | 19 | **All** notices of 276 projects |
| Procurement notices (global) | 800 | 25 | Stratified by notice type × 2 sort orders |
| DS00979 notice bulk | 422,557 | 15 | Full population |
| WDS v3 type sample / cluster | 1,200 / 22,337 | 53 / 53 | 8 doc types × 2 offsets / all docs of 276 projects |
| WDS v2 type sample / cluster | 1,200 / 18,350 | 77 | Same design (stale index) |
| DS00053 IEG ratings | 12,629 | 21 | Full population |
| DS00047 IBRD / DS00001 IDA latest | 9,538 / 11,486 | 35 / 32 | Full population |
| DS00975 / DS00976 history | 507 / 408 | 34 / 32 | 3 loans each, all months |
| WGI `GOV_WGI_CC.EST` / CPIA `IQ.CPA.TRAN.XQ` | 5,832 / 17,556 | 7 | Full series |

Sampling bias notes:
- The cluster sample was drawn from projects that **have** DS00005 contracts, stratified by region (276 = 300 ÷ 8 regions, floored).
- The global notice sample is limited to the first and last 100,000 records of each type and sort order, because of the API's offset cap.

---

## 2. Contract awards — DS00005 (full population)

### 2.1 Observed schema (bulk CSV)

| Column | Observed type(s) | Populated % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---|
| `As of Date` | date MM/DD/YYYY | 100.0 | 0.0 | 0.0 | 1 | 2026-10-20 |
| `Fiscal Year` | int-as-text | 100.0 | 0.0 | 0.0 | 8 | 2020–2027 |
| `Region` | text | 100.0 | 0.0 | 0.0 | 8 | Eastern and Southern Africa (68,356) |
| `Borrower Country / Economy` | text | 100.0 | 0.0 | 0.0 | 159 | India (35,005); Western and Central Africa (14,400) |
| `Borrower Country / Economy Code` | text | **96.63** | **3.37** | 0.0 | 146 | IN; **3W** (14,400); ET |
| `Project ID` | text `P\d{6}` | 100.0 | 0.0 | 0.0 | 3,155 | 100% valid pattern |
| `Project Name` | text | 100.0 | 0.0 | 0.0 | 3,146 | |
| `Project Global Practice` | text, `;`-delimited | 89.73 | **10.27** | 0.0 | 241 | 48.0% multi-valued |
| `Procurement Category` | text | 100.0 | 0.0 | 0.0 | 4 | Consultant Services 112,594; Goods 92,657; Works 66,281; Non-consulting 23,526 |
| `Procurement Method` | text | 100.0 | 0.0 | 0.0 | 18 | RFQ 106,532; ICS 65,121; RFB 56,799; Direct Selection 35,293 |
| `WB Contract Number` | int-as-text | 100.0 | 0.0 | 0.0 | 282,890 | |
| `Contract Description` | text (21,087 multi-line) | 99.88 | 0.12 | 0.0 | ≥200,000 | |
| `Borrower Contract Reference Number` | free text | 100.0 | 0.0 | 0.0 | ≥200,000 | e.g. `MZ-UEM- FA-474115-GO-RFB` (188 rows) |
| `Contract Signing Date` | date MM/DD/YYYY | 100.0 | 0.0 | 0.0 | 2,658 | 2019-07-01 → 2026-10-13 |
| `Supplier ID` | int-as-text **or 16-hex hash** | 100.0 | 0.0 | 0.0 | 188,653 | |
| `Supplier` | text | 100.0 | 0.0 | 0.0 | 124,823 | `INDIVIDUAL CONSULTANT` (65,121) |
| `Supplier Country / Economy` (+`Code`) | text | 99.77 | 0.01 | **0.22** (`null` literal) | 230 | IN; ET; NG |
| `Supplier Contract Amount (USD)` | decimal-as-text | 100.0 | 0.0 | 0.0 | ≥200,000 | median 33,528.45 |
| `Review type` | text | 99.94 | 0.06 | 0.0 | 2 | Post 273,808; Prior 21,062 |
| `Contract signed - Calendar year` | int-as-text | 100.0 | 0.0 | 0.0 | 8 | |

**API vs bulk schema** (same dataset, verified on the same day):
- Column names differ: `wb_contract_number` vs `WB Contract Number`, `contract_signed___calendar_year` vs `Contract signed - Calendar year`.
- Date formats differ: `19-May-2022` vs `05/19/2022`.
- Types differ: the API returns `fiscal_year` and amounts as JSON numbers (amount as `float` *or* `int`), and returns JSON `null`
  (e.g. `project_global_practice` 14.0% null in the sample) where the CSV has empty strings.

A canonical column mapping is required regardless of which channel is used.

### 2.2 Grain, keys and duplicates

| Check | Result |
|---|---|
| Exact duplicate rows | **0** |
| Duplicates ignoring `As of Date` | 0 |
| Distinct `WB Contract Number` | 282,890 (rows: 295,058) |
| Contracts with >1 row | **8,864** (2 rows: 6,678; 3: 1,679; up to 12) |
| Rows sharing (`WB Contract Number`, `Supplier ID`) | **0**, so this pair is a **unique row key** |
| Multi-row contracts whose amounts are all equal | **79.91%** (consistent with the documented JV equal-split rule) |
| Multi-row contracts with inconsistent project / description / date / method / category | **0** for each attribute |
| (`Project ID`, normalised reference) keys shared by >1 contract | 12,817 keys covering **53,769 contracts (19.0%)**; max 188 contracts per key (framework/multi-lot/repeat RFQ). 785 raw keys merged after normalisation. |

The row grain is therefore **contract × supplier**. 20% of multi-row contracts have unequal amounts. The equal-split rule
explains only 80%; the remainder are presumably multi-supplier awards that are not JVs (**unverified**). The borrower
reference is **not** a contract key; it identifies a procurement *package/activity*.

### 2.3 Supplier identity

| Check | Result | Class |
|---|---|---|
| Supplier IDs that are 16-hex hashes | **65,121 rows (22.07%)**, all with `Supplier` = `INDIVIDUAL CONSULTANT` and method = Individual Consultant Selection | [SOURCE] anonymised individuals; not linkable across contracts |
| Supplier IDs with >1 distinct name | **580** | [SOURCE] |
| Examples | `607068`: "PKS INFRA ENGINEERS PRIVATE LIMITED" / "…PVT. LTD" (same firm). `818187`: "IMMOBILIARE OFFICE SRL" / "SGI STUDIO GALLI INGEGNERIA SRL" (**different firms under one ID**) | [SOURCE] |
| Normalised name + country keys with >1 Supplier ID | **9,567** (dominated by `INDIVIDUAL CONSULTANT`; real firms also affected) | [SOURCE] duplicate supplier registrations, or legitimately distinct entities; **ambiguous** |
| Empty Supplier ID | 1 row | [SOURCE] |

### 2.4 Country representation

| Check | Result |
|---|---|
| Borrower code empty | **9,948 rows**. Includes **real countries**: Côte d'Ivoire (4,972 rows, all empty) and Somalia (2,837), plus regional programmes |
| Regional / multi-country borrower codes | `3W` 14,400; `3E` 7,953; `7C` 1,137; `6O` 1,128; `8S` 368; `99` 257; `4P` 170; `3T` 147; `5M` 20 |
| WB legacy codes ≠ ISO | `ZR` (DR Congo), `GZ` (West Bank & Gaza), `YF` (Serbia), `TP` (Timor-Leste), `RY` (Yemen). Verified against Indicators API ISO2 (`CD`, `PS`, `RS`, `TL`, `YE`) |
| Supplier country code anomalies | `null` literal string 640 rows; `1W` ("World") 1,276 |
| Contract vs project country agreement (v3 project `countrycode`) | 95.28% of projects |

Country joins need a curated crosswalk ([`wbdisc/countries.py`](../wbdisc/countries.py), tested). Fuzzy name matching was
tried and **rejected**: it mapped `ZR` to "Congo, Rep." and "Multi-Regional" to "Bulgaria".

### 2.5 Amounts, dates and categories

| Check | Result |
|---|---|
| Amount unparseable / empty | 0 / 1 |
| Amount = 0 | 9 |
| Amount < US$100 | **1,921** (plausible for small RFQs and JV splits; not validated) |
| Amount quantiles (USD) | p1 173; p10 2,136; p50 33,528; p90 525,642; p99 7,461,757; max 553,067,460 |
| Currency | USD only. Original currency is **not** in DS00005, but is present in award-notice text (§3.3) |
| Signing date unparseable / after snapshot | 0 / 0 |
| `Fiscal Year` inconsistent with signing date (WB FY rule) | **0**; calendar year inconsistent: 0 |
| Procurement method label quality | `Consultant Qualification  Selection` carries a **double space** in the source (16,393 rows) [SOURCE]. No case or whitespace variants otherwise. |
| `Review type` empty | 188 rows |
| Late arrival | Rows per signing month: Mar 2026 2,733 → Jun 2,337 → Aug 1,314 → Sep 925 → Oct 138. Recent months are incomplete. |

---

## 3. Procurement notices

### 3.1 API schema (cluster sample: 30,977 notices, 276 projects)

| Column | Kind | Populated % | Notes |
|---|---|---:|---|
| `id` | text `OP\d{8}` | 100 | **unique** |
| `notice_type` | text | 100 | Contract Award 23,672; REOI 3,956; IFB 3,070; GPN 167; Prequal 112 |
| `noticedate` | date `DD-Mon-YYYY` | 99.96 | 2009-02-23 → 2026-10-19 |
| `submission_date` | ISO-8601 Z | 99.96 | ≠ `noticedate` in 11 rows |
| `submission_deadline_date` / `_time` | ISO-8601 Z / `HH:MM` | 21.66 | IFB 94.6%, REOI 93.9%, Prequal 83.0%, award 0% |
| `project_id` | text | 100 | 0 mismatches with the filter value |
| `bid_reference_no` | free text | 99.25 | Empty: GPN 100%, award 0.26%, IFB 0.03% |
| `bid_description` | text | 99.25 | Multilingual |
| `procurement_group` | code | 97.66 | CS / GO / CW / NC |
| `procurement_method_code` / `_name` | code / text | 99.25 | 17 codes incl. legacy `ICB` |
| `notice_status` | text | 100 | Published 30,845; **Revised 117; Draft 11; Cancelled 4** |
| `notice_lang_name` | text | 99.97 | English 22,056; `Spanish; Castilian` 4,091; French 3,470; Portuguese 1,351 |
| `contact_organization` / `contact_ctry_name` | text | 23.6 / 23.5 | Not present on award notices |
| `notice_text` | HTML | 99.4 | Contains personal data; Microsoft Translator markup (`_msthash`) in some notices |

Quality checks:
- Deadline earlier than notice date: **68** notices. [SOURCE]
- Notices whose deadline equals the notice date: 512 of 4,433 matched tender notices (zero-day bidding periods), consistent with **retrospective entry**. [SOURCE]

### 3.2 DS00979 (bulk, 422,557 rows)

- `ID` is unique and numeric, and maps to the API `OP` id. Its URL column embeds the API id in 100% of rows.
- `Project ID` is valid in 100% of rows.
- `Deadline Date` equals `Publication Date` for 100% of awards and GPNs, 31.6% of IFBs, 30.7% of REOIs, and 52.1% of
  prequalification notices. In this file the field is **not a reliable deadline**. [SOURCE]
- `Procurement Method` is empty for 99.97% of GPNs.
- It mixes 24 method labels, both current (`Request for Bids`) and legacy (`International Competitive Bidding`, 16,012).
- Region labels mix taxonomies (`Mid East,North Africa,Afg,Pak` 19,143 vs `Middle East And North Africa` 343).
- Publication FY: 174 notices in FY2006 rising to 67,287 in FY2025, so coverage before about FY2018 is thin.

### 3.3 Contract Award `notice_text` (semi-structured)

Parser: [`wbdisc/award_notice.py`](../wbdisc/award_notice.py), unit-tested. Applied to 23,672 cluster award notices.

| Field extracted | Coverage |
|---|---:|
| STEP template detected | 98.7% |
| Reference number in text | 99.4% |
| Loan / credit / TF ids | 98.7% |
| Contract duration | 97.0% |
| Award-notification date **or** contract-signature date | 51.4% + 44.0% (template variants) |
| Signed contract price + local currency | 85.1% (+35.2% via the "Currency: Amount" variant) |
| Awarded supplier IDs | 72.8% |
| Bidder count by supplier ID | 75.8% (but **86% zero** for INDV, where IDs are withheld) |
| Notice version > 0 | 15.7% (versions up to 28) |

### 3.4 Publication lag (award event → award notice published)

n = 22,591. Median **127 days**, p75 338, p90 633, p95 999. 5,025 notices were published more than a year after the award. [SOURCE]

---

## 4. Projects (v3, 28,193)

| Check | Result |
|---|---|
| `id` unique and valid | 28,193 / 28,193 |
| Status | Closed 18,319; Dropped 6,907; Active 2,624; Pipeline 343 |
| `boardapprovaldate` | 97.7% populated (27,550; 643 null); 1947-05-09 → 2027-10-26 (future = pipeline) |
| `closingdate` | 56.1% populated |
| `impagency` | **29.8%** populated; 5,871 distinct; multiple agencies are concatenated in one comma-separated string (ambiguous split) |
| `milestones` | Present for 100%. Approval date 27,550; effectiveness 15,011; negotiations 14,909; concept review 14,830; appraisal 14,826; ICR/NCO 13,684 |
| `isr_ratings` | 32.1%. **Exactly one ISR date per project** (latest only). Procurement rating present for 25.1% |
| `ieg_ratings` / `icr_ratings` | 45.2% / 22.8% |
| `countrycode` | Always a list; single element for all 28,193 (regional projects use regional codes) |
| v2 vs v3 | 5,842 IDs only in v3 (new projects); 378 IDs only in v2 (230 pipeline, 136 without status, removed) |

v2 profile (stale index) for comparison: the `fl=*` sample exposed 491 fields. Rating fields there are padded arrays of
`"NIL"` (e.g. `overall_currentrating` is `NIL`-only for at least 63% of the 500-record sample), and `statusdate` holds `1900-01-01` sentinels.

---

## 5. Documents (WDS v3)

| Field (type sample n=1,200) | Populated % | Notes |
|---|---:|---|
| `id`, `guid`, `chronical_docm_id`, `docdt`, `pdfurl`, `txturl`, `disclosure_date`, `datestored`, `majdocty` | 100 | |
| `projectid` | 100 | **8.25% hold several comma-separated P-numbers** |
| `lang` | 99.2 | English 97.6% |
| `count` (country), `admreg` | 99.8 / 99.4 | country names as text; `count_key` numeric |
| `repnb` (report no.) | 87.5 | |
| `versiontyp` | 87.1 | Values (cluster v2): Final, Buff cover, Gray cover, Revised, Initial appraisal, `draft`, `2`… |
| `abstracts` / `keywd` | 55.3 / 79.4 | |
| `last_modified_date` | **19.2** | |
| `no_of_pages` | not in v3 (99.8% in v2) | |

Other checks:
- Cluster: 22,337 records and 22,329 distinct IDs. The 8 repeats are documents linked to two cluster projects.
- 1,783 records share project + type + date + title (duplicate uploads or language versions; **not resolved**).
- [SOURCE] v2 contains `docdt` = 2026-12-31 for documents disclosed 2025-01-01 (future dates).
- [SOURCE] Facet values contain newlines (`Project Information and Integrated\n Safeguards Data Sheet`), mojibake (`BokmÃl`),
  code-like languages (`sl`, `sp`) and synonyms (`Bengali`/`Bangla`, `Indonesian`/`Bahasa (Indonesian)`).

---

## 6. IEG ratings (DS00053, 12,629)

| Column | Populated | Sentinel | Notes |
|---|---:|---:|---|
| `Project ID` | 100% | | 12,628 distinct; **P122764 appears twice** [SOURCE] |
| `Outcome` | 98.8% | 1.2% `Not Rated` | Satisfactory 5,639; MS 3,153; U 1,870; MU 1,224; HS 398; HU 194 |
| `Bank Performance` | 67.5% | 32.6% `Not Rated` | |
| `Quality at Entry` / `Quality of Supervision` | 71.0% / 76.3% | 29.0% / 23.7% | |
| `M&E Quality` | 40.4% | 59.7% | Different scale (Negligible / Modest / Substantial / High) |
| `Global Practice` / `Practice Group` | 64.0% / 63.9% | 36% empty | Older projects predate the Global Practice taxonomy |
| `Final Closing FY` | 64.2% | 35.8% empty | |
| `Country / Economy` | 100% | | Long official names (`Republic of India`); **no code column** |

---

## 7. Financial statements

- **DS00047 / DS00001:**
  - Every loan has a `Project ID`. Loans per project: 1 (10,740 projects), 2 (2,901), up to 10+.
  - `End of Period` = 09/30/2026 (the monthly snapshot lags the daily contract data).
  - Amounts are in USD, at current rates for undisbursed balances.
- **History (DS00975/6 via API):**
  - Snake-case columns; dates as `DD-Mon-YYYY`.
  - `exchange_adjustment_us_` arrives as a **string** while other amounts are numbers [API].
  - Monthly snapshots let point-in-time values be reconstructed.
- **Leakage-prone fields:** `cancelled_amount`, `closed_date_most_recent` and `last_disbursement_date` describe outcomes, and
  only the value as of the prediction date may be used.

## 8. Governance indicators

| Series | Non-null rows | Years | Economies | Aggregates mixed in |
|---|---:|---|---:|---:|
| `GOV_WGI_CC.EST` | 5,588 | 1996, 1998, 2000, 2002–2025 | 208 | 180 aggregate rows |
| `IQ.CPA.TRAN.XQ` | 2,477 | 2005–2025 | 131 | 189 aggregate rows (e.g. `AFE`) |

The API returns regional aggregates in the same response, so they must be filtered out by ISO2 / aggregate flag.

---

## 9. Issue register

| # | Issue | Dataset | Class | Impact | Handling in discovery |
|---|---|---|---|---|---|
| 1 | No contract data FY2017–FY2019 | DS00005/DS01004 | SOURCE | Time-series break | Documented gap |
| 2 | Legacy dataset is prior-review only, without supplier IDs | DS01004 | SOURCE | Not comparable | Kept separate |
| 3 | JV amounts split equally; row grain is contract × supplier | DS00005 | SOURCE | Double-counting risk if summed per supplier vs per contract | Grain documented |
| 4 | Individual consultants anonymised (hash IDs) | DS00005 | SOURCE | No supplier history for 22% of rows | Flag |
| 5 | Supplier ID → multiple names (580), incl. unrelated firms | DS00005 | SOURCE | Supplier-entity errors | Flag; do not auto-merge |
| 6 | Empty borrower code for Côte d'Ivoire and Somalia; regional codes | DS00005 | SOURCE | Country joins | Crosswalk + explicit "regional" class |
| 7 | WB legacy country codes ≠ ISO | DS00005, projects | SOURCE | Indicator joins | Curated 5-code alias table |
| 8 | Literal `"null"` as supplier country | DS00005 | SOURCE | Treated as sentinel | Profiler sentinel |
| 9 | API/bulk naming, date formats and types differ | Finances One | API | Mapping errors | Canonical mapping needed |
| 10 | Projects v2 and WDS v2 frozen around Jan 2025 | search API | API | Silent staleness | Use v3 |
| 11 | v3 projects lacks `lendinginstr`, `parentprojid` | search API | API | Can't filter IPF or link AF parents from v3 | Use DS00053 / contract presence; open item |
| 12 | Back-filled tender notices (published after signing / 0-day deadline) | procnotices | SOURCE | Invalid tender start dates | Validity rule (ML report) |
| 13 | DS00979 deadline = publication date placeholders | DS00979 | SOURCE | False deadlines | Use API deadlines only |
| 14 | Award notices published a median 127 days late | procnotices | SOURCE | Late-arriving data | Refresh design |
| 15 | Multi-project document `projectid` (comma list) | WDS | SOURCE | Many-to-many doc↔project | Split on comma |
| 16 | Future `docdt`; low `last_modified_date` coverage | WDS | SOURCE | Unreliable change tracking | Snapshot diffing |
| 17 | Facet label noise (newlines, mojibake) | WDS | SOURCE | Filter mismatches | `clean_label()` |
| 18 | Duplicate project in IEG (P122764 ×2) | DS00053 | SOURCE | Label duplication | Flag |
| 19 | Profiler showed spurious date ranges / capped distinct counts | our code | PARSE | Misleading report | **Fixed** + tests |
| 20 | `parse_date` initially lacked plain MM/DD/YYYY; DD/MM fallback removed | our code | PARSE | Silent mis-parse risk | **Fixed** + tests |
| 21 | Procurement-plan headers broken by line wraps (`Contract Complet ion`) | PP PDFs | PARSE | Column naming; **under-counted fields** (123 header variants for 71 columns) | **Fixed 2026-10-21**: `merge_header_variants()` + tests; p05 re-run (status 100% populated, not 58%) |
| 22 | Garbled overlapping text in PP status (`Eionn cours d'exécut`) | PP PDFs | SOURCE/PARSE | Status misclassification (7 rows) | Flag |
| 23 | ISR rating values prefixed by private-use glyph U+F06C | WDS ISR `.txt` | SOURCE | Naive parsing finds no ratings | Stripped / used as delimiter in `wbdisc/isr.py` (2026-10-21) |
| 24 | Three ISR text layouts, incl. vertically flattened values (2/27 unparseable) | WDS ISR `.txt` | SOURCE | Brittle text parsing | Prefer PDF table parsing for ISRs |
| 25 | Duplicate ISR records (same project + sequence number up to 8×, same language) | WDS v3 | SOURCE | Double-counting of supervision evidence | De-duplicate on (project, seq no) |
| 26 | v3 `major_sector_name` is a comma-joined string whose items contain commas; FY17 / Historic taxonomy prefixes (67.4%) | Projects v3 | SOURCE | Ambiguous split | Use structured `major_sectors` (82.9%, 35 codes) |
| 27 | DS00005 Global Practice labels mix variants ("Water/Sanit/Waste" vs "Water, Sanitation"; "Energy & Extractives" vs "Energy and Mineral") | DS00005 | SOURCE | Category mismatch | Curated GP map (D-21) |
| 28 | Procurement-plan cell values carry injected spaces ("Open - Internationa l", "16,447,818. 74") and multilingual vocabularies (*A posteriori*, *Demande de prix*, *Previa*) | PP PDFs | PARSE + SOURCE | Category / amount parsing | Whitespace-insensitive matching; multilingual maps |
| 29 | Two official IEG sources disagree for 793 projects (782 = later PPAR in DS00053 vs earlier ICRR / PCR in v3 `ieg_ratings`) | DS00053, v3 | SOURCE (supersession) | Conflicting labels | Keep both; precedence policy (D-18) |

*Rows 23–29 and the #21 update were added in the 2026-10-21 alignment review
([`ASSESSMENT_ALIGNMENT_REPORT.md`](../../docs/requirements/ASSESSMENT_ALIGNMENT_REPORT.md)).*

## 10. Alignment update (2026-10-21): additional field profiles

| Field | Population | Result | Evidence |
|---|---|---|---|
| v3 `pdo` (development objective) | 28,193 projects | 57.6% populated (95.7% of the FY2020+ cluster) | G1 |
| v3 `envassesmentcategorycode` (legacy EA category) | 28,193 | 55.6% populated: C 7,343; B 6,518; A 1,348; F 363 | G1 |
| v3 `esrc_ovrl_risk_rate` (ESF risk) | 276 cluster | 51.5% populated: S 56; M 50; L 25; H 11. Not in the Phase 1 population field list. | G1 |
| v3 `financers` (co-financing) | 28,193 | 64.5% populated; 26.0% of projects have external co-financiers | G1 |
| v3 `major_sectors` (structured) | 28,193 | 82.9% populated; 35 major-sector codes | G5 |
| Procurement-plan fields (merged headers) | 1,137 activities | Market approach 99.9%; estimated amount 89.3%; actual amount 99.9%; process status 100%; procurement process 45.4% | p05 re-run |
