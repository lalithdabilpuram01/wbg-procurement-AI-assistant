# RAG scope v3: 5 countries, 35 projects (planning only)

2026-10-25.

**What was done:**
- Reused cached data only: WDS metadata, the 4,421 procurement-plan texts fetched earlier and the existing manifests. Nothing was refetched.
- Ran read-only Databricks queries against Silver contract tables.
- Made 48 header-only (HEAD) availability checks on plan PDFs newly added to the download list. No PDF content was fetched.

**What was not done:** no PDFs downloaded, no Databricks or manifest changes, no tables, embeddings or parsing pipeline. The 2,000-project cohort is unchanged.

## Summary

| Item | Value |
|---|---|
| Countries | Sri Lanka, Ethiopia, Senegal, Indonesia, Argentina |
| Projects | 35 (7 per country; 25 closed, 10 active), exactly as approved |
| Recommended PDFs | **237**: PAD 35, ISR 70, ICR 25, ICRR 22, procurement plans 85 (35 latest + 50 coverage versions) |
| Already local | 6 (all procurement plans) |
| **New downloads** | **231 PDFs, about 90.6 MB estimated** |
| Plan versions available for the 35 | 2,164 listed; 2,111 with text; 53 without text |
| Contract denominators | 5,103 DS00005 contracts. 4,214 of these carry a STEP plan-package reference; 889 do not. A further 143 legacy DS01004 contracts are reported separately. |
| **Reference coverage with the v3 plan choice** | 3,486 of 4,214 STEP-reference contracts (82.7%) have row-level reference evidence. 554 contracts are amount-verified, plus 354 corroborated by an exact actual-date match. |
| Same, with the v2 plan choice (latest + 8 earliest) | 2,714 of 4,214 (64.4%); 431 amount-verified |
| Same, with all 2,111 texts (upper bound) | 3,535 of 4,214 (83.9%); 572 amount-verified |

**Decisive finding:** adding 50 selected older plan versions to the 35 latest plans raises row-level reference coverage from 64% to 83% of STEP-reference contracts. That is within 1.2 points of what all 2,111 versions give. A further 41 versions would add only 49 contracts with row-level evidence (and 84 date- or amount-verified contracts, mostly by exact date), so they are not recommended.

**Text evidence is not proof of a page-level link.** Every link here comes from the World Bank's text version of a plan. Page numbers are taken from page breaks in the text and must be confirmed on the PDF.

## 1. Final 35-project selection

`PROJECT_SELECTION_35.csv` is the v2 project manifest filtered to the 35 approved IDs, with all v2 attributes. No project, country or attribute was changed.

## 2. Procurement-plan version coverage

**Method** (`m16_plan_version_coverage.py` run with `--projects <35>`; `plan_text_rows.py`):

1. Parse each cached plan text into activity rows, anchored on the STEP package pattern (`-NNNNNN-(CW|GO|CS|NC|TA)-METHOD`).
2. For each row, keep:
   - the printed reference;
   - the section (category);
   - review type and method label;
   - status;
   - estimated and actual amounts;
   - "Signed Contract" planned and actual dates;
   - the line number and text page.
3. **Text forms:**
   - *Layout* (1,994 texts): columns are kept, so dates are assigned to stages by column position.
   - *Flow* (50 texts): one cell per line, so dates cannot be assigned to stages and are never used as evidence.
   - *No STEP table* (67 texts): older free-form plans.
4. **Link key:** same `project_id`, and the contract's STEP package reference (normalised, with any lot or sequence suffix removed) equals the reference printed in the row. A shared project ID alone never creates a link.

