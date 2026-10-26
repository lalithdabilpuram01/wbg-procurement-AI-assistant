# Contract-to-document entity linking: feasibility audit (70 projects, 10 countries)

2026-10-24. Read-only. No PDFs were downloaded and no Databricks object was changed. No tables, embeddings or pipeline were created, and the 70-project manifest is unchanged.

**Sources:**
- Silver `contract_awards` (DS00005), `contract_awards_legacy` (DS01004), `notice_contract_links`, `notice_contract_link_candidates` and `procurement_notice_details`, queried read-only;
- table rows parsed earlier from local plan PDFs (`discovery/profiling/procurement_plan_activities.csv`);
- the 4 local latest-plan PDFs;
- the World Bank's own text files for the 32 P178432 plan versions downloaded in the earlier deep dive;
- WDS metadata for the 70 projects.

**Script:** `rag/module1_discovery/m14_entity_linking_feasibility.py`.

## Summary

- **The "only 4 projects" figure measured what had been parsed, not what can be linked.** Only 4 of the 70 projects had a procurement plan parsed. Re-examining those 4 plans gives **87 verified contract links**: 51 one-to-one, plus 36 lot contracts tied to a single plan activity. The earlier figures counted plan references, not contracts.
- **A fifth project already has local evidence.** For P178432 (Sri Lanka public financial management), **all 55 of its contracts** have their reference printed in at least one of the 32 local plan versions. The latest version alone references only 19 of them.
- **The other 65 projects are not "no evidence".** Their contracts carry plan-style references (8,061 of 9,454 contracts across the 70 projects), but plans have not been parsed. Potential across all 70:
  - verified: 4;
  - high: 56;
  - medium: 4;
  - unknown: 6 (non-STEP references, so the format can only be judged from the plans).

  No project is classed as having no evidence.
- **The biggest coverage risk is which plan versions are downloaded, not which projects were selected.** Latest plans drop many completed or earlier activities. The current manifest takes the latest plan for every project and the earliest for only 14.
- **Sri Lanka's 5-day difference is not a conflict.** Both dates are actual signing dates, and the amount matches exactly. Across 55 one-to-one pairs with both dates, the plan's date is never earlier than the contract dataset's (41 same day, 14 later by 2–15 days), which is consistent with a recording lag.
- **Recommendation:** keep the 70 projects. No swap is needed. One optional Indonesian swap is described in §6.

## 1. Confirmed contract-level matches and their evidence

**How a plan row is matched to a contract:**
- Same `project_id`.
- The plan reference is taken from the "Activity Reference No. / Description" cell (whitespace removed, upper-cased).
- It is compared with DS00005 `borrower_contract_reference` using `wbdisc.normalize.normalize_ref`. That function applies NFKC, upper-cases, unifies `- _ / \ .` separators and removes whitespace.
- Of 99 matched pairs, 94 are also equal as raw strings. The 5 others needed normalisation, or matched on the package reference plus a lot or sequence suffix (3 pairs).

**Corroboration:**
- *Consistency checks:* category (plan section) and method and review type (French plan labels mapped to English).
- *Independent evidence:* the amount (within 0.5%), or the plan's actual signing date within 7 days of DS00005's. The reference itself encodes the category and method, so only amount and date count as independent.
- *Verified* means a reference match, at least one independent corroborator and no contradiction.

| Project | Plan (WDS ID, date) | Pages with parsed rows / PDF pages | Plan rows | Contracts | Verified 1:1 | Verified lots | Candidate | Conflicting | Contracts whose reference appears in the PDF text |
|---|---|---|---|---|---|---|---|---|---|
| P132833 | 34172790, 2023-10-02 | 3 / 6 | 44 | 89 | 8 | 14 | 7 | 2 | 58 of 89 |
| P148850 | 40102923, 2026-05-14 | 3 / 7 | 53 | 43 | 11 | 4 | 0 | 1 | 25 of 31 |
| P175325 | 40127897, 2026-09-17 | 3 / 10 | 44 | 94 | 18 | 8 | 2 | 0 | 87 of 93 |
| P500392 | 40134065, 2026-10-19 | 2 / 4 | 25 | 24 | 14 | 10 | 0 | 0 | 24 of 24 |
| **Total** | | | 166 | 250 | **51** | **36** | 9 | 3 | 194 of 237 |

