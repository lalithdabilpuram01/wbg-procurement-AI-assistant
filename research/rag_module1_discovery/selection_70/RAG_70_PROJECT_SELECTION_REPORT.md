# RAG scope: 70-project selection and PDF download plan (read-only planning)

- **Date:** 2026-10-24
- **Status:** planning only. **No PDFs downloaded, nothing installed, no Databricks object or the 2,000-project cohort changed, no embeddings or parsing.**
- **Network use:** metadata-only API listings for the 70 selected projects (`search.worldbank.org/api/v3/wds`), plus 510 HTTP `HEAD` checks (headers only, no PDF bodies). All logged in `discovery/evidence/access_log.jsonl`.
- **Read-only Databricks queries:** Silver `contract_awards` and Gold `gold_contract_analytics`, for the plan-to-contract evidence probe.
- **Code:**
  - `rag/module1_discovery/m09_select_70_projects.py`: project selection (seed 20261025)
  - `m10_document_selection_70.py`: document selection, issues, `HEAD` checks

## Deliverables (all in `rag/module1_discovery/selection_70/`)

| File | Content |
|---|---|
| `PROJECT_SELECTION_MANIFEST.csv` | **70 proposed projects**: attributes, available document counts by type, selection reasons |
| `DOCUMENT_DOWNLOAD_MANIFEST.csv` | **510 recommended documents** (Tier 1: 380, Tier 2: 130): official document ID, title, type, role, dates, report number, language, source `pdfurl`, canonical URL after redirect, `HEAD` status, size, tier |
| `DOCUMENT_INVENTORY_BY_PROJECT.csv` | Per project: available vs selected counts by type |
| `DOCUMENT_ISSUES.csv` | Duplicates, superseded ISR re-uploads, translations, unselected PADs, missing PDF URLs, missing metadata |
| `FIRST_BATCH_10_FOR_MANUAL_INSPECTION.csv` | The first 10 PDFs to download and inspect |
| `SAMPLE_METADATA_RECORDS.json` | Real document records (PAD, plan) and a verified plan-row-to-contract evidence link |
| `all_documents_metadata_70_projects.jsonl` | Full metadata listing for the 70 projects (6,304 documents) |
| `project_selection_summary.json`, `document_selection_summary.json`, `evidence_projects.json` | Machine-readable summaries |

## 1. Project selection

### Method (reproducible: `m09`, seed 20261025)

1. **Eligibility (1,362 of 2,000):**
   - ≥ 1 PAD, ≥ 2 ISRs and ≥ 1 procurement plan (from the API counts of all 2,000 projects);
   - ≥ 10 contracts and ≥ 10 notices (useful procurement activity, from the cohort manifest).
2. **Mandatory:**
   - **9 verifiable-evidence projects:** procurement-plan activity references match contract borrower references exactly, ≥ 5 matches each (see §5).
   - The local parser-control project **P145502** (PAD, ISR, ICR, ICR Review and plan already local).
   - P131049 is not eligible (fewer than 10 contracts or notices). Its local PDFs can still serve as parser controls outside the 70.
3. **Greedy diversity fill:** each step adds the eligible project with the highest gain on unmet quotas:
   - region, proportional to the cohort ("Other" at most 1);
   - status (≥ 30 closed, ≥ 25 active);
   - approval-FY band (≥ 8 each);
   - sector (≥ 2 where available);
   - a new country, with at most 2 per country;
   - plus small bonuses for ICR, ICR Review, API notice details and plan-to-contract matches.

### Result

| Dimension | Mix (70) |
|---|---|
| Countries | **70 distinct country codes** (64 single-country projects + 6 regional / multi-country) |
| Region | W&C Africa 14; E&S Africa 13; East Asia & Pacific 10; Europe & Central Asia 10; Latin America & Caribbean 9; MENA-AfPak 7; South Asia 6; Other 1 |
| Status | Closed 45 / Active 25 |
| Approval FY | ≤ 2014: 13; 2015–17: 19; 2018–20: 15; 2021–23: 15; 2024+: 8 |
| Sector (14) | Transportation 10, Water/Sanitation 9, Health 8, Public Administration 8, Agriculture 7, Education 6, Industry/Trade 4, Social Protection 4, ICT 3, Energy & Extractives 3, Financial 2, Energy & Mineral Resources 2, Social Sustainability 2, Digital Development 2 |
| Evidence | 45 with ICR, 45 with ICR Review, 52 in the API notice-detail set (184), **9 with verifiable plan-to-contract matches** |

## 2. Available documents for the 70 projects (API listing, 2026-10-24)

| Type | Available | Projects with ≥ 1 |
|---|---|---|
| PAD | 78 | 70 |
| ISR | 912 | 70 |
| ICR | 46 | 45 |
| ICR Review (IEG) | 45 | 45 |
| Procurement Plan | 3,030 | 70 |
| Other types (Project Papers, audits, safeguards, agreements…) | 2,193 | — |
| **All** | **6,304** | |

## 3. Recommended documents and download count

### Selection rules (`m10`)

