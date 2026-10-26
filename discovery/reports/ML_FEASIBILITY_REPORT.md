# Predictive ML Feasibility

Run date 2026-10-20. **No model was trained.** Every count is computed from retrieved public data by
[`p02_join_analysis.py`](../scripts/p02_join_analysis.py), [`p05_procurement_plan_tables.py`](../scripts/p05_procurement_plan_tables.py)
and [`p06_ml_feasibility.py`](../scripts/p06_ml_feasibility.py). Outputs: [`profiling/join_analysis.json`](../profiling/join_analysis.json),
[`ml_feasibility.json`](../profiling/ml_feasibility.json), [`procurement_plan_summary.json`](../profiling/procurement_plan_summary.json),
and [`tender_duration_candidates_cluster.csv`](../profiling/tender_duration_candidates_cluster.csv).

**Scope of contract-level measurements:** these use the *cluster sample*, i.e. all notices and contracts of 276
region-stratified projects that have FY2020+ contracts (22,402 contracts, 30,977 notices). Population-level figures stated
as "≈" are **extrapolations, not measurements**.

## Summary

| # | Target (as requested) | Classification | Main reason |
|---|---|---|---|
| 1 | Tender completion time | **Conditionally feasible** | Start (tender notice) and end (signing) are both observable and linkable for 16.9% of contracts, mostly advertised methods, after removing back-filled notices |
| 2 | Contract amendment probability | **Not currently supported by verified public data** | No amendment fields or events at contract level |
| 3 | Re-bidding risk | **Not supported as a verified label**; conditionally feasible with probabilistic plan-based labels (**Corrected 2026-10-21**) | Notices rarely flag cancellations (4 of 30,977). In procurement plans, 30.8% of activities are cancelled, and 11.7% of those have a near-identical successor activity. The link is probabilistic. |
| 3b | Alternative: procurement-plan activity cancellation | Conditionally feasible | **30.8%** of 1,137 parsed plan activities cancelled (**Corrected 2026-10-21**; Phase 1 reported 18.2% because of a header-parsing artefact); needs extraction at scale and semantic validation |
| 3c | Alternative: low competition (single bidder) in competitive methods | **Conditionally feasible** | Bidder counts parseable from award notices (RFB: 23.1% single, n = 4,744) |
| 4a | Project outcome risk (IEG MU or worse) | **Feasible with verified data** | 12,478 rated projects, 26.35% negative, 100% joinable |
| 4b | Procurement-specific project risk (ISR Procurement rating MU or worse) | Conditionally feasible, **cross-sectional only** | 7,088 projects with a latest rating (8.79% negative). **Corrected 2026-10-21:** public ISR documents do not contain the Procurement rating, so history exists only if the platform snapshots the API from now on. |
| 4c | Project timeline risk (closing-date extension) | Conditionally feasible | Observable in monthly loan snapshots (Apr 2011+); bulk history (~1.1 GB) not yet ingested |

---

## 1. Tender completion time

**Target definition (proposed):** days from the *first valid tender publication* of a procurement package to the
*contract signing date*. The unit is the contract, with package-level grouping.

### Required vs observed fields

| Required | Observed? | Source | Coverage |
|---|---|---|---|
| Tender start (invitation / REOI / SPN date) | **Yes** | procnotices `noticedate` (IFB, REOI, Prequal) | Links to 19.87% of cluster contracts |
| Bid submission deadline | Yes | procnotices API `submission_deadline_date` | IFB 94.6%, REOI 93.9% of notices |
| Bid opening / evaluation dates | **Mostly missing** | Only procurement-plan PDFs (actual opening 7.2%, actual evaluation report 6.9% of plan activities; corrected 2026-10-21 from 6.7%) | — |
| Award decision date | Partial | Award notice text | 51.4% of award notices |
| Contract signing (end event) | **Yes** | DS00005 | 100% |
| Planned dates (for planned-vs-actual delay) | Partial | Procurement-plan PDFs (`Signed Contract` planned 97.8%, actual 45.5%) | 33% of activities link to contracts |

### Observed label set (cluster)

Validity rule applied to remove back-filled notices: tender notice date < signing date; deadline > notice date; and the
tender notice was published before the award notice. It rejected **670 of 4,452** candidates (15%). Some of those were IFBs
published the day *after* signing with deadline equal to publication date.