Example, verified: plan **34172790**, page 3, row 2, section WORKS, `LK-RDA-204059-CW-RFQ`, matched to contract **1674110**.

| Field | Plan | DS00005 |
|---|---|---|
| Method | RFQ | RFQ |
| Review type | Post | Post |
| Amount | US$17,652.71 | US$17,652.71 |
| Signing date | 2020-12-29 | 2020-12-24 |

A verified notice-to-contract link also exists: contract award notice OP00179514, published 2022-05-13.

**Conflicting (3, amount differs):**
- 1674232: plan US$52,957.59 against DS00005 US$1,436.41.
- 1674300: US$951,489 against US$995,986.
- 1696116 (P148850): US$1.93m against US$1.99m. This one was matched as `…-GO-UN` plus suffix `-DIR`.

These stay as candidates with both values recorded.

**Normalised legacy references:** two pre-STEP plan rows in P148850 match contracts after normalisation:
- `MOT/RSDS/NSR/13/09` → 1870877, a row marked "Terminated" in the plan;
- `MOT/RSIDP/SR/03/18` → 1617349.

Both are candidates, not yet corroborated.

**Plan rows with no contract:** of 166 parsed rows, 72 matched, 18 carry legacy references (2 matched after normalisation, above) and 76 STEP rows have no contract.
- 74 are rows where no contract is expected: cancelled or "annulé" (48), under implementation (15), pending (10) or under review (1).
- 2 are signed rows in P132833 with no DS00005 contract: `LK-RDA-47056-NC-RFQ` and `LK-RDA-278916-CS-CDS`.

Row-level evidence is in `PLAN_ROW_CONTRACT_MATCHES_4_PROJECTS.csv` (plan ID, page, row on page, section, both sets of values, status) and `PLAN_ROWS_UNMATCHED_4_PROJECTS.csv`.

## 2. Why only 4 projects have confirmed matches

1. **Parsing coverage.** Only these 4 of the 70 had a plan PDF parsed. They were the latest plans in the Phase 1 sample of 40, and no plan of the other 66 has been parsed. P178432's 32 plan versions were already local but had never been probed.
2. **Table extraction misses rows.** In the same PDFs, the reference of a further 27 contracts (P132833), 9 (P148850) and 59 (P175325) appears in the page text but not in the parsed rows. The plan text holds up to 3 times as many matchable references as the parsed rows.
3. **Only the latest plan version was examined.** Latest STEP exports omit many earlier or completed activities:
   - P178432: 36 of 55 contracts appear only in earlier versions.
   - P132833: 31 contracts are absent from the latest plan's text, all signed before that plan's date.
4. **Structured contract coverage has a gap.** DS00005 covers contracts signed from 2019-07-01 onward. The legacy DS01004 covers prior-review contracts only, up to 2016-06-30. Contracts signed between July 2016 and June 2019 are in neither, so early plan versions list activities with no structured contract to link to.
5. **Reference formats vary.** There are lot and sequence suffixes (`…-GO-RFQ-LOT1`, `…-CS-CQS-12`), programme prefixes (`CERC-LK-…`), legacy formats (`MOT/RSDS/SR/02/09`, `05-TVAUX-PASE-2017`) and French plan labels. The strict STEP pattern used in the earlier probe and in country scoring missed 615 contracts that do contain a STEP package reference.
6. **The earlier probe's metric.** It counted plan references and kept one contract per reference, so lots were collapsed: 203 references matched, but contracts were never counted.

## 3. Matching potential across the other 66 projects

