# Data Source Inventory

**Phase 1: Public Data Discovery.** Run date 2026-10-20. All accessibility statements come from requests recorded in
[`evidence/access_log.jsonl`](../evidence/access_log.jsonl) (URL, HTTP status, timestamp for every call). Raw samples
and their SHA-256 checksums are listed in [`samples/MANIFEST.jsonl`](../samples/MANIFEST.jsonl) and
[`data_cache/MANIFEST.jsonl`](../data_cache/MANIFEST.jsonl).

**Limitation:** the project's technical-assessment document was not available. The only workspace file,
`MasterPrompt_01.md`, is empty (0 bytes). Requirements were taken from the Phase 1 brief.

## Legend

| Label | Meaning |
|---|---|
| **Verified** | Retrieved successfully, and the schema/values were inspected in downloaded data |
| **Partially verified** | Reachable, but only part of it was retrieved, or a documented feature failed |
| **Documented only** | Stated on an official page and not tested |
| **Unavailable** | Request failed, or the source does not exist |

## Summary

| # | Source | Status | Recommended role |
|---|---|---|---|
| A1 | Contract Awards in IPF since FY2020 (Finances One **DS00005**) | **Verified** | Core contract fact table |
| A2 | Contract Awards in IPF FY2001–FY2016 (**DS01004**) | **Verified** | Historical context only (prior-review contracts, different schema) |
| A3 | Contract awards FY2017–FY2019 | **Unavailable** (0 rows in DS00005; not found elsewhere) | Gap; see risks |
| B1 | Projects API **v3** (`search.worldbank.org/api/v3/projects`) | **Verified** | Core project dimension, milestones, ratings |
| B2 | Projects API **v2** (`/api/v2/projects`) | **Verified, but stale** (index frozen around Jan 2025) | Do not use |
| C1 | Procurement Notices API v2 (`/api/v2/procnotices`) | **Verified** (current) | Notices, award notices, bidder detail |
| C2 | Procurement Notice bulk (Finances One **DS00979**) | **Verified** | Population statistics only; lacks reference numbers and text |
| C3 | Data Catalog entry 0037795 | **Verified** (metadata page only) | Pointer to C1; its metadata is stale (Nov 2022) |
| D1 | Documents & Reports API **v3** (`/api/v3/wds`) | **Verified** | Document metadata (619,903 docs) |
| D2 | Documents & Reports API v2 (`/api/v2/wds`) | **Verified, but stale** (549,248 docs; nothing stored after about Jan 2025) | Do not use |
| D3 | Document files (PDF + WB text rendition) | **Verified** (40 PDFs + 22 TXT downloaded) | RAG corpus |
| E1 | IBRD/IDA Statement of Loans/Credits, latest snapshot (**DS00047**, **DS00001**) | **Verified** | Loan dimension, financing context |
| E2 | IBRD/IDA historical monthly snapshots (**DS00975**, **DS00976**) | **Partially verified** (API history for 6 loans; ~1.1 GB bulk not downloaded) | Point-in-time disbursement features; extension labels |
| E3 | v3 `loan_transactions` (disbursement ledger in project record) | **Verified** (276 projects) | Dated disbursement features |
| F1 | IEG Project Performance Ratings (**DS00053**) | **Verified** (full, 12,629 rows) | Project-outcome labels |
| F2 | Worldwide Governance Indicators (Indicators API source 3) | **Verified** | Country context (as-of joins) |
| F3 | CPIA (Indicators API `IQ.CPA.*`) | **Verified** | Country context, IDA countries only |
| F4 | IEG EDS08 ratings (DS01695, 30,206 rows) | **Documented only** | Possibly all evaluations per project; to validate |

## Detailed inventory

### A1. Contract Awards in Investment Project Financing (since FY2020) — DS00005