| | n | p25 | **median** | p75 | p95 |
|---|---:|---:|---:|---:|---:|
| All valid | **3,782** (16.9% of 22,402 contracts) | 89 | **140** | 233 | 470 |
| Request for Bids | 1,523 | 98 | 149 | 220 | 386 |
| Individual Consultant Selection | 1,020 | 83 | 123 | 186 | 342 |
| Request for Quotations | 425 | 34 | 66 | 114 | 222 |
| Quality and Cost-Based Selection | 408 | 274 | **371** | 489 | 733 |
| Consultant Qualification Selection | 315 | 89 | 119 | 206 | 430 |
| Least Cost Selection | 49 | 146 | 235 | 269 | 556 |

Sub-stage observability (cluster):
- **Bidding period** (notice → deadline): median 16 days (n = 4,433; 512 zero-day placeholders).
- **Evaluation-to-award** (deadline → award notification): median 92 days (n = 3,808; 354 negative, which marks data defects).

Population estimate: if the cluster rate held, roughly 17% of 282,890 contracts ≈ **48k** labelled contracts (**unverified
extrapolation**).

### Limitations
- **Selection bias:** labels exist almost only for advertised methods. Direct Selection (0.02%) and most RFQs (8.7%) have no
  tender notice by design. A model must be scoped to advertised methods.
- **Package vs contract:** 19% of contracts share a package reference. Multi-lot packages share one start date, so
  contracts within a package are not independent (group-aware CV is needed).
- **Censoring:** tenders published but not yet signed are right-censored. Cancelled tenders never reach signing and are not
  identifiable (see §3). Survival methods are needed to avoid an optimistic bias.
- **Late arrival:** recent contracts are under-represented (Sep 2026 has a third of the rows of Mar 2026).
- **History:** contracts start at FY2020; FY2017–19 are missing. Tender notices before about FY2018 are thin (DS00979).

### Leakage controls (prediction at planning / tender-design time)

| Allowed (known before tender publication) | Not allowed (known only after) |
|---|---|
| Method, category, review type, market approach (plan) | Contract amount (signed), signed local price |
| **Estimated** amount (procurement plan, where linked) | Number of bidders, rejected bidders |
| Country, region, sector, GP, project age at tender date | Award notification date, contract duration |
| Governance indicators for year t−1 (as-of) | Notice version count after publication |
| Historical medians computed only from contracts signed before the prediction date | ISR ratings after the tender date |
| Project milestones already reached (approval, effectiveness) | Project closing-date changes after tender |

**Baseline recommendations** (only after the label pipeline is validated):
1. Empirical quantiles by method × category × region (as-of historical window).
2. Survival model (Kaplan–Meier → Cox or AFT) with censoring.
3. Gradient-boosted trees on log-duration with grouped temporal CV (train on earlier FYs, test on later).

---

## 2. Contract amendment probability

| Evidence searched | Result |
|---|---|
| DS00005 fields | No amendment, variation, revised-amount or revised-date field. A single daily snapshot with no history. |
| Award notice text (23,672) | "amend" / "variation order" / "avenant" / "enmienda": **14 notices** (0.06%) |
| Award notice `Notice Version No` > 0 | 3,707 (15.7%). These are **revisions of the published notice**, not contract amendments, and must not be used as a proxy. |
| WDS documents titled "Amendment" (cluster) | 312, of which 267 are "Agreement", 18 "Financing Agreement" and 15 "Disbursement Letter". These are **legal / financing agreement** amendments at loan or project level. |
| Differences between related financial values | **Not used** (forbidden by the brief and semantically invalid) |

**Classification: not currently supported by verified public data.** Contract amendments are recorded in STEP for
prior-review contracts, but that is not public. Do not construct a proxy label.

**Defensible project-level alternatives:**
- *Restructuring:* "restructuring"-titled documents exist for 77.5% of cluster projects.
- *Closing-date extension:* closing dates change over monthly snapshots in DS00975/6. Loan IDA44840, for example, was
  extended 2013 → 2015 → 2017 → Apr 2017.

Both are project/loan-level and conditionally feasible. They need full history ingestion and a precise definition
(restructuring type is not in metadata).

---

## 3. Re-bidding risk

| Signal | Observed (cluster) | Usable as label? |
|---|---|---|
| Notice `notice_status = Cancelled` | **4** of 30,977 | No (too rare; cancellation reason absent) |
| `notice_status = Revised` | 117 | No (revision ≠ failure) |
| Same package reference with >1 tender notice | 14 of 7,120 package keys | No: too few, and corrigenda are indistinguishable from re-tenders |
| Re-bid / re-tender / cancellation wording in tender notices | 106 of 7,138 (regex; **not validated**) | Not without manual review |
| Rejected bidders listed in award notice | 3,545 (15.0%) of award notices | Evaluation outcome, not re-bidding |
| Procurement-plan `Process Status` | **Corrected 2026-10-21:** cancelled (incl. *Annulé*, *Cancelado*) **350 of 1,137 (30.8%)**; terminated 11; status 100% populated (31 garbled values, 2.7%). Phase 1's "207 (18.2%), blank 42%" came from reading only one of 4 wrapped-header variants. | **Candidate**, but "cancelled" may mean dropped from the plan rather than failed tender (**semantics unverified**); 92.1% of sampled plans contain ≥1 cancelled activity. Cancelled activities match a DS00005 contract in only 0.9% of cases (signed: 69%). |