Potential classes:
- **High:** at least 10 contracts, and at least 60% of the project's contracts, carry a STEP package reference. This is the format plan rows carry, so plan rows should match by reference once parsed.
- **Medium:** 3 or more such contracts.
- **Unknown:** fewer than 3. These projects use pre-STEP or agency-specific references (for example P180263 `MOH-WB-HSERP-AF-G15`, P125565 `05-TVAUX-PASE-2017`, P096532 `BAP-CS-10`). Their plans may use the same strings, but that can only be judged by parsing.

Counts (including the 4 verified):

| Country | Verified | High | Medium | Unknown |
|---|---|---|---|---|
| AR | 0 | 7 | 0 | 0 |
| BF | 0 | 7 | 0 | 0 |
| ET | 1 | 5 | 1 | 0 |
| ID | 0 | 6 | 0 | 1 |
| KE | 0 | 4 | 2 | 1 |
| LK | 1 | 6 | 0 | 0 |
| MD | 0 | 6 | 0 | 1 |
| PK | 0 | 7 | 0 | 0 |
| PS | 1 | 5 | 0 | 1 |
| SN | 1 | 3 | 1 | 2 |
| **All 70** | **4** | **56** | **4** | **6** |

**Further evidence routes and limits:**
- *Verified notice-to-contract links* already exist in Silver for 9 of the 70 projects: P163980 101, P175325 55, P132833 53, P148850 22, P150357 12, P500392 10, P121019 9, P163714 6, P159303 3. Notice details were fetched only for the 184-project cluster.
  - Kenya P163980 has only 15% STEP references, yet 101 contracts link to notices by reference. A non-STEP format does not mean a reference cannot be linked.
- *Lots:* in 4 projects more than half the contracts share a reference: P179534, P177233, P175669, P180707. Links there will be mostly at activity level (`verified_one_to_many`).
- *After the latest plan:* 181 contracts across the 70 were signed after the project's latest plan date, so no available plan can list them.
- *Legacy contracts:* 326 legacy prior-review contracts (18 projects) predate DS00005. They can only be linked to early plan versions.

## 4. Entity-resolution strategies compared

| # | Method | Required fields | Reliability | False-match risks | Validation | Can produce |
|---|---|---|---|---|---|---|
| 1 | Exact reference | project_id, plan reference, borrower reference | High precision, lower recall (94 of 99 pairs equal as raw strings) | Low; reused references across lots | Independent corroborator (amount or actual date) | Verified, with corroboration |
| 2 | Normalised reference (`normalize_ref`) | as 1 | High; recovers spacing, case and separator variants | Low within a project; short legacy references (e.g. `C-03/2019/ADM`) can collide | As 1; check uniqueness on both sides | Verified, with corroboration; legacy formats are candidates until checked |
| 3 | Project ID + package reference (core + lot/sequence suffix) | project_id, STEP core, suffix | High at activity level | One plan row covers several lot contracts; amounts not separable | Date range of the lots against the plan's actual date; sum of lot amounts if the plan states a total | `verified_one_to_many` (activity level) or candidate |
| 4 | Project ID + category + description + dates | category, method, cleaned description, planned/actual dates, amount | Medium–low; many near-identical rows (consultants, RFQ goods) | High for repetitive activities and same-day signings | Manual review; at least 3 agreeing attributes and a unique best candidate | Candidate only |
| 5 | Text similarity | description text from plan and contract | Low as evidence; useful to find candidates | Semantic near-duplicates across packages | Must be confirmed by method 1–3 or manually | Candidate only |

A shared project ID alone never creates a link. Narrative documents (PAD and ISRs) carry no contract references: 0 found in the 8 local P178432 PAD and ISR texts, matching the earlier probe. They link at project level only.

**Recommended strategy:**
1. Run methods 2 and 3 on parsed plan rows, keeping the raw string and noting whether method 1 also holds.
2. Promote a match to `verified` only if all of these hold:
   - a unique row-to-contract pair, or a lot set (`verified_one_to_many`);
   - at least one independent corroborator (amount within 0.5%, or plan actual signing date 0–7 days from DS00005);
   - no contradiction in category, method or review type after language mapping.