| Field | Value |
|---|---|
| Official URL | https://financesone.worldbank.org/contract-awards-in-investment-project-financing/DS00005 |
| Access methods | (1) Bulk CSV `financesonefiles.worldbank.org/f-one/DS00005/RS00005/…csv` (109.39 MB, **verified**). (2) Bulk JSON (253.59 MB, documented). (3) REST `datacatalogapi.worldbank.org/dexapps/fone/api/apiservice?datasetId=DS00005&resourceId=RS00005&type=json&top=&skip=&filter=&select=` (**verified**) |
| Accessibility | **Verified**. API: 1,000-row pages; SQL-like `filter` (e.g. `project_id='P151416'`, `contract_signing_date>='2026-10-01'`). OData `eq` is rejected. **`filter` combined with any `select` returns HTTP 403** (HTML WAF page; reproduced in `profiling/refresh_probe.json`). |
| Date coverage | Contract signing **2019-07-01 → 2026-10-13**, FY2020–FY2027 (verified). FY2016–FY2019 return 0 rows. |
| Row count | **295,058** rows; 282,890 distinct `WB Contract Number` (verified) |
| Update frequency | Documented "Daily updates when new records are available". Observed: every row carries `As of Date` = 2026-10-20, so this is a daily full snapshot. Page `dateModified` = Oct 20, 2026. |
| Major identifiers | `WB Contract Number` / `wb_contract_number`, `Project ID`, `Supplier ID`, `Borrower Contract Reference Number` |
| Main attributes | Region, Borrower Country / Economy (+Code), Project Name, Project Global Practice, Procurement Category, Procurement Method, Contract Description, Contract Signing Date, Supplier, Supplier Country (+Code), Supplier Contract Amount (USD), Review type, Fiscal Year, Calendar year |
| **Not present** | Tender/invitation date, bid deadline, award-decision date, number of bidders, contract duration, amendment/variation fields, contract completion, local-currency amount, implementing agency |
| Access constraints | CC BY 4.0. API and bulk use **different column names** (snake_case vs display labels) and **different date formats** (`19-May-2022` vs `05/19/2022`). Bulk CSV has a UTF-8 BOM and 21,087 multi-line descriptions. |
| Documented caveats (official text) | Data entered by Borrowers in STEP. Amounts are committed (not disbursed) USD at the no-objection exchange rate. **JV contract values are split equally among members.** Subcontractors and co-financing are excluded. Supplier country = place of registration. |
| Project relevance | ML (contract-level features and labels), analytics, benchmarking, business metadata |
| Evidence | `samples/contract_awards/ds00005_api_sample.jsonl` (1,000 rows, 40 API calls), `ds00005_stratified_sample.csv` (945 rows, FY×region), bulk in cache, `profiling/ds00005_*.profile.md` |

### A2. Contract Awards in IPF (FY2001–FY2016) — DS01004

| Field | Value |
|---|---|
| URL | https://financesone.worldbank.org/contract-awards-in-investment-project-financing-(since-fy-2001-fy-2016)/DS01004 |
| Access | Bulk CSV (52 MB, **verified**) |
| Coverage | FY2001–FY2016, **146,802** rows; `As of Date` = 03/11/2022 for all rows (frozen) |
| Identifiers | `Contract Number`, `Project ID`, `Borrower Contract Reference Number`. **No `Supplier ID`**, no country codes, no review type. |
| Notes | DS00005's description calls this "Prior reviewed project procurement contracts since FY2001", so it is **not** a complete population. Region labels are an older taxonomy (`AFRICA EAST`, `MIDDLE EAST AND NORTH AFRICA`). 0 contract-number overlap with DS00005. |
| Relevance | Historical benchmarking only. Schema-incompatible with DS00005 for supplier analytics. |

### A3. Contract awards FY2017–FY2019 — gap