| Type | Rule | Role |
|---|---|---|
| PAD | Exactly one: the **original appraisal**. English preferred (translations are separate WDS records), then `Final`, then earliest; titles mentioning additional financing excluded | `PAD_original` |
| PAD translations | Same-period non-English versions (Spanish 3, Arabic 3), kept for multilingual evaluation | `PAD_translation` (Tier 2) |
| ISR | After removing re-uploads (same sequence number, keep the latest stored): **first and latest**, plus the **middle** one when ≥ 5 distinct ISRs. Represents three stages | `ISR_first` / `ISR_middle` / `ISR_latest` |
| ICR, ICR Review | All distinct (normally 1 each) | `ICR`, `ICRR` |
| Procurement Plan | **Latest** version, plus the **earliest** (temporal comparison) | `PP_latest` / `PP_earliest` |

### Tiers and counts

| Option | Contents | Documents | Already local | **New PDFs** | Est. size |
|---|---|---|---|---|---|
| **Tier 1 (recommended)** | PAD 70; ISR first + latest 140; ICR 46; ICRR 45; latest plan 70; earliest plan for the 9 evidence projects 9 | **380** | 18 | **362** | **≈ 159 MB** |
| Tier 2 (optional) | ISR middle 63; earliest plan for the other 61 projects; PAD translations 6 | 130 | 1 | 129 | ≈ 28 MB |
| Minimal (≈ 300 target) | PAD + ISR first / latest + ICR + ICRR | 301 | 3 | 298 | ≈ 151 MB |

**Recommendation:** Tier 1, **362 new PDFs**.
- It is about 25% above the 300 planning target, because the latest procurement plan is included for every project. Those plans are the documents that make contract-level evidence linkable (§5).
- If the target must hold, the minimal option (298 new PDFs) drops the plans.

**Size caveat:** the servers sent `Content-Length` for only 17 of 510 PDFs (chunked responses). Sizes are estimated from local per-type medians: PAD 0.87 MB, ISR 0.21, ICR 1.22, ICRR 0.15, plan 0.16. Outliers exist: a local PAD is 31.9 MB and one local plan is 101 MB / 4,184 pages. Budget **≈ 160–400 MB** for Tier 1.

**Availability:** **510/510 `HEAD` = 200 `application/pdf`**, publicly accessible, no login. `http://` URLs take 3 redirects and `https://` 2, ending at `documents1.worldbank.org`; the canonical final URL is recorded per document.

## 4. Duplicates, versions, unavailable PDFs, missing metadata