3. Everything else is a `candidate`, `ambiguous` or `conflicting` record with its evidence kept.
4. A reference found in PDF text but not in a parsed row is `text_reference_only`, with the page recorded.
5. Use methods 4 and 5 only to generate candidates, for example for the 6 unknown projects.

Matching should run across every available plan version, not only the latest.

## 5. Evidence-link metadata schema (proposal)

Full field list: `EVIDENCE_LINK_SCHEMA_PROPOSAL.json`.

**Two stores with identical columns,** mirroring Silver `notice_contract_links` and `notice_contract_link_candidates`:
- `contract_document_links` holds verified links;
- `contract_document_link_candidates` holds candidate, ambiguous, conflicting and text-only links.

**Fields per link:**
- *Identity:* project_id, wb_contract_number (or legacy_contract_number), the borrower reference raw, normalised and as a package core.
- *Document:* document_id, type, version date, disclosure date, language, source URL, file SHA-256.
- *Location:* section_id, table_id, page_number, row_on_page, the reference text as printed, hash of the cited row text.
- *Matching:* match_method, matching_fields, corroborating_fields, independent_corroboration, disagreeing_fields, both sets of compared values, date_difference_days (actual against actual only).
- *Status:* link_cardinality, verification_status, confidence (for methods 4 and 5 only), rule_version, reviewer role, created_at.

## 6. Project substitutions

No swap is needed. All 10 countries already have at least 4 projects that are verified or high potential.

The search looked for same-country, same-sector, same-status replacements for medium or unknown projects that would at least double the reference-bearing contracts. It found only:

| Replace | With | Effect | Cost |
|---|---|---|---|
| ID P096532 (Dam Operational Improvement and Safety, FY2009, 0 STEP-reference contracts, 62 legacy contracts) | P154782 (National Slum Upgrading, FY2017, 82 of 103 STEP-reference contracts, ICR) or P156125 (Urban Water Supply, FY2018, 82 of 90 STEP-reference contracts, ICR) | Moves one project from unknown to high; sector, status and ICR coverage kept | Indonesia loses its only FY≤2014 project and the legacy-format test case, and both alternatives have twice as many contracts to parse |

**Recommendation: no swap now.** The 6 unknown projects are a useful test of legacy-format linking. Reconsider P096532 only if every project in Indonesia must show contract-level links. The one other candidate found (LK P163721 → P173867) would lose ICR coverage and is not recommended.

## 7. Uncertainties that need PDF parsing

1. The actual match rate in the high and medium projects. In the 5 projects with local plans, 65–100% of STEP-reference contracts have their reference in plan text (P132833 65%, P148850 81%, P175325 94%, P500392 100%, P178432 100% across versions).
2. Whether the 6 unknown projects' plans use the same legacy strings as DS00005.
3. Which plan versions are needed per project. For P178432, 2 of its 32 versions (2026-08-21 and 2026-04-24) cover all 55 contracts, while its latest plus earliest versions cover only 20. The latest plan plus 14 earliest plans in the current manifest will miss completed activities.
4. Table extraction quality. The existing parser recovered between a third and two thirds of the contract references present in the page text, and splits words ("Quota tions").
5. Plan amounts are often 0 or blank (all P175325 rows). Verification then rests on the actual signing date.
6. Spanish (Argentina) and French (Senegal, Burkina Faso) plan labels need mapping tables. The French one was validated on P175325.
7. Why the amounts conflict for 3 contracts, and the 2 signed plan rows with no DS00005 contract.

## 8. Module 1 alignment

**Requirement:** connect structured procurement records to specific documentary evidence.

**Contract-level evidence comes from procurement plan rows.** Each row provides:
- the planned and actual method;
- the review type;
- planned and actual dates;
- the amount;
- the status (including cancelled, "annulé" and terminated).

Each link cites a document ID, page and row, and is verified against DS00005. This is demonstrated for 87 contracts across 4 projects, and reference-level evidence exists for a fifth (P178432).

**Notice evidence** (`notice_contract_links`) adds award-notice links where notice details exist.