DS00005 returns 0 rows for `fiscal_year` 2016, 2017, 2018 and 2019 (verified by API count). DS01004 ends at FY2016. No public
dataset covering these years was found in this phase. Procurement notices (C1) do exist for those years: DS00979 has
3,692 / 10,734 / 19,262 notices of all types published in FY2017 / FY2018 / FY2019. The Contract Award share of those was
not separately counted. Award notices could be a partial, text-based substitute (**unverified**).
**Status: unresolved.**

### B1. Projects API v3 — recommended

| Field | Value |
|---|---|
| URL | `https://search.worldbank.org/api/v3/projects?format=json` (used by the live projects.worldbank.org pages; found in their HTML). Bulk `…/api/v3/projects/all.xlsx` responds (HTTP 206 partial range); not downloaded. |
| Accessibility | **Verified**. `rows` ≤ 1,000, `os` offset (≤ 100,000), `fl` field list, `id=` filter, `srt`/`order`, facets. |
| Coverage | **28,193** projects; board approval 1947-05-09 → 2027-10-26 (pipeline). Status: Closed 18,319, Dropped 6,907, Active 2,624, Pipeline 343. |
| Identifiers | `id` (P-number), `countrycode` (WB codes, list), loan ids inside `financers` / `loan_transactions` |
| Main attributes | 70 fields in `fl=*`. Includes `milestones` (concept review, appraisal, negotiations, approval, effectiveness, ICR dates), `isr_ratings` (latest ISR incl. **Procurement** rating), `icr_ratings`, `ieg_ratings`, `loan_transactions` (dated disbursement/repayment ledger), `financers`, `major_sectors`, `theme_list`, `impagency`, `borrower`, `closingdate`, `proj_last_upd_date`, `indicators`, `geo_locations` |
| **Missing vs v2** | `lendinginstr` (IPF/DPF/PforR) is absent in v3. `parentprojid` (additional-financing parent) is absent. `no_of_pages`-style document fields don't apply. |
| Relevance | Project dimension, milestones, ratings, financing context |
| Evidence | `data_cache/projects_v3_population.jsonl.gz` (28,193), `samples/projects/projects_v3_cluster_allfields.jsonl.gz` (276, `fl=*`) |

### B2. Projects API v2 — stale; do not use

`https://search.worldbank.org/api/v2/projects` responds normally (22,729 projects, 491 fields across a 500-record `fl=*`
sample). However, the most recent `boardapprovaldate` is **2024-12-20**, the most recent `p2a_updated_date` is **2025-01-20**,
and 50 projects referenced by current contracts (556 rows) are missing. 378 v2 IDs (mostly pipeline entries with no status)
no longer exist in v3. This is an observed fact. Whether v2 is officially deprecated is **unverified**.

### C1. Procurement Notices API (v2)

| Field | Value |
|---|---|
| URL | `https://search.worldbank.org/api/v2/procnotices?format=json` (v3 returns HTTP 404) |
| Accessibility | **Verified, current** (notices dated 19-Oct-2026). `rows` ≤ 1,000; **`os` > 100,000 rejected** (`$skip` "Value must be between 0 and 100000"; Azure Cognitive Search index `procnoticesnew`). Filters: `project_id=`, `notice_type_exact=`, facets (e.g. `project_ctry_name_exact`). `strdate`/`enddate` are **silently ignored**. Date equality filters fail with HTTP 400 (type mismatch). |
| Coverage | **423,546** notices: Contract Award 312,305; REOI 60,988; IFB 46,009; GPN 3,471; Prequalification 733. Publication from Sep 2005 (DS00979). |
| Identifiers | `id` (`OP000xxxxx`), `project_id`, `bid_reference_no` |
| Main attributes | `notice_type`, `noticedate`, `submission_date`, `submission_deadline_date/time`, `notice_status` (Published/Revised/Cancelled/Draft), `notice_lang_name`, `bid_description`, `procurement_group`, `procurement_method_code/name`, `contact_organization`, `contact_ctry_name`, `notice_text` (HTML) |
| Key content | Contract Award `notice_text` is a STEP template (98.7% of the cluster sample). It carries loan IDs, reference number, **notice version**, award-notification or contract-signature date, contract duration, awarded / evaluated / rejected bidders **with STEP supplier IDs**, bid prices at opening, evaluated price, signed price in **local currency** |
| Constraints | Personal data: `contact_name`, `contact_email`, `contact_phone_no` and names/e-mails inside `notice_text` |
| Evidence | `samples/procnotices/procnotices_cluster_sample.jsonl.gz` (30,977 notices, all notices of 276 projects), `procnotices_global_sample.jsonl.gz` (800, stratified by type) |

