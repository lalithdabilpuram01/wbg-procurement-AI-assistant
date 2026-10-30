# Module 2: ML Data Readiness Report (Phase 1)

**Date:** 2026-10-25 · **Scope:** the approved 2,000-project PoC cohort (D-26) · **Status:** Phase 1 complete; no model trained, no synthetic labels generated.

**How these numbers were produced:** read-only Databricks Connect queries, run on 2026-10-25. Silver and Gold Delta versions were identical before and after (`ml/eda/extract_20261025T065554Z/extract_manifest.json`). Cohort manifest SHA-256: `82a43185…dac940`.

**Labels used in this report:**
- **Measured 2026-10-25:** recomputed in this phase.
- **Historical:** quoted from an earlier report and not re-run.

## 1. Summary

| | Model 1: tender completion time | Model 2: contract amendment | Model 3: re-bidding risk |
|---|---|---|---|
| Task | Regression on ln(days); survival as a check | Binary classification | Binary classification |
| Real outcome labels | **Yes: 2,114 valid labels** (measured), in 145 projects and 1,691 packages | **No.** 0 confirmed positives and 0 confirmed negatives; 56 + 5 unverified candidate events | **No.** 0 confirmed; at most ~160 overlapping, unverified candidate pairs; no negative rule |
| Label source | Real observed dates | **Synthetic (recommended)** | **Synthetic (recommended)**; verify candidates in parallel |
| Ready for Stage 2? | Yes, once the target definition and prediction point are approved | After the synthetic design is approved | After the synthetic design is approved |

## 2. Prediction problems (Task 1)

The prediction points below are **proposals awaiting approval**.

### Model 1: tender completion time

| Item | Definition |
|---|---|
| Business question | How long after a tender is first advertised will the contract be signed? |
| Prediction unit | A contract, reached through its procurement package (project + normalised reference). Lots of one package share a start date, so evaluation groups by package. A package-level variant (first signing per package) is a sensitivity analysis |
| Prediction time (proposed) | **Tender publication:** the date of the first IFB / REOI / prequalification notice. See the trade-offs below |
| Target | `duration_days` = contract signing date − first tender-notice publication date for the package. Modelled as ln(days) |
| Outcome observation date | Contract signing date (DS00005) |
| What it is not | **Not** the bid response window (notice → deadline; median 19 days). **Not** the full procurement lifecycle (planning → contract completion): planning dates are only in procurement plans, and contract completion is not public |
| Label source | Real |
| Use for procurement officers | A realistic signing date with a P10–P90 range when a package is advertised. Flags packages likely to exceed the planned date |
| Limitations | Only advertised methods have labels (Direct Selection 0%, RFQ 2.9%); only the 184 projects with notice references; truncation (§3.4) |

**Prediction point trade-offs:**
- **Planning stage:** most useful to officers, but it needs plan data (estimated amount, market approach, planned dates), which is not in Silver: only 25 cohort plans are parsed, plus the Module 1 pilot. The published bid window would be unavailable.
- **Tender publication (recommended now):** every label and feature is observable. It adds the published bid window, the strongest numeric predictor found (Spearman 0.31).
- **Contract signing:** too late; the outcome is already known.

### Model 2: contract amendment probability