**PAD, ISR, ICR and ICRR** provide project-level context and ratings. They do not cite contract references, so links from them must not be presented as contract-specific.

**Credibility:** a credible Module 1 demonstration is feasible with the current 70 projects, provided plans are parsed across versions.

**Decisions needed (none taken):**
1. Keep the 70 projects as they are (recommended), or swap P096532.
2. Choose how to cover plan versions. Option (a), recommended: fetch the small WDS text versions of all plan versions for the 70 to choose, per project, the minimal set of plan PDFs that covers its contracts. Option (b): add versions by date rules. Either needs network approval; the plan versions available for the 70 total 4,522.
3. Optionally extend notice-detail retrieval (API) to the 61 selected projects not yet covered.
4. Approve the first-batch download as previously planned. P132833's earliest plan in the first batch tests version coverage directly.

## 9. Files

All in `procurement-intelligence/rag/module1_discovery/entity_linking_audit_v1/`:
- `CONTRACT_DOCUMENT_ENTITY_LINKING_FEASIBILITY_REPORT.md`: this report
- `PROJECT_LINKING_POTENTIAL_70.csv`: per-project metrics and potential
- `PLAN_ROW_CONTRACT_MATCHES_4_PROJECTS.csv`, `PLAN_ROWS_UNMATCHED_4_PROJECTS.csv`: row-level evidence
- `SWAP_CANDIDATES_SAME_COUNTRY.csv`
- `EVIDENCE_LINK_SCHEMA_PROPOSAL.json`
- `entity_linking_audit_summary.json`: probe, PDF text scan, P178432 version scan, date case
- `p178432_version_cover.json`: the minimal plan versions covering P178432's contracts

## Appendix: all 70 projects

Column meanings:
- **STEP-ref contracts:** DS00005 contracts containing a STEP package reference.
- **Verified:** one-to-one plus lot contracts, from parsed plan rows.
- **Cand./amb./confl.:** row-level candidate, ambiguous or conflicting contracts.
- **Ref. in local text:** contracts whose reference appears in a local plan's text (candidates).
- **Notice links:** verified notice-to-contract links in Silver.
- **Unassessed:** contracts not yet checked against any document.