### C2. Procurement Notice bulk — DS00979

`https://financesonefiles.worldbank.org/f-one/DS00979/RS00909/Procurement_Notice.csv` (195 MB, **verified**). 422,557 rows,
15 columns. `ID` is **numeric** (`14`), whereas the API uses `OP00000014`; the URL column embeds the API form in 100% of rows.
It has **no `bid_reference_no` and no notice text**, so it can't support notice↔contract joins. `Deadline Date` equals
`Publication Date` for 100% of award notices and for 31.6% of IFBs (placeholder values). Region names mix current and legacy
labels (`Mid East,North Africa,Afg,Pak` vs `Middle East And North Africa`).

### C3. Data Catalog 0037795

https://datacatalog.worldbank.org/search/dataset/0037795/world-bank-procurement-notices. The page loads. It lists only the
procnotices API as a resource. "Metadata last updated on Nov 16, 2022" (stale). The DDH metadata API
`datacatalogapi.worldbank.org/ddhxext/DatasetView` returned **HTTP 429** on first use (rate limited) and was not used again.

### D1/D2/D3. Documents & Reports

| Field | Value |
|---|---|
| URLs | `https://search.worldbank.org/api/v3/wds` (recommended), `/api/v2/wds` (stale); files at `documents.worldbank.org/curated/en/<guid>/pdf/<name>.pdf` and `/text/<name>.txt` |
| Accessibility | **Verified.** Filters `projectid=`, `docty_exact=`, `lang_exact=`, `strdate`/`enddate` (on `docdt`; honoured), `srt=docdt`. Offset limit 100,000. File URLs are `http://`, and redirect 302→302→301 to `https://documents1.worldbank.org/...`. |
| Coverage (v3 facets) | 619,903 docs. PAD 7,000; ISR 59,693; ICR 8,617; ICR Review 7,847; PPAR 2,648; **Procurement Plan 143,335**; Project Paper 13,163; Note on Cancelled Operation 191 |
| Identifiers | `id` (WDS entity id), `guid` (URL path id), `docm_id`/`chronical_docm_id`, `repnb` (report number), `projectid` (**comma-separated list when a document covers several projects**: 8.25% of the type sample) |
| Metadata populated (v3 type sample, n=1,200) | projectid 100%, docdt 100%, lang 99.2%, pdfurl/txturl 100%, versiontyp 87.1%, repnb 87.5%, abstracts 55.3%, **last_modified_date 19.2%**; v3 has no `no_of_pages` field (v2 had it) |
| Language | English 97.6% of type sample; 100+ language labels in facets with inconsistencies (`sl`, `sp`, `BokmÃl`, `Bengali`/`Bangla`) |
| Evidence | `samples/documents/wds_v3_*.jsonl.gz`, `wds_*` (v2), 22 PDFs + 22 TXT in `samples/documents/files/`, 38 procurement-plan PDFs in `samples/documents/pp_files/` |

### E. Financial and disbursement data