**Match classes (contracts, best class in the v3 versions):**
- exact (printed string equals the DS00005 reference): 1,191;
- normalised (differs only in spaces, case or separators): 2,120;
- lot or sequence suffix (e.g. `…-GO-RFQ-1` against the plan's `…-GO-RFQ`): 175.

**Cardinality:** a plan row tied to one contract is kept separate from one activity awarded as several contracts. Lot links are verified only when the plan's actual amount equals the sum of the lot contracts.

**Evidence tiers.** Independent fields only: the plan reference already encodes category and method, so those count as consistency checks, not proof.

| Tier | Rule | DS00005 contracts (v3 versions) |
|---|---|---|
| verified_amount | Plan "Actual Amount (US$)" > 0 equals the DS00005 amount (or the lot sum), within max(US$1, 0.5%); no category or review-type conflict | **554** |
| verified_exact_date | Layout text only: plan "Signed Contract – Actual" is exactly the DS00005 signing date; plan actual amount 0 or blank; no conflict. A difference of 1–7 days is **not** accepted. Reported separately so a stricter definition can exclude it. | 354 |
| candidate | Reference match with no conflict and no independent corroboration. Differing dates are kept, not reconciled. | 2,440 |
| conflicting | Plan actual amount differs, or a category or review-type conflict. Both values are kept. | 119 |
| ambiguous | Same package on several rows of one version with different values | 19 |
| text_reference_only | A non-STEP reference string found in the text; no row-level evidence (candidate only) | 408 (+2 legacy) |
| no evidence | Not found in any v3 plan version | 1,209 |

**Dates are kept separate:**
- *planned* (plan "Signed Contract – Planned");
- *actual* (plan "Signed Contract – Actual");
- *recorded* (DS00005 `contract_signing_date` and `source_as_of_date`).

The date difference is computed only between actual and recorded.

**Parser validation:** checked against independently parsed PDF tables of the local plans. Agreement was 122 of 122 actual amounts, 78 of 79 signed-actual dates and 122 of 122 review types. The text parser recovered 122 of the 123 table rows, plus 146 rows the PDF-table parser missed.

**Data-quality findings:**
- 26 plan "versions" listed under these projects in WDS are compilations of other projects' plans. One listed under P159929 holds 40,433 rows from India, Mongolia, Papua New Guinea and others. They are never selected.
- Only activities whose reference starts with the project's own country prefix are counted: 19,624 own-project activity references, 2,580 of them matched to contracts.

**Version selection** (`m18_scope_v3_35_projects.py`):
1. Start with the latest plan.
2. Greedily add the version with the largest new coverage, but only while it is *material*. A version is material if it adds at least max(3, 5% of the project's STEP-reference contracts) contracts with new row-level evidence, or at least 3 newly verified contracts, or at least that many text-only references in legacy-format projects.

Every step and its marginal gain are in `PLAN_VERSION_SELECTION_STEPS_35.csv`. Which contracts each selected version covers is in `CONTRACT_COVERAGE_BY_DOCUMENT_35.csv`.

Per project (DS00005 contracts; "row-level" = verified + candidate + conflicting + ambiguous):

| Ctry | Project | Status | DS00005 contracts | STEP-ref | Plan versions (listed / text) | v2: versions, row-level, verified | **v3: versions** | **v3 row-level (% of STEP-ref)** | v3 verified amount + exact date | v3 text-only | All texts: row-level |
|---|---|---|---|---|---|---|---|---|---|---|---|
| LK | P132833 | Closed | 89 | 89 | 58 / 58 | 2, 58, 11 | **2** | **66 (74%)** | 5 + 6 | 0 | 67 |
| LK | P146314 | Closed | 66 | 64 | 35 / 35 | 1, 49, 8 | **1** | **49 (77%)** | 8 + 0 | 3 | 50 |
| LK | P156019 | Closed | 1354 | 1352 | 103 / 103 | 2, 940, 61 | **3** | **1090 (81%)** | 77 + 0 | 0 | 1102 |
| LK | P159303 | Closed | 11 | 11 | 44 / 43 | 1, 8, 8 | **1** | **8 (73%)** | 8 + 0 | 0 | 11 |
| LK | P163714 | Active | 14 | 14 | 29 / 26 | 1, 0, 0 | **2** | **14 (100%)** | 12 + 0 | 0 | 14 |
| LK | P163721 | Closed | 64 | 64 | 35 / 33 | 1, 64, 18 | **2** | **64 (100%)** | 34 + 0 | 0 | 64 |
| LK | P178432 | Active | 55 | 55 | 32 / 32 | 1, 19, 0 | **3** | **55 (100%)** | 0 + 10 | 0 | 55 |
| ET | P123531 | Closed | 72 | 50 | 32 / 32 | 1, 50, 38 | **1** | **50 (100%)** | 38 + 0 | 22 | 50 |
| ET | P143302 | Closed | 45 | 45 | 77 / 75 | 1, 44, 28 | **1** | **44 (98%)** | 26 + 2 | 1 | 44 |
| ET | P148850 | Closed | 43 | 31 | 65 / 65 | 2, 25, 14 | **2** | **31 (100%)** | 8 + 10 | 12 | 31 |
| ET | P163452 | Closed | 66 | 66 | 60 / 58 | 1, 66, 55 | **1** | **66 (100%)** | 55 + 0 | 0 | 66 |
| ET | P164336 | Closed | 719 | 318 | 258 / 246 | 2, 0, 0 | **12** | **217 (68%)** | 58 + 49 | 204 | 217 |
| ET | P176731 | Active | 11 | 11 | 21 / 21 | 1, 1, 0 | **2** | **11 (100%)** | 0 + 7 | 0 | 11 |
| ET | P177233 | Active | 312 | 312 | 79 / 78 | 1, 275, 1 | **5** | **312 (100%)** | 1 + 89 | 0 | 312 |
| SN | P122841 | Closed | 25 | 2 | 24 / 24 | 1, 2, 0 | **1** | **2 (100%)** | 0 + 0 | 10 | 2 |
| SN | P124018 | Closed | 36 | 6 | 31 / 29 | 1, 6, 6 | **1** | **6 (100%)** | 6 + 0 | 1 | 6 |
| SN | P125565 | Closed | 91 | 0 | 82 / 81 | 1, 0, 0 | **1** | **0 (–)** | 0 + 0 | 52 | 0 |
| SN | P156186 | Active | 154 | 150 | 80 / 79 | 2, 126, 68 | **1** | **126 (84%)** | 68 + 0 | 0 | 131 |
| SN | P162042 | Closed | 145 | 94 | 65 / 58 | 1, 85, 19 | **1** | **85 (90%)** | 17 + 2 | 4 | 87 |
| SN | P167681 | Closed | 47 | 47 | 45 / 45 | 1, 47, 33 | **1** | **47 (100%)** | 32 + 1 | 0 | 47 |
| SN | P175325 | Active | 94 | 93 | 57 / 54 | 2, 87, 0 | **5** | **93 (100%)** | 0 + 51 | 1 | 93 |
| ID | P096532 | Closed | 139 | 0 | 124 / 124 | 1, 0, 0 | **9** | **0 (–)** | 0 + 0 | 99 | 0 |
| ID | P144269 | Closed | 61 | 61 | 25 / 25 | 1, 2, 0 | **1** | **2 (3%)** | 0 + 0 | 0 | 2 |
| ID | P157599 | Closed | 137 | 97 | 120 / 120 | 1, 3, 1 | **3** | **14 (14%)** | 1 + 0 | 0 | 27 |
| ID | P165543 | Closed | 271 | 257 | 90 / 84 | 2, 1, 0 | **3** | **229 (89%)** | 8 + 16 | 0 | 236 |
| ID | P168076 | Closed | 268 | 242 | 68 / 66 | 1, 242, 70 | **2** | **242 (100%)** | 63 + 10 | 0 | 242 |
| ID | P169548 | Active | 35 | 35 | 20 / 20 | 1, 35, 0 | **2** | **35 (100%)** | 0 + 26 | 0 | 35 |
| ID | P180811 | Active | 84 | 74 | 132 / 131 | 1, 43, 0 | **3** | **73 (99%)** | 0 + 0 | 0 | 74 |
| AR | P106685 | Closed | 10 | 10 | 26 / 25 | 1, 10, 5 | **1** | **10 (100%)** | 3 + 2 | 0 | 10 |
| AR | P125151 | Closed | 47 | 44 | 40 / 39 | 1, 6, 2 | **2** | **14 (32%)** | 4 + 1 | 0 | 16 |
| AR | P133288 | Closed | 123 | 110 | 66 / 64 | 1, 91, 7 | **2** | **91 (83%)** | 14 + 0 | 0 | 91 |
| AR | P159929 | Closed | 36 | 36 | 48 / 46 | 1, 17, 5 | **2** | **28 (78%)** | 5 + 15 | 0 | 28 |
| AR | P170329 | Closed | 81 | 76 | 24 / 24 | 1, 14, 3 | **1** | **14 (18%)** | 3 + 0 | 1 | 16 |
| AR | P175669 | Active | 127 | 127 | 44 / 43 | 1, 127, 0 | **3** | **127 (100%)** | 0 + 46 | 0 | 127 |
| AR | P179534 | Active | 171 | 171 | 25 / 25 | 2, 171, 0 | **2** | **171 (100%)** | 0 + 11 | 0 | 171 |
| **All** | 35 | | **5103** | **4214** | 2164 / 2111 | 43, 2714, 461 | **85** | **3486 (82.7%)** | 554 + 354 | 410 | 3535 |

Text-only counts include 2 legacy DS01004 contracts (P096532).

## 3. Revised PDF inventory and download count

`DOCUMENT_DOWNLOAD_MANIFEST_v3_35.csv`:
- *Kept unchanged from v2:* the v2 document rows for the 35 projects (PAD, ISR, ICR, ICRR) and the latest plan.
- *Dropped:* 8 earliest plans that added no material coverage.
- *Added:* 50 plan versions, each with its selection reason and marginal gain.

Every row has the official document ID, GUID, PDF URL, text URL, document and disclosure dates, version type, report number, language, HEAD status, size or size estimate, local flag and an expected local path: `rag/corpus_v3/pdf/<country>/<project>/<TYPE>_<date>_<document_id>.pdf`.

| Type | Selected | Already local | New |
|---|---|---|---|
| PAD (original, English) | 35 | 0 | 35 |
| ISR (first + latest) | 70 | 0 | 70 |
| ICR | 25 | 0 | 25 |
| ICR Review | 22 | 0 | 22 |
| Procurement plan | 85 | 6 | 79 |
| **Total** | **237** | **6** | **231** |

**Estimated size of the new files: 90.6 MB (about ±30%).**
- 7 files report an exact Content-Length.
- The rest use the median size of local PDFs of the same type; the plan median is 164 KB.
- Plan sizes vary widely: plan texts range from 16 KB to over 90 MB, so a few very large plans could raise the total.

**Compared with v2 for the same 35 projects:** 195 documents became 237 (+50 plan versions, −8 earliest plans).

## 4. Contract-to-document linking potential

Coverage is measured on 4,214 STEP-reference DS00005 contracts:

| | Row-level reference evidence | Amount-verified | + exact actual-date | Plan versions |
|---|---|---|---|---|
| v2 choice | 2,714 (64.4%) | 431 (10.2%) | 461 (10.9%) | 43 |
| **v3 choice** | **3,486 (82.7%)** | **554 (13.1%)** | **908 (21.5%)** | **85** |
| All available texts | 3,535 (83.9%) | 572 (13.6%) | 992 (23.5%) | 126 needed for the maximum |

Of the 889 non-STEP DS00005 contracts, 408 (45.9%) appear as text-only references. All 5,103 DS00005 contracts: v3 gives some plan-text evidence for 3,894 (76.3%).

**Why the strict verified share is low:** many STEP exports print "Actual Amount" as 0.00, even for signed contracts. Amount verification is impossible for those rows, so they stay candidates unless the exact actual date matches. These candidates are expected to remain candidates after PDF parsing unless another independent field is added, for example award-notice links, which exist for some projects in Silver.

**Strongest projects for Module 1 demonstrations:**
- *Many amount- or date-verified contracts:* P163452, P163721, P177233, P156019, P156186, P168076, P175325, P123531, P175669.
- *Lot-level examples:* P164336, P132833, P165543.

**Weakest:**
- *Plans exist but few rows match:* P144269 (2 of 61), P157599 (14 of 97; 27 at most), P170329 (14 of 76), P125151 (14 of 44).
- *Legacy references only:* P096532, P125565, P122841.

Examples of each tier are in `EVIDENCE_EXAMPLES_35.csv`. All of the following come from plan text, with the text page:

| Tier | Example |
|---|---|
| Verified, amount, exact, 1:1 | P132833 contract 1674110 `LK-RDA-204059-CW-RFQ`, plan 34172790 p.3: actual US$17,652.71 equals DS00005. Planned 2020-12-14, actual 2020-12-29, recorded 2020-12-24; the 5-day actual-versus-recorded difference is kept. |
| Verified, amount, normalised | P156019 contract 1605573 `LK-MOA-PMU-105835-GO-RFQ`, plan 34410985 p.127: US$41,998.02 |
| Verified, amount, lot sum | P164336 `ET-SNNPR-LLRP-216406-CW-RFB`, plan 40034572 p.3: actual US$476,943.84 equals contracts 1679183 (US$297,492.83) and 1679178 (`…-RFB1`, US$179,451.01) |
| Verified, lot sum (earlier conflict resolved) | P132833 `LK-RDA-106767-GO-RFQ`, plan 34172790 p.3: US$5,010.81 equals 1698692 (US$159.46) + 1698690 (`…-RFQ-1`, US$4,851.35). The previous audit had flagged this as an amount conflict. |
| Exact date only (separate tier) | P132833 contract 1744126 `LK-RDA-238233-NC-DIR`, plan 34172790 p.5: plan actual amount 0; plan actual date equals DS00005 date (2022-03-03) |
| Candidate | P132833 contract 1742613 `LK-RDA-41143-GO-RFB`: plan amount 0; plan actual 2022-07-02 against recorded 2022-06-30. A 2-day difference is not taken as proof. |
| Candidate, lots | P132833 `LK-RDA-41147-GO-RFQ`: one plan row for 8 contracts signed 2019-11-01 to 2020-02-11; plan actual 2020-02-18; amount 0 |
| Conflicting | P132833 contract 1674232 `LK-RDA-161704-GO-RFQ`: plan US$52,957.59 against DS00005 US$1,436.41. P132833 contract 1745414 (plan 33761744 p.4): plan 1.44 against US$1,161,767.28, likely a units or extraction issue. Both are kept unreconciled. |
| Conflicting, suffix | P148850 contract 1696116 `ET-RSIDP-285543-GO-UN-DIR`: plan US$1,926,970 against US$1,994,564 |
| Text-only, legacy | P096532 contract 1645097 `PMJ - CS - 12` in plan 32765763. No row-level evidence; candidate only. |

## 5. First 10 PDFs for inspection (`FIRST_BATCH_10_v3.csv`)

All 10 are new downloads that returned HTTP 200, from Sri Lanka, Ethiopia and Senegal. They cover:
- PADs (procurement sections and annexes);
- first and latest ISRs;
- an ICR and ICR Review pair;
- an ICR with heavy annex tables;
- historical plan versions with identifiable contract references, including a French-label plan with 33 verified rows and a plan holding the two P132833 amount conflicts.

The P132833 latest plan (34172790), with 11 verified contracts, is already local and can be inspected alongside these without downloading.

| # | Ctry | Project | WDS ID | Type / role | Date | HEAD | Verified contracts in text | Purpose | Expected local path |
|---|---|---|---|---|---|---|---|---|---|
| 1 | LK | P132833 | 26256705 | PAD / PAD_original | 2016-04-18 | 200 | – | LK closed transport project: PAD procurement arrangements and annexes | `rag/corpus_v3/pdf/LK/P132833/PAD_2016-04-18_26256705.pdf` |
| 2 | LK | P132833 | 34345370 | ICR / ICR | 2024-06-19 | 200 | – | LK completion report: results and procurement lessons | `rag/corpus_v3/pdf/LK/P132833/ICR_2024-06-19_34345370.pdf` |
| 3 | LK | P132833 | 34407877 | ICRR / ICRR | 2024-10-18 | 200 | – | LK IEG review: compare ratings with ICR and latest ISR | `rag/corpus_v3/pdf/LK/P132833/ICRR_2024-10-18_34407877.pdf` |
| 4 | LK | P132833 | 33761744 | PP / PP_coverage | 2022-03-17 | 200 | 0 | LK historical plan version with the most verified contract rows | `rag/corpus_v3/pdf/LK/P132833/PP_2022-03-17_33761744.pdf` |
| 5 | ET | P148850 | 24440102 | PAD / PAD_original | 2015-04-27 | 200 | – | ET expressway PAD (closed, no ICR in WDS) | `rag/corpus_v3/pdf/ET/P148850/PAD_2015-04-27_24440102.pdf` |
| 6 | ET | P148850 | 40089196 | ISR / ISR_latest | 2026-02-28 | 200 | – | ET latest ISR: final ratings without an ICR | `rag/corpus_v3/pdf/ET/P148850/ISR_2026-02-28_40089196.pdf` |
| 7 | ET | P148850 | 40025990 | PP / PP_coverage | 2025-06-27 | 200 | 4 | ET historical plan version; legacy and STEP references side by side | `rag/corpus_v3/pdf/ET/P148850/PP_2025-06-27_40025990.pdf` |
| 8 | ET | P163452 | 34455066 | ICR / ICR | 2025-01-24 | 200 | – | ET urban project ICR: table-heavy annexes | `rag/corpus_v3/pdf/ET/P163452/ICR_2025-01-24_34455066.pdf` |
| 9 | SN | P175325 | 33851641 | ISR / ISR_first | 2022-06-24 | 200 | – | SN first ISR of an active project (French-language plans elsewhere in the project) | `rag/corpus_v3/pdf/SN/P175325/ISR_2022-06-24_33851641.pdf` |
| 10 | SN | P175325 | 40009757 | PP / PP_coverage | 2025-04-29 | 200 | 33 | SN French-label plan version with verified contract rows | `rag/corpus_v3/pdf/SN/P175325/PP_2025-04-29_40009757.pdf` |

The source URLs are in `FIRST_BATCH_10_v3.csv` (`pdf_url`, plus `final_url` after redirect).

## 6. Metadata completeness and known limitations

**Manifest (237 rows):**
- No blank document dates, PDF URLs or GUIDs.
- 2 blank disclosure dates.
- 42 blank version types (ISRs and plans, whose version is held in the ISR sequence or the date).
- 40 blank report numbers.
- 1 blank language: the P125151 ICRR, whose English title confirms the language.

**Known gaps in documents:**
- P148850 is closed with neither an ICR nor an ICRR.
- P164336, P162042 and P170329 are closed without an ICRR.

**Plan texts unavailable:** 53 of the 2,164 versions.
- 20 have no text URL in WDS;
- 30 returned HTTP 403;
- 3 returned HTTP 404.

All are recorded in `plan_coverage/PLAN_VERSION_INVENTORY.csv`. Their PDFs could still exist, but they were not assessed.

**Limits of the text-based evidence:**
- Pages are text-page estimates.
- Flow-form texts carry no stage dates.
- Method labels can be misread from neighbouring columns, so they are flags only.
- A single amount under a two-column header is left unassigned rather than guessed.
- The activity count (19,624) is an upper bound, because wrapped references can create fragments.

**Superseded outputs:** `plan_version_coverage_v1/` holds the earlier 70-project outputs, produced before the amount-column fix. They are kept but superseded by this folder for the 35 projects.

## 7. Remaining Module 1 assessment gaps

1. **Page, table and row confirmation:** every link above must be re-confirmed on the PDF, matching the page, section table and row against the text-derived values.
2. **Corroboration depth:** only 13.1% of STEP-reference contracts can be amount-verified. Adding notice-to-contract links (Silver `notice_contract_links`, which today covers 5 of these projects: P132833, P148850, P159303, P163714 and P175325) or award-notice text would raise independent corroboration. Fetching notice details for the other 30 projects would need approval.
3. **Uncovered contracts:**
   - 728 STEP-reference contracts have no row in any available plan text;
   - 481 non-STEP contracts have no text reference;
   - 3 projects (P096532, P125565, P122841) rely on legacy references.
4. **Structured-data coverage gap:** DS00005 starts on 2019-07-01 and DS01004 covers prior-review contracts only up to 2016-06-30. Plan rows signed between July 2016 and June 2019 have no structured contract.
5. **Narrative documents:** PADs, ISRs and ICRs do not cite contract references, so they support project-level context and ratings only, never contract-specific claims.
6. **Languages:** French (Senegal) and Spanish (Argentina) plan labels are mapped for review type and status. Method-label mapping is partial and is not used as evidence.

## 8. Files

**Report and manifests,** all in `procurement-intelligence/rag/module1_discovery/rag_scope_v3_35_projects/`:
- `RAG_SCOPE_v3_35_PROJECTS_REPORT.md`: this report
- `PROJECT_SELECTION_35.csv`
- `DOCUMENT_DOWNLOAD_MANIFEST_v3_35.csv`: 237 documents
- `FIRST_BATCH_10_v3.csv`
- `PLAN_COVERAGE_BY_PROJECT_35.csv`
- `PLAN_VERSION_SELECTION_STEPS_35.csv`
- `CONTRACT_COVERAGE_BY_DOCUMENT_35.csv`
- `EVIDENCE_EXAMPLES_35.csv`
- `scope_v3_summary.json`

**Underlying evidence,** in `plan_coverage/`:
- `CONTRACT_PLAN_EVIDENCE_LINKS.csv`: every contract-to-version-to-row link with all compared values and provenance
- `PLAN_VERSION_INVENTORY.csv`
- `PLAN_ACTIVITIES.csv`
- `VERSION_SELECTION_MARGINAL_GAINS.csv`
- `PROJECT_PLAN_COVERAGE_35.csv`
- `plan_version_coverage_summary.json`

**Cached plan texts (not modified):** `rag/module1_discovery/plan_version_coverage_v1/plan_text/` and `fetch_log.jsonl`.

**Scripts,** in `rag/module1_discovery/`: `m15_fetch_plan_texts.py` (already run; not rerun), `plan_text_rows.py`, `m16_plan_version_coverage.py`, `m18_scope_v3_35_projects.py`.

**Prior manifests,** unchanged: `selection_70/`, `selection_v2_10_countries/`, `entity_linking_audit_v1/`.

## Decisions needed

1. Approve the 237-document manifest (231 new PDFs, about 91 MB).
2. Decide whether `verified_exact_date` counts as verified, or stays a separate corroborated tier (recommended).
3. Approve downloading the first batch of 10 for manual inspection.