| Ctry | Project | Status | FY | Potential | Contracts | STEP-ref contracts | Verified (1:1 + lots) | Cand./amb./confl. | Ref. in local text | Notice links | Unassessed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| AR | P106685 | Closed | 2015 | high | 10 | 10 | 0 (0+0) | 0 | 0 | 0 | 10 |
| AR | P125151 | Closed | 2011 | high | 47 | 44 | 0 (0+0) | 0 | 0 | 0 | 47 |
| AR | P133288 | Closed | 2015 | high | 123 | 110 | 0 (0+0) | 0 | 0 | 0 | 123 |
| AR | P159929 | Closed | 2017 | high | 36 | 36 | 0 (0+0) | 0 | 0 | 0 | 36 |
| AR | P170329 | Closed | 2020 | high | 81 | 76 | 0 (0+0) | 0 | 0 | 0 | 81 |
| AR | P175669 | Active | 2022 | high | 127 | 127 | 0 (0+0) | 0 | 0 | 0 | 127 |
| AR | P179534 | Active | 2024 | high | 171 | 171 | 0 (0+0) | 0 | 0 | 0 | 171 |
| BF | P120517 | Closed | 2012 | high | 93 | 90 | 0 (0+0) | 0 | 0 | 0 | 93 |
| BF | P124015 | Closed | 2014 | high | 215 | 215 | 0 (0+0) | 0 | 0 | 0 | 215 |
| BF | P148062 | Closed | 2015 | high | 426 | 402 | 0 (0+0) | 0 | 0 | 0 | 426 |
| BF | P166785 | Active | 2021 | high | 70 | 70 | 0 (0+0) | 0 | 0 | 0 | 70 |
| BF | P173858 | Closed | 2020 | high | 127 | 127 | 0 (0+0) | 0 | 0 | 0 | 127 |
| BF | P177918 | Active | 2024 | high | 75 | 75 | 0 (0+0) | 0 | 0 | 0 | 75 |
| BF | P178598 | Active | 2023 | high | 92 | 92 | 0 (0+0) | 0 | 0 | 0 | 92 |
| ET | P148850 | Closed | 2015 | verified | 43 | 31 | 15 (11+4) | 1 | 25 | 22 | 18 |
| ET | P123531 | Closed | 2013 | high | 72 | 50 | 0 (0+0) | 0 | 0 | 0 | 72 |
| ET | P143302 | Closed | 2014 | high | 45 | 45 | 0 (0+0) | 0 | 0 | 0 | 45 |
| ET | P163452 | Closed | 2018 | high | 66 | 66 | 0 (0+0) | 0 | 0 | 0 | 66 |
| ET | P176731 | Active | 2024 | high | 11 | 11 | 0 (0+0) | 0 | 0 | 0 | 11 |
| ET | P177233 | Active | 2022 | high | 312 | 312 | 0 (0+0) | 0 | 0 | 0 | 312 |
| ET | P164336 | Closed | 2019 | medium | 719 | 318 | 0 (0+0) | 0 | 0 | 0 | 719 |
| ID | P144269 | Closed | 2016 | high | 61 | 61 | 0 (0+0) | 0 | 0 | 0 | 61 |
| ID | P157599 | Closed | 2018 | high | 137 | 97 | 0 (0+0) | 0 | 0 | 0 | 137 |
| ID | P165543 | Closed | 2019 | high | 271 | 257 | 0 (0+0) | 0 | 0 | 0 | 271 |
| ID | P168076 | Closed | 2019 | high | 268 | 242 | 0 (0+0) | 0 | 0 | 0 | 268 |
| ID | P169548 | Active | 2022 | high | 35 | 35 | 0 (0+0) | 0 | 0 | 0 | 35 |
| ID | P180811 | Active | 2024 | high | 84 | 74 | 0 (0+0) | 0 | 0 | 0 | 84 |
| ID | P096532 | Closed | 2009 | unknown | 139 | 0 | 0 (0+0) | 0 | 0 | 0 | 139 |
| KE | P105269 | Closed | 2013 | high | 15 | 14 | 0 (0+0) | 0 | 0 | 0 | 15 |
| KE | P152394 | Closed | 2016 | high | 971 | 947 | 0 (0+0) | 0 | 0 | 0 | 971 |
| KE | P161305 | Active | 2021 | high | 121 | 111 | 0 (0+0) | 0 | 0 | 0 | 121 |
| KE | P164654 | Closed | 2019 | high | 50 | 41 | 0 (0+0) | 0 | 0 | 0 | 50 |
| KE | P163980 | Active | 2020 | medium | 144 | 21 | 0 (0+0) | 0 | 0 | 101 | 144 |
| KE | P174059 | Closed | 2021 | medium | 14 | 3 | 0 (0+0) | 0 | 0 | 0 | 14 |
| KE | P121019 | Closed | 2013 | unknown | 30 | 2 | 0 (0+0) | 0 | 0 | 9 | 30 |
| LK | P132833 | Closed | 2016 | verified | 89 | 89 | 22 (8+14) | 9 | 58 | 53 | 31 |
| LK | P146314 | Closed | 2014 | high | 66 | 64 | 0 (0+0) | 0 | 0 | 0 | 66 |
| LK | P156019 | Closed | 2016 | high | 1354 | 1352 | 0 (0+0) | 0 | 0 | 0 | 1354 |
| LK | P159303 | Closed | 2017 | high | 11 | 11 | 0 (0+0) | 0 | 0 | 3 | 11 |
| LK | P163714 | Active | 2018 | high | 14 | 14 | 0 (0+0) | 0 | 0 | 6 | 14 |
| LK | P163721 | Closed | 2018 | high | 64 | 64 | 0 (0+0) | 0 | 0 | 0 | 64 |
| LK | P178432 | Active | 2023 | high | 55 | 55 | 0 (0+0) | 0 | 55 | 0 | 0 |
| MD | P118518 | Closed | 2012 | high | 79 | 65 | 0 (0+0) | 0 | 0 | 0 | 79 |
| MD | P127388 | Closed | 2013 | high | 241 | 220 | 0 (0+0) | 0 | 0 | 0 | 241 |
| MD | P144103 | Closed | 2015 | high | 25 | 25 | 0 (0+0) | 0 | 0 | 0 | 25 |
| MD | P173776 | Closed | 2020 | high | 101 | 101 | 0 (0+0) | 0 | 0 | 0 | 101 |
| MD | P177895 | Active | 2022 | high | 96 | 96 | 0 (0+0) | 0 | 0 | 0 | 96 |
| MD | P504278 | Active | 2025 | high | 25 | 25 | 0 (0+0) | 0 | 0 | 0 | 25 |
| MD | P150357 | Closed | 2016 | unknown | 13 | 0 | 0 (0+0) | 0 | 0 | 12 | 13 |
| PK | P125999 | Closed | 2012 | high | 22 | 22 | 0 (0+0) | 0 | 0 | 0 | 22 |
| PK | P151075 | Closed | 2020 | high | 48 | 48 | 0 (0+0) | 0 | 0 | 0 | 48 |
| PK | P154278 | Closed | 2016 | high | 181 | 147 | 0 (0+0) | 0 | 0 | 0 | 181 |
| PK | P159712 | Closed | 2018 | high | 41 | 41 | 0 (0+0) | 0 | 0 | 0 | 41 |
| PK | P166308 | Closed | 2020 | high | 161 | 160 | 0 (0+0) | 0 | 0 | 0 | 161 |
| PK | P180323 | Active | 2023 | high | 131 | 131 | 0 (0+0) | 0 | 0 | 0 | 131 |
| PK | P180707 | Active | 2024 | high | 126 | 126 | 0 (0+0) | 0 | 0 | 0 | 126 |
| PS | P500392 | Active | 2025 | verified | 24 | 24 | 24 (14+10) | 0 | 24 | 10 | 0 |
| PS | P129861 | Closed | 2012 | high | 31 | 30 | 0 (0+0) | 0 | 0 | 0 | 31 |
| PS | P148600 | Closed | 2018 | high | 31 | 31 | 0 (0+0) | 0 | 0 | 0 | 31 |
| PS | P150991 | Closed | 2017 | high | 43 | 43 | 0 (0+0) | 0 | 0 | 0 | 43 |
| PS | P164412 | Closed | 2018 | high | 159 | 159 | 0 (0+0) | 0 | 0 | 0 | 159 |
| PS | P172571 | Active | 2020 | high | 175 | 143 | 0 (0+0) | 0 | 0 | 0 | 175 |
| PS | P180263 | Active | 2023 | unknown | 155 | 0 | 0 (0+0) | 0 | 0 | 0 | 155 |
| SN | P175325 | Active | 2022 | verified | 94 | 93 | 26 (18+8) | 2 | 87 | 55 | 7 |
| SN | P156186 | Active | 2017 | high | 154 | 150 | 0 (0+0) | 0 | 0 | 0 | 154 |
| SN | P162042 | Closed | 2020 | high | 145 | 94 | 0 (0+0) | 0 | 0 | 0 | 145 |
| SN | P167681 | Closed | 2019 | high | 47 | 47 | 0 (0+0) | 0 | 0 | 0 | 47 |
| SN | P124018 | Closed | 2014 | medium | 36 | 6 | 0 (0+0) | 0 | 0 | 0 | 36 |
| SN | P122841 | Closed | 2012 | unknown | 25 | 2 | 0 (0+0) | 0 | 0 | 0 | 25 |
| SN | P125565 | Closed | 2013 | unknown | 91 | 0 | 0 (0+0) | 0 | 0 | 0 | 91 |
| **Total** | 70 | | | | **9454** | **8061** | **87** | **12** | **249** | **271** | **9205** |