| Source | Status | Notes |
|---|---|---|
| DS00047 IBRD Statement of Loans and Guarantees – latest | **Verified** (9,538 rows, 35 cols) | `End of Period` 09/30/2026 (monthly). `Loan Number` (`IBRD87250`), `Project ID`, original/cancelled/disbursed/undisbursed amounts, agreement/approval/effective/closed/last-disbursement dates |
| DS00001 IDA Statement of Credits, Grants and Guarantees – latest | **Verified** (11,486 rows, 32 cols) | Same structure with `Credit Number` (`IDA70480`, `IDAD1010`) |
| DS00975 / DS00976 historical monthly snapshots (Apr 2011 →) | **Partially verified** | API filtered by loan returned full monthly histories (507 / 408 rows for 3 loans each). Bulk CSVs 566.94 MB / 556.6 MB (1,585,498 / 1,612,290 rows), **not downloaded**. History shows `closed_date_most_recent` changing over time (IDA44840: 3 extensions). |
| v3 `loan_transactions` | **Verified** (cluster) | Dated `LOAN_DISB`, `LOAN_REPAY`, `INT_CHARGES` by `financier_loan` (`IDA-57620`) |

Financial milestones are **loan-level**. Nothing in these sources links a disbursement to an individual contract.

### F. Evaluations and country indicators

| Source | Status | Coverage | Identifiers | Notes |
|---|---|---|---|---|
| DS00053 IEG Project Performance Ratings | **Verified** (12,629 rows; 12,628 distinct `Project ID`) | Evaluation FY 1973–2027; `As of Date` 10/18/2026 | `Project ID`, **no country code** (long official country names, e.g. "Republic of India") | Documented: **only the latest evaluation per project** is kept. Outcome, Bank Performance, Quality at Entry, Quality of Supervision, M&E Quality, Evaluation Type (ICRR 6,909; PPAR 3,713; PCR 1,482; EVM 487) |
| WGI (Indicators API source 3) | **Verified** | 1996–2025 (**1997, 1999, 2001 missing**); 208 economies; last updated 2026-09-25 | ISO2 `country.id`, ISO3 `countryiso3code` | Codes are **`GOV_WGI_CC.EST`** etc. Legacy `CC.EST` is rejected (HTTP 200 with error 120 "Invalid value"). Estimates and new 0–100 scores (`.SC`) |
| CPIA (`IQ.CPA.*`) | **Verified** | 2005–2025; 131 economies with values (IDA-eligible); last updated 2026-10-08 | ISO2/ISO3 | Null for all non-IDA countries and for years before 2005 |

## Access-failure log (exact errors)

All rows are reproduced by `scripts/p04_refresh_probe.py` (`endpoint_existence`, `fone_ds00005_filter_select_combination`, `procnotices_sort_and_filter`), and each request is recorded in `evidence/access_log.jsonl` (summary: [`evidence/ACCESS_SUMMARY.md`](../evidence/ACCESS_SUMMARY.md)).

| Attempt | Result | Resolution |
|---|---|---|
| `GET /api/v2/contractdata` (guessed contract-award search endpoint) | HTTP 404 | Use DS00005 |
| `GET /api/v3/procnotices` | HTTP 404 (Tomcat) | v2 procnotices is current |
| procnotices / wds `os` > 100000 | HTTP 400 `$skip` "Value must be between 0 and 100000" | Partition by project, type or country |
| procnotices `noticedate=19-Oct-2026` / `submission_date=…` | HTTP 400 "binary operator with incompatible types … Edm.DateTimeOffset and Edm.String" | No server-side date filtering on notices |
| Finances One `filter=…&select=…` | HTTP 403 HTML page (WAF) | Use `filter` without `select` |
| Indicators API `GE.EST?source=3` | **HTTP 200** with error body `{"message":[{"id":"120","key":"Invalid value"}]}`, so a status check alone would pass | Use `GOV_WGI_GE.EST`; always validate the body |
| DDH `datacatalogapi.worldbank.org/ddhxext/DatasetView` | HTTP 429 "Rate limit is exceeded. Try again in 39 seconds." (on a single request, reproduced) | Not needed; Finances One pages hold the metadata |
| `finances.worldbank.org/resource/kdui-wcs3.json` (legacy Socrata ID still listed as DS00005 `archive_id`) | HTTP 200 HTML, not JSON | Legacy platform; not usable |