| Issue | Count | Handling |
|---|---|---|
| Duplicate records (same type, date and title) | 201 (plans 159, ISR 41, PAD 1) | Latest stored kept; others listed with the kept ID |
| Superseded ISR re-uploads (same sequence number) | 9 | Latest kept |
| Additional PAD (AF or other version) | 1 | Not selected; listed |
| PAD translations | 6 | Tier 2 |
| No PDF URL in the API | 34 (32 plans, 2 ISRs) | None selected |
| Selected PDFs unavailable | **0** | — |
| Missing metadata among the 510 selected | `versiontyp` 152 (mostly ISRs and plans, which don't use it); `repnb` 38; `lang` 5 (all ICRRs) | Kept NULL with status `not_published` |
| `datestored` | Returned for **0 of 6,304** records when requested via `fl` (the earlier v3 bulk pull had it); `last_modified_date` 1,132 / 6,304 | Use `docdt` + `disclosure_date` (6,283 / 6,304) |
| Page count | Not provided by the v3 API | Measured after download |
| Title differences | e.g. the ICRR for P132833 is titled "Sri Lanka – Transport Sector Project" | Link by project ID and type, never by title |

**Earlier and later versions are kept on purpose:**
- the first, middle and latest ISRs;
- the earliest and latest plans;
- the ICR and ICR Review;

so that temporal comparison and conflicting-evidence tests (latest ISR vs ICR vs IEG) are possible.

## 5. Verifiable procurement evidence in documents

Probe (read-only), using 591 activity references parsed earlier from 40 local procurement-plan PDFs (`evidence/pp_reference_to_contract_match_probe.json`):
- **203 references match a contract's borrower reference exactly** (same project, after whitespace removal and upper-casing).
- 16 of 26 projects have at least one match.
- **9 of the 70** have ≥ 5 matches: P132833, P148850, P165782, P173367, P175325, P175659, P176948, P181385, P500392.
- In all 9, the matching plan version is the selected **latest plan**, and it is already local.

**Example (verified):** plan **34172790** (P132833, Sri Lanka), page 3, Works, activity `LK-RDA-204059-CW-RFQ` → contract **1674110** (Request for Quotations, US$17,652.71).
- The plan records "Signed Contract | Actual" as 2020-12-29; DS00005 records signing on 2020-12-24.
- That is a 5-day **conflict**: keep both and cite both sources.
- A second match on the same page differs by three months (2019-11-15 vs 2020-02-18).

The plan-row-to-contract link is verifiable by ID. Links from narrative sections (PAD procurement arrangements, ICR fiduciary sections) to specific contracts are not expected to be ID-based (the earlier probe found 0 contract numbers in narrative text), so they stay project-level or `candidate`.

## 6. First batch of 10 PDFs for manual inspection

From `FIRST_BATCH_10_FOR_MANUAL_INSPECTION.csv`. All returned `HEAD` 200 `application/pdf`.

| # | Project | Type | Doc ID | Date | Title | Why |
|---|---|---|---|---|---|---|
| 1 | P132833 | PAD | 26256705 | 2016-04-18 | Sri Lanka – Transport Connectivity and Asset Management Project | Full-life-cycle evidence project; appraisal baseline |
| 2 | P132833 | ISR | 26733306 | 2016-08-26 | … Transport Connectivity and Asset Management Project : P132833 … | Early stage |
| 3 | P132833 | ISR | 34098960 | 2023-06-21 | Disclosable Version of the ISR – Transport Connectivity and Asset Management … | Late stage, vs ICR / ICRR |
| 4 | P132833 | ICR | 34345370 | 2024-06-19 | Sri Lanka – Transport Connectivity and Asset Management Project | Completion and lessons |
| 5 | P132833 | ICRR | 34407877 | 2024-10-18 | Sri Lanka – Transport Sector Project | IEG validation (title differs) |
| 6 | P132833 | Plan | 29493164 | 2018-01-09 | Sri Lanka / SOUTH ASIA – P132833 – … Procurement Plan | Earliest plan; pair with the local latest plan 34172790 (20 contract matches) |
| 7 | P181385 | PAD | 34213041 | 2023-12-06 | South Sudan – Health Sector Transformation Project | Active Health evidence project (26 matches) |
| 8 | P181385 | ISR | 40120706 | 2026-08-05 | Disclosable Version of the ISR – South Sudan Health Sector Transformation … | Latest status of an active project |
| 9 | P106284 | PAD | 9361149 | 2008-04-07 | Turkey – Cadastre Modernization Project | Oldest PAD in the set (older template) |
| 10 | P106284 | ICR | 34203596 | 2023-11-27 | Türkiye – Land Registration and Cadastre Modernization Project | Same project 15 years later |

## 7. Sample metadata records

In `SAMPLE_METADATA_RECORDS.json`:
- the P132833 **PAD** record: ID 26256705; report PAD1265; Buff cover; English; GUID; source and canonical URLs; `HEAD` 200; size basis "local type median";
- the P132833 **latest plan** record;
- the **verified evidence link** above.

Fields the source does not publish are left NULL with a note. Nothing is invented.

## 8. Selection criteria and potential biases

| Bias | Effect | Mitigation |
|---|---|---|
| Eligibility needs PAD + ≥ 2 ISRs + plan + ≥ 10 contracts and notices | Excludes 638 cohort projects: small, very new, or missing documents | Disclose; Tier 2 or a later batch could add low-activity projects |
| 45 closed / 25 active | Over-represents completed projects (ICR and IEG available) | Kept 25 active and 23 approved FY2021+ for recency |
| ICR Review projects | Inherit IEG selection (evaluated, older projects) | Treat IEG evidence as a latest snapshot; precedence policy |
| Evidence projects | Chosen because their plans parse well and match contracts | Mostly active, 2026 plan versions; label as "verifiable subset", not representative |
| English-first | 498 of 510 selected docs are English | 6 PAD translations + 1 French plan in Tier 2 for multilingual tests |
| One project per country | Maximises country diversity, but can't compare within a country | Acceptable for the PoC |
| Cohort design | The 2,000 cohort is a stratified sample, not the WB population | Unchanged |

## 9. Assessment Module 1 coverage and remaining gaps

| Requirement | Coverage with this plan | Gap |
|---|---|---|
| Structured + unstructured ingestion | 70 projects already in Bronze, Silver and Gold, plus 362 new PDFs with API metadata | Ingestion pipeline not built |
| Entity resolution | Document → project by source ID; plan row → contract by exact reference (9 evidence projects, 203 matches in the probe) | Narrative-section → contract links remain candidates |
| Linking contracts / suppliers to document evidence | Contract links via plans; supplier identity in documents not resolved (D-14b display rules) | Supplier-level evidence largely unavailable in public reports |
| Long-document parsing | PADs and ICRs (median 77 / 58 pages); some PADs > 100 pages expected (page counts measured after download) | Confirm ≥ 10 PADs over 100 pages after download |
| Section-aware chunking | PAD / ISR / ICR / ICRR templates across 2008–2026 | Multilingual templates (Tier 2) |
| Tables and annexes | Plans (structured tables), PAD and ICR annexes, results frameworks | Borderless tables need tool evaluation |
| Embeddings / knowledge representation | Designed in the Module 1 report | Not built (by restriction) |

## 10. Decisions needed

1. Approve the **70-project manifest** (or ask for swaps).
2. Approve **Tier 1, 362 new PDFs** (≈ 160–400 MB), or the minimal 298, or Tier 1 + Tier 2 (491).
3. Approve downloading the **first batch of 10** for manual inspection before the rest.
4. Choose the download location (local `rag/` folder vs a Databricks Volume) and the logging and checksum convention (existing `wbdisc` client and manifest pattern).