**Classification: not supported as a verified label.** There is no public identifier linking a failed tender to its re-issued successor.

**Alignment update (2026-10-21), from [`p08_plan_reprocurement.py`](../scripts/p08_plan_reprocurement.py):**
- Within the same plan, 41 of 350 cancelled activities (**11.7%**) have a near-identical activity (description similarity ≥ 90). In 30 of those, the activity was signed or completed.
- Only 3 share the same normalised reference; re-tendered activities usually receive a new reference.
- Candidate re-bidding labels are therefore **probabilistic (class P)** and a lower bound, because successors in other plan versions are not captured.
- They become usable only after manual validation of a sample (decision D-22).

**Alternative targets:**
- **3b. Plan-activity cancellation:** conditionally feasible. Requires parsing STEP plan tables at scale; v3 lists 143,335
  procurement-plan documents, which are *versions* of the plans. It also requires de-duplicating activities across
  versions and confirming status semantics with WB procurement guidance.
- **3c. Low competition:** conditionally feasible. The label is "single bidder" or "≤ 2 bidders", counted from awarded +
  evaluated + rejected supplier IDs in the award notice.

| Method | n (award notices) | Single-bidder % | Zero parsed % | Median bidders |
|---|---:|---:|---:|---:|
| RFB | 4,744 | 23.1 | 8.2 | 3 |
| RFQ | 6,105 | 30.3 | 4.2 | 3 |
| QCBS | 703 | 12.4 | 10.4 | 4 |
| LCS | 152 | 24.3 | 2.0 | 3 |
| CQS | 1,068 | 72.1 | 7.3 | 1 (shortlist-based by design) |
| Direct (DIR/CDS) | 3,628 / 1,689 | 97.5 / 90.8 | — | 1 (by design; exclude) |
| INDV | 5,122 | — | **86.1** | IDs withheld; **unusable** |

Caveats:
- "Zero parsed" means the template held no supplier IDs.
- Legacy `ICB` notices (163) parse to 0 under the current parser.
- Bidder counts are **post-award** information. They are a legitimate *label* for a planning-time model, but never a feature.

---

## 4. Procurement-related / project risk

### 4a. Project outcome (IEG DS00053): **feasible with verified data**

| Item | Value |
|---|---|
| Target | `Outcome` ∈ {Moderately Unsatisfactory, Unsatisfactory, Highly Unsatisfactory} (binary), or the 6-point ordinal |
| Labelled rows | 12,478 rated (151 "Not Rated" excluded) |
| Class balance | **26.35% negative** |
| By instrument | IPF 26.53% (n = 10,680); DPF 25.31% (n = 1,798) |
| By evaluation type | ICRR 23.95% (6,828); PPAR 27.34% (3,705); PCR 31.47% (1,465) |
| By approval decade | 1980s 34.7%; 1990s 26.6%; 2000s 27.8%; 2010s 20.1%; 2020s 20.8% (n = 303). The base rate is drifting. |
| IPF projects approved FY2005+ | 3,321 |
| Join to v3 projects | 100% |
| Point-in-time governance | WGI at approval FY−1: 83.7% of FY1998+ projects; CPIA: 58.6% of FY2006+ (IDA only) |

**Leakage, which must be excluded:**
- DS00053 `Quality of Supervision`, `Bank Performance`, `M&E Quality`, `Final Closing FY` and `Evaluation FY` describe the
  same evaluation or later.
- v3 `icr_ratings`, `ieg_ratings`, `isr_ratings` (latest), current `closingdate`, loan `cancelled_amount`, `status`,
  disbursement totals.
- Contract aggregates computed over the full project life.

`Quality at Entry` is assessed *ex post* by IEG, so it is leakage too, despite its name.

**Allowed features at approval time:** country (as-of governance), region, sector mix, commitment, instrument, approval FY,
pre-approval milestone durations (concept → appraisal → approval), and the borrower's portfolio outcomes evaluated *before*
the approval date.