No access restriction was bypassed. No credentials were used.

---

## Alignment update (2026-10-21): additional sources examined

Added after the technical assessment became available (§3 of the assessment). Probes:
[`scripts/p07_assessment_gap_probes.py`](../scripts/p07_assessment_gap_probes.py) →
[`profiling/assessment_gap_probes.json`](../profiling/assessment_gap_probes.json).

| # | Source | Status | Evidence / notes | Relevance |
|---|---|---|---|---|
| G1 | **Open Knowledge Repository** (`openknowledge.worldbank.org/server/api`, DSpace 7 REST) | **Verified** (search API, JSON) | "procurement lessons learned" → 149 items. Metadata includes `okr.guid`, `okr.pdfurl`, `okr.identifier.report`, `okr.doctype`, `okr.region.country`, `okr.sector`, `okr.date.lastmodified`. **5 / 5** sampled GUIDs resolve to WDS v3 documents, so OKR largely overlaps WDS (extent beyond 5 not measured). | Publications / thematic knowledge; de-duplicate by GUID |
| G2 | **Procurement Regulations for IPF Borrowers** (`thedocs.worldbank.org/.../ProcurementRegulations.pdf`) | **Verified** (downloaded, 5.6 MB) | 4th ed., Nov 2020, 142 pages, text layer present. Procurement framework landing page reachable (HTML; links rendered client-side). | Policy constraints for RAG |
| G3 | Procurement guidance notes, Standard Procurement Documents | **Partially verified** | WDS v3 full-text: "procurement guidance note" 202 hits; "Standard Procurement Document" 40,947 hits (full-text matches, **not** a document-type count). Individual guidance PDFs were not enumerated. | Tender-design substitute |
| G4 | Country Partnership Frameworks, Systematic Country Diagnostics, IEG thematic evaluations (WDS v3 `docty_exact`) | **Verified** (counts) | CPF 864; SCD 282; IEG Evaluation 1,019. 0% linked to cluster projects (country / sector-level). | Country / sector context |
| G5 | Aide Memoires (supervision mission notes) | **Verified** (counts) | 2,815 in WDS v3; 34.1% of cluster projects have ≥ 1 | Supervision notes (assessment §1, Module 4) |
| G6 | Auditing documents | **Verified** (counts) | 30,340 in WDS v3; 88.8% of cluster projects. Procurement content not examined. | Audit findings (assessment §1) |
| G7 | Environmental & Social Commitment Plans | **Verified** (counts) | 5,459 in WDS v3; 48.2% of cluster projects | E&S context |
| G8 | ISR series (v3 wds + `.txt`) | **Verified** (2 projects, 24 ISRs) | 43 and 31 ISR records; duplicates per sequence number; component ratings absent (see RAG alignment update) | Supervision ratings history |
| G9 | World Bank debarred firms list (`worldbank.org/.../procurement/debarred-firms`) | **Documented only** | HTML page (HTTP 200); listing data is loaded by a client-side service. **No open dataset or documented API found; not scraped** (no bypass of access mechanisms). | Supplier risk; see D-24 |

**Fields added to existing sources:**
- **B1 Projects v3:**
  - `esrc_ovrl_risk_rate` (ESF overall E&S risk; 51.5% of cluster projects)
  - `envassesmentcategorycode` (legacy EA category; 55.6% of projects)
  - `pdo` (57.6%)
  - `financers` with `projectfinancialtype` (co-financing: 26.0% of projects have multilateral / bilateral / trust-fund /
    private co-financiers)

  The E&S risk classification and co-financing structure named in assessment §3.1 are therefore **available**.
- **No Word documents** were observed in any source. Formats: CSV, JSON, XLSX, HTML, PDF, TXT.