| Item | Definition |
|---|---|
| Business question | Is this newly signed contract likely to be amended (value, time or scope) during implementation? |
| Prediction unit | Contract (`wb_contract_number`, Gold #2) |
| Prediction time (proposed) | Contract signing date |
| Target | `amended_within_24_months` (binary). **Simulated** |
| Outcome observation date | Not observable in public data. In the simulation, signing date + 24 months |
| Features | Known at signing: method, category, review type, country / region, income group at signing FY, project stage, sector. Contract amount only as a declared sensitivity feature (§4) |
| Label source | **Synthetic.** Output is a *simulated amendment probability* |
| Use for procurement officers | Demonstrates how a contract-management risk flag would work, if STEP amendment records became available |
| Limitations | Performance measures fit to the simulation's assumptions, never to real World Bank outcomes |

### Model 3: re-bidding risk

| Item | Definition |
|---|---|
| Business question | When a tender is published, how likely is it to fail and need re-bidding? |
| Prediction unit | Tender notice (DS00979 IFB / REOI / prequalification; 55,223 in the cohort) |
| Prediction time (proposed) | First publication of the tender notice |
| Target | `rebid_within_12_months` (binary). **Simulated** |
| Outcome observation date | In the simulation, publication + 12 months. In real data, a verified successor tender |
| Features | Known at publication: notice type, method, category, published window and deadline status, country / region, income group, project stage, sector |
| Label source | **Synthetic.** Output is a *simulated re-bidding probability* |
| Use for procurement officers | Demonstrates an early-warning flag at tender design |
| Limitations | No verified re-bids. Cancellations, revisions and addenda are not re-bids. The project's own re-bid markers are label evidence and never features |

## 3. Tender-duration label validation (Task 2)

### 3.1 Linking method

**Sources:**
- Tender notices (IFB / REOI / prequalification) from `wbg_silver.procurement_notice_details`.
- Contracts from Silver `contract_awards` and Gold `gold_contract_analytics`.

**Matching:** same project and equal normalised reference.
- 2,496 tender–contract pairs in the Silver candidate table.
- An independent re-derivation from hashed package keys gives the **identical 2,496 pairs** (0 differences).

**Why only 184 projects:** DS00979, which covers all 2,000 projects, has **no reference number**, so the other 1,816 projects (163,744 contracts) cannot be linked. Description similarity is not evidence of identity (Silver notices report §6), so it was not used.

### 3.2 Label funnel (measured)

| Step | Contracts | % of cohort | % of 184-project scope |
|---|---:|---:|---:|
| Cohort contracts (DS00005, Gold #2) | 178,921 | 100.0 | – |
| In the 184 projects with notice references | 15,177 | 8.48 | 100.0 |
| With a borrower contract reference | 15,177 | 8.48 | 100.0 |
| Linked to ≥ 1 tender notice | 2,495 | 1.39 | 16.44 |
| **Valid under the documented rule** | **2,114** | **1.18** | **13.93** |

**Exclusions.** The documented Phase 1 rule (`discovery/scripts/p02_join_analysis.py`) was applied unchanged, with exclusion reasons assigned in order:

| Reason | Contracts |
|---|---:|
| Tender published on or after the signing date (back-filled notice) | 265 |
| Deadline not after publication | 110 |
| No submission deadline | 6 |
| Tender published on or after the award notice | 0 (the first rule already removes these) |

**Sensitivity:**
- Using the DS00979 deadline instead of the API deadline still gives 2,114.
- Also matching award notices on the reference quoted in the award text still gives 2,114.

**Comparison with earlier figures:**

| Source | Valid labels | Rate | Median |
|---|---:|---:|---:|
| Earlier 276-project sample (historical) | 3,782 | 16.9% of 22,402 contracts | 140 days |
| This cohort (measured) | 2,114 | 13.9% of the 15,177 linkable contracts | 144 days |

- Method medians agree closely: RFB 137.5 vs 149, QCBS 375 vs 371, RFQ 72 vs 66, individual consultants 125 vs 123.
- The Gold notices report's figure of "about 1,271 contracts" was **not reproduced**. Its derivation is undocumented; 2,114 supersedes it.
- The two populations were **not combined**.

### 3.3 Quality checks (measured)

| Check | Result |
|---|---|
| Contract key | Unique (1 row per contract) |
| Signing dates after the snapshot | 0 |
| Notice publication dates | `noticedate` equals the DS00979 publication date for all shared notices (Silver check) |
| Negative or zero durations before the rule | 265 (all excluded) |
| Valid durations | min 1, p25 92, **median 144**, p75 251, p95 511, p99 810, max 1,372 days. Skew 2.11; on the log scale −0.62 |
| Extremes (kept, not deleted) | 7 at ≤ 7 days; 277 over 365; 30 over 730; 3 over 1,095 |
| Shared packages | 1,691 packages. 155 have several contracts, holding 578 contracts (27.3%); at most 37 contracts in one package |
| More than one tender notice per contract | 1. Mixed notice types: 0. Cancelled or revised tender notices among valid labels: 0 |

### 3.4 Censoring and truncation (measured)

**Left truncation.** DS00005 starts on 2019-07-01. A tender published earlier appears only if its contract was signed later, so the 56 labels from tenders published before that date skew long (median 352 days).

**Right truncation.** Recent tenders appear only if they have already been signed. The median for FY2026 tenders is 103 days, against 124–190 days in FY2020–25.

**Full-follow-up subsets:**

| Required follow-up | Tender publication window | Valid labels | Projects | Durations above the horizon |
|---|---|---:|---:|---:|
| 365 days | 2019-07-01 to 2025-10-20 | 1,919 | 135 | 250 |
| 540 days | 2019-07-01 to 2025-04-28 | 1,776 | 129 | 81 |
| **730 days (recommended)** | 2019-07-01 to 2024-10-20 | **1,615** | 124 | 27 |

**Tenders with no linked contract.** Of 3,985 tender packages in the 184 projects, 2,012 have no linked contract; 1,459 of them are more than 730 days old. A Kaplan–Meier estimate on the 3,113 packages first published on or after 2019-07-01 gives the share still unlinked:

| Days after first notice | 90 | 180 | 365 | 730 |
|---|---:|---:|---:|---:|
| Share still unlinked | 0.85 | 0.63 | 0.47 | 0.39 |

**About 39% of packages never link within two years.** These are mostly not open tenders: they are cancellations, re-tenders under new references, or contracts recorded under a different reference. They cannot be told apart in the data, so they **cannot be treated as censored observations** for the contract-level target.

### 3.5 Coverage and representativeness (measured)

**By method** (share of contracts in the 184 projects that have a valid label):

| Method | Coverage |
|---|---:|
| QCBS | 68.6% |
| CQS | 34.4% |
| RFB | 30.3% |
| Individual consultants | 17.0% |
| RFQ | 2.9% |
| Direct Selection | 0% |

**By category:** Consulting 20.1%; Works 18.2%; Goods 11.1%; Non-consulting 3.1%.

**By region:** ESA 26.1%; MENA/AFG/PAK 26.7%; SAR 20.3%; ECA 14.5%; WCA 12.6%; EAP 9.2%; **LAC 3.9%**. LAC is low because Bolivia has 2,474 contracts but only 1 valid label: a reference-format or method effect that still needs investigating.

**By signing FY:** from 4.6% (FY2021) to 21.5% (FY2026).

**Concentration:**
- Valid labels cover 79 of the 88 countries in scope.
- The top 5 countries hold 36.9% of labels; the top 10 projects hold 36.3%.
- Labels per project: median 7, maximum 153.

Full tables are in `ml/eda/tender_labels_20261025T070133Z/coverage_by_*.csv`.

### 3.6 Verdict for Model 1

The real labels are adequate for a **scoped** model:
- advertised methods only;
- the 184 projects with references;
- tenders published from 2019-07-01 to 2024-10-20 (1,615 labels with full two-year follow-up).

The model must not be presented as covering direct contracting or the whole cohort. The validity rules were not relaxed.

## 4. Amendment and re-bidding evidence (Task 3)

**Every historical candidate count reproduced exactly on Silver (measured):**

| Count | Value |
|---|---:|
| Contracts with amendment wording | 56 |
| DS00979 notices with re-bid wording | 654 |
| …of which tender notices | 221 |
| …projects | 142 |
| Contracts with a strong re-bid reference marker | 89 |
| Contracts with a weak `-R` marker | 140 |
| API tender notices | 4,002 |
| …with a re-bid reference marker | 11 |
| API tender references repeated ≥ 30 days apart | 8 |
| API tender status Cancelled / Revised | 2 / 43 |
| Award notices with version > 0 (notice revisions, not amendments) | 2,564 |

Output: `ml/eda/label_evidence_20261025T070231Z/label_evidence_recheck.json`.

### Contract amendment

| Evidence | Count | Classification |
|---|---:|---|
| Contract records whose description names an amendment | 56 (33 projects) | Rule triage: 44 explicit amendment wording, 9 contract-extension wording, 3 ambiguous. The earlier reading (not verification) found 3 that are not amendments; the rules did not catch them, so **rules cannot replace human review** |
| Award notices describing an amendment | 5 | 4 explicit, 1 extension |
| Linked to an earlier contract | Same-supplier route (historical): 13, of which 8 unique. Quoted-reference route (measured, new): **2** | Most originals predate FY2020 or carry different references |
| Notice revisions / bidding-document addenda | 2,564 / 167 | **Not amendments** |
| Confirmed positives / negatives | 0 / 0 | No public source lists every amendment of a contract, so **no defensible negatives exist** |

**Recommendation:** proceed with a **synthetic demonstration**. Real-label training is not possible.

### Re-bidding

| Evidence | Count | Triage (measured) |
|---|---:|---|
| DS00979 tender notices with re-bid wording | 221 | Terms: rebid / re-tender 116; relance 76; second call 15; re-advertise 14. 4 in known false-positive contexts. 123 have a similar earlier tender (historical; similarity ≥ 90, not a link) |
| API tender notices with a marker | 100 | relance 43, re-advertise 25, rebid 14, re-invite 6, second call 3, other 9. **19 in known false-positive contexts** (e.g. a project named "…RELANCE", boilerplate). 2 link to an original by reference |
| Contracts with a re-bid reference marker | 89 | These show a re-bid happened; the original tender is unlinked (0) |
| Plan activities cancelled (25 plans, historical) | 269 | Cancellation can mean descoped, merged or moved; 38 have a successor (historical) |

**Not re-bidding by itself:**
- notice status Cancelled or Revised;
- award-notice versions;
- bidding-document addenda;
- repeated references (lots, revisions);
- plan status "cancelled";
- description similarity without a reference link.

**A real positive needs both:**
- (a) an official statement that the tender failed and was re-launched; and
- (b) a resolved link to the original tender record, verified by a person.

**What needs human verification:**
- all 100 API candidates;
- the 123 DS00979 pairs;
- the 38 plan pairs;
- a pre-registered negative rule.

**Recommendation:** the real labels are **inadequate** (0 verified, no negatives), so use **synthetic outcomes** for the PoC. In parallel, verifying the candidates would give a small real *plausibility* set; it is not a training set.

## 5. Unresolved issues

1. **Prediction points** for all three models: awaiting approval.
2. **Bolivia / LAC:** why 2,474 contracts produce 1 label (reference format or method mix).
3. **Plan-based features** (estimated amount, market approach, planned dates): unavailable for the cohort without plan extraction at scale (D-11).
4. **Current-vintage indicators:** WDI, WGI and CPIA values are not point-in-time. They are excluded from primary experiments (see the feature inventory).
5. **DS00005 amounts** may include in-place revisions (unverified, needs D-20 snapshots). This matters for Model 2's amount feature.
6. **Package-level vs contract-level target:** a decision is needed (§2).