**Biases:**
- Only the latest evaluation per project is kept.
- Projects not yet evaluated are excluded; recent projects are evaluated with a lag of several years.
- The 2020s are underpopulated (n = 303).

**Relevance:** project-level, not procurement-specific. It provides **context risk** for procurement planning.

### 4b. ISR Procurement rating: conditionally feasible
- v3 `isr_ratings` contains a "Procurement" dimension for **7,088 projects**. Values: S 3,679; MS 2,900; MU 491; U 143;
  HS 50; HU 5. That is **8.79% MU or worse**.
- Only **one ISR date per project** (the latest, 2005-01-25 → 2026-10-15) is in the API. A cross-sectional label mixes
  projects at different implementation stages.
- ~~A temporal label requires parsing ISR documents.~~ **Corrected 2026-10-21:** public ISR documents (27 text renditions,
  two templates, 2016–2026) contain PDO, Implementation Progress, Overall Risk and SORT risk ratings (incl. **Fiduciary**),
  but **not** the Procurement / FM / M&E component ratings. The API's procurement rating is therefore available only as a
  latest value. A temporal procurement label requires the platform to snapshot the API from now on (D-20).
- What *is* extractable from ISR documents (25/27 parsed; validated against API latest values): PDO / IP rating history and
  **Fiduciary risk** history, a procurement-relevant proxy because the fiduciary rating covers procurement and FM risk.
  The series must be de-duplicated by (project, sequence number), since re-uploads repeat a sequence up to 8 times.
- This is the most procurement-specific public risk label found.

### 4c. Timeline risk (closing-date extension): conditionally feasible
- Requires DS00975/DS00976 full history (566.94 MB + 556.6 MB CSV).
- The label is first observed (snapshot-derived) extension of `closed_date_most_recent`, available from April 2011.
- It is loan-level and aggregates to project level. Not ingested in Phase 1.

### 4d. Project dropped before approval: feasible (data), low procurement relevance
6,907 Dropped versus 18,319 Closed in v3 (27.4%). It is excluded from the recommended scope.

---

## 5. Cross-cutting leakage rules for any model

1. Every feature carries a `knowledge_available_date`: publication, disclosure or snapshot date, **not** event date. Award
   notices are published a median 127 days after the award.
2. Country indicators are joined on (ISO2, year ≤ prediction year − 1). WGI has no 1997/1999/2001 values; do not
   interpolate silently.
3. Training/validation splits are temporal (by FY of prediction date) and grouped by project, or package for contract
   targets.
4. Snapshot-only sources (DS00005, v3 projects, DS00053) reflect *today's* values. Historical feature values must come from
   timestamped sources (monthly loan snapshots, dated notices, document dates), or from platform snapshots captured going
   forward.

## 6. What would change these classifications

| Data | Effect |
|---|---|
| Full STEP procurement-plan table extraction across plan versions | Upgrades 1 (planned-vs-actual delay) and 3b |
| Full DS00975/6 history | Enables 4c |
| ~~ISR document parsing (procurement rating history)~~ Snapshotting v3 `isr_ratings` going forward (**corrected 2026-10-21**) | Upgrades 4b to a temporal target after enough snapshots accumulate; ISR documents give Fiduciary-risk history now |
| Non-public STEP amendment / cancellation records | Would be needed for 2 and 3 as defined. **Out of scope** (not public). |

---

## Alignment update (2026-10-21)

Added after the technical assessment became available. See
[`docs/requirements/ASSESSMENT_ALIGNMENT_REPORT.md`](../../docs/requirements/ASSESSMENT_ALIGNMENT_REPORT.md).

1. **Corrections (marked inline):**
   - Procurement-plan cancellation rate is 30.8% (not 18.2%).
   - The ISR procurement-rating history is not recoverable from public ISR documents.
2. **Re-bidding (assessment Module 2)** moves from "not supported" to *conditionally feasible with probabilistic,
   plan-derived candidate labels*, pending validation (D-22).
3. **Amendment probability** remains unsupported. No new public source was found: 14 of 23,672 award notices mention an
   amendment, and WDS "Amendment" documents are legal-agreement amendments.
4. **Governance indicators as predictors** (assessment Module 2): coverage is unchanged (ER J13–J15).
5. **Conflicting-evidence prevalence**, relevant to label choice:
   - Latest ISR PDO rating vs IEG outcome agree on direction for 85.6% of 4,928 projects; 13.1% are ISR-positive but
     IEG-negative.
   - 782 projects have a later PPAR rating that differs from the earlier ICR Review rating.
   - Outcome labels must specify *which* evaluation they use (D-18).
