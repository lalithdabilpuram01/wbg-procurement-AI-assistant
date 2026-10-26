# Assessment Alignment Report: Phase 1 Discovery vs Technical Assessment

**Date:** 2026-10-21 · **Status:** alignment review complete. Phase 2 has not started.

**Inputs:**
- The technical assessment, *"Technical Assessment: AI-Powered Procurement Intelligence Platform"*. The file was found at
  [`procurement-intelligence/assessment.md`](../../assessment.md), not under `docs/requirements/` as expected. It was read
  in full and left in place; this report lives in `docs/requirements/` as requested.
- Phase 1 reports in [`discovery/reports/`](../../discovery/reports/), scripts, samples and profiling outputs.

**Additional read-only discovery performed:**
- [`scripts/p07_assessment_gap_probes.py`](../../discovery/scripts/p07_assessment_gap_probes.py) (G1–G7) and
  [`scripts/p08_plan_reprocurement.py`](../../discovery/scripts/p08_plan_reprocurement.py).
- A corrected re-run of [`p05`](../../discovery/scripts/p05_procurement_plan_tables.py), using cached PDFs only.
- Outputs: [`profiling/assessment_gap_probes.json`](../../discovery/profiling/assessment_gap_probes.json),
  [`procurement_plan_reprocurement_candidates.json`](../../discovery/profiling/procurement_plan_reprocurement_candidates.json),
  [`ieg_source_disagreement.json`](../../discovery/profiling/ieg_source_disagreement.json), and the updated
  [`procurement_plan_summary.json`](../../discovery/profiling/procurement_plan_summary.json).
- New network use: about 130 small API calls, 24 ISR text files (≈0.5 MB) and one 5.6 MB guidance PDF. No full dataset was
  downloaded again.

## How coverage is graded

| Status | Meaning |
|---|---|
| **Covered** | Phase 1 + this review provide concrete, cited evidence that fully answers what public-data discovery can establish for the requirement |
| **Partially Covered** | Some evidence exists, but a measurable question remains open, or evidence exists for only part of the requirement |
| **Missing** | No discovery evidence yet, or the requirement is a design / implementation deliverable not started (Phase 2) |

Phase 1 was discovery-only. Design deliverables (pipelines, models, agent framework, guardrails, rollout) are therefore graded
on the **data evidence that underpins them**. Wherever the design itself is still to be produced, that is stated explicitly.
Nothing is marked Covered because an architecture *could* support it.

---

## 1. Requirements coverage summary

| Area | Requirements | Covered | Partially | Missing |
|---|---:|---:|---:|---:|
| Data layers & lifecycle scope (§1–2) | 11 | 1 | 9 | 1 |
| Open-data ecosystem (§3) | 12 | 8 | 4 | 0 |
| Module 1: Ingestion & knowledge representation | 3 | 0 | 3 | 0 |
| Module 2: Predictive & RAG engines | 9 | 2 | 4 | 3 |
| Module 3: Guardrails, faithfulness, attribution | 2 | 0 | 2 | 0 |
| Module 4: Continuous learning & rollout | 3 | 0 | 1 | 2 |
| Rubric criteria (§5) | 13 | 4 | 7 | 2 |
| **Total** | **53** | **15** | **30** | **8** |

Before this review, 6 of these requirements had **no** discovery evidence:
- semi-structured documents and specification sources (A-05, A-07)
- OKR, guidance notes and CPFs (S-10)
- the conflicting-evidence methodology (M3-2)
- procurement-rating history (M2-3 / A-09)
- sector-code linking (M1-2)

This review moved them to Partially Covered or Covered, or corrected the evidence behind them. The 8 remaining Missing items are
design / implementation deliverables (5) or depend on data that is **not public** (3).

---

## 2. Requirements traceability matrix

Δ marks rows whose evidence or status changed in this review. Report references are to `discovery/reports/` unless prefixed.

### 2.1 Data layers and lifecycle scope (assessment §1–2)

| ID | Assessment requirement | Evidence | Status | Identified gap | Follow-up action |
|---|---|---|---|---|---|
| A-01 | Structured data: milestones, award amounts, vendor identities, procurement methods, execution timelines, disbursement trajectories | Inventory A1/B1/E; Profiling §2, §4, §7; v3 `milestones` (approval 97.7%, effectiveness 53.2%) and `loan_transactions`; DS00975/6 history | **Covered** (for public data) | Execution timelines are project-level. Contract-level execution is limited to planned duration (award text 97%) and plan "Contract Completion" dates (planned 86.9% / actual 15.7%). | Record contract-level execution data as a known limitation (D-03) |
| A-02 | Semi-structured: procurement strategies, ToRs, market approaches, tender docs, bid evaluation reports, supervision updates | Δ G4: REOI text mentions ToR in 71.7% and qualification / experience in 83.0% (median 4,450 chars). Δ Procurement plans: market approach 99.9%, procurement process 45.4% (corrected). Award notices: evaluated / rejected bidders and reasons (15.9%). Δ Aide Memoires: 2,815 in WDS, 34.1% of cluster projects. | **Partially** | Bid evaluation reports and full tender documents are **not public**. PPSD (procurement strategy) documents were not searched for. | Search WDS for PPSD / "Project Procurement Strategy for Development" (Phase 2 discovery task); treat evaluation summaries in award notices as the substitute |
| A-03 | Unstructured: audit findings, completion evaluations, lessons, policy notes, regional risk analyses | RAG §1 (ICR 8,617; ICRR 7,847; PPAR 2,648); Δ G6: Auditing Document 30,340 (88.8% of cluster projects), CPF 864, SCD 282, IEG Evaluation 1,019 | **Partially** | Audit documents are financial audits (procurement content unverified). CPF / SCD / IEG thematic evaluations are country- or sector-level with **0%** project linkage. | Define country / sector-level evidence scope (D-09 extension); sample 10 audit reports for procurement content |
| A-04 | Planning: realistic schedules | ML §1: 3,782 valid tender-duration labels (cluster; RFB median 149 d, QCBS 371 d); plan planned-vs-actual dates | **Partially** | Coverage is 16.9% of contracts, biased to advertised methods; population-level rate not measured | Population-level partitioned notice join (§6, action 3) |
| A-05 | Planning: viable market-approach options | Δ Plans: `Market Approach` 99.9% populated (Open-National, Open-International, Limited, Direct); award notices carry method only | **Partially** | Only available where procurement plans are parsed (38 plans sampled); no market-approach field in DS00005 or notices | Include market approach in the plan-extraction pilot (D-11) |
| A-06 | Planning: proactive risk mitigations from precedent | Lessons sections located in ICR / ICRR / PPAR (RAG §2); ISR risk ratings (SORT) incl. Fiduciary | **Partially** | Mitigation *actions* are narrative only; no structured mitigation data | Section-level extraction pilot (RAG §5) |
| A-07 | Tender design: cross-reference technical specifications, qualification criteria, ToRs vs evaluation benchmarks | Δ G4: specifications mentioned in only 8.9% of REOIs / 7.2% of IFBs; qualification criteria in REOI 83%, IFB 57%; Δ Procurement Regulations PDF (142 pp.) retrieved; award rejection reasons ("Non Responsive" 5,197 phrases; "Does not meet Eligibility Requirements" 1,305; "Failure to meet minimum technical score" 540) | **Partially** | **Technical specifications are not publicly available** in the examined sources. "Evaluation benchmarks" are only proxied by award-notice outcomes. | Re-scope with the product owner (D-17, revised) |
| A-08 | Execution: milestone tracking | v3 project milestones; loan snapshots; plan stage dates | **Partially** | No public contract-level milestone or progress data | Accept project / loan-level monitoring for v1 |
| A-09 | Execution: early-warning indicators (contract slippage, compliance bottlenecks) | ISR ratings (latest via API; Δ PDO / IP / risk history parseable from ISR text, 25/27); Δ ISR **Procurement** rating is **not** in public ISR documents; Δ Fiduciary risk history parseable; closing-date extensions (DS00975/6) | **Partially** | Contract slippage isn't observable (no actual completion except 15.7% of plan activities). The procurement-rating time series only exists from the API (latest value). | Snapshot the API `isr_ratings` from now on (D-20); fiduciary-risk history extraction |
| A-10 | Continuous learning: convert new evaluations / reviews into intelligence | Refresh report; IEG daily `As of Date`; Δ 782 projects where DS00053 now shows a later PPAR replacing the earlier ICRR rating | **Partially** | Mechanism is design work; the source overwrites earlier evaluations | Platform-side evaluation history (D-13, D-18) |
| A-11 | Problem outcomes: re-tender / cancellation rates; specification mismatches; supplier performance shortfalls | Δ Plans: **30.8%** of activities cancelled (corrected from 18.2%); Δ 11.7% of cancelled activities have a near-identical successor activity; notices cancelled 4 / 30,977; G6: debarment list is an HTML page with client-side data | **Missing** for specification mismatch & supplier performance; partial for cancellation | No public supplier performance ratings; debarment data not available as an open dataset; specification mismatch not observable | D-24 (debarment data policy); flag to product owner |

### 2.2 Open-data ecosystem (assessment §3)

| ID | Assessment requirement | Evidence | Status | Identified gap | Follow-up action |
|---|---|---|---|---|---|
| S-01 | Contract awards: descriptions, values, supplier names, country / region, category, review types | Inventory A1; Profiling §2 (all fields 96–100%) | **Covered** | FY2017–19 gap; category label is "Works" (assessment says "Civil Works") | D-03 |
| S-02 | Projects: IDs, development objectives, implementing agencies, total commitments, E&S risk classification, lifecycle status | Inventory B1; Δ G1: PDO 57.6% population / 95.7% cluster; implementing agency 29.8%; E&S: legacy EA category (A/B/C/F…) 55.6%; ESF `esrc_ovrl_risk_rate` 51.5% of cluster projects (was not in the Phase 1 population pull) | **Covered** (Δ) | Two E&S schemes coexist (EA category pre-ESF; ESF risk rating post-2018); agency field sparse | Add `esrc_ovrl_risk_rate` to the v3 population pull; map both E&S schemes |
| S-03 | Finances: loan / credit / **grant** disbursement schedules, commitment timelines, **co-financing** | Inventory E; Δ G1: v3 `financers` 64.5% populated; **26.0%** of projects have co-financiers (multilateral 3,117, bilateral 2,665, trust funds 4,457 entries); IDA grants in DS00001; `loan_transactions` | **Partially** | *Projected* disbursement schedules are only in PAD text (not extracted); trust-fund grant disbursements are not in IBRD / IDA statements | Extract PAD disbursement-estimate tables in the parsing pilot |
| S-04 | Procurement notices & prior-review archives: SBDs, REOIs, award summaries | Inventory C1 / C2; DS01004 (prior-review contracts FY2001–16) | **Partially** | Standard Bidding Documents are templates (not retrieved); full prior-review archives are not public | Retrieve SPD templates only if tender-design scope is retained |
| S-05 | ISRs: ratings, intermediate milestone progress, roadblocks | RAG §2; Δ G7: ISR series for 2 projects. Three text layouts; parser extracts PDO / IP / risk from 25/27 renditions (validated against API latest); **duplicate ISR records** (Seq 12 ×6, Seq 10 ×8, all English); **Procurement / FM component ratings absent from public ISR text** | **Covered** (Δ) | Component-rating history not public; duplicates need de-duplication by (project, seq no) | D-20; parser hardening via PDF tables |
| S-06 | Documents & Reports API | Inventory D1 (v3 619,903 docs) | **Covered** | — | — |
| S-07 | Open Knowledge Repository (OKR) | Δ G6: DSpace 7 REST API reachable (JSON). "procurement lessons learned" → 149 items. **5 / 5** sampled items' `okr.guid` resolve to WDS v3 documents. | **Partially** (Δ) | Overlap with WDS beyond 5 items not measured; OKR adds publications (research, thematic) rather than project documents | Measure OKR↔WDS overlap on 500 items (D-25) |
| S-08 | PADs: design rationale, procurement arrangements, initial risk assessments | RAG §2; procurement headings located in 4/4 PADs; Δ G3: PAD median **98 pages**, **46%** ≥ 100 pages (max 203) | **Covered** | Section extraction not yet validated | Parsing pilot |
| S-09 | ICRs: retrospectives, challenges, cost variances, lessons | RAG §2; Δ G3: ICR median 59 pp. (3.3% ≥ 100) | **Covered** | FR / ES section typing not handled | D-16 |
| S-10 | IEG evaluations, PPARs, **thematic sector studies**; procurement guidance notes; CPFs | DS00053; PPAR sample; Δ G6: IEG Evaluation 1,019, CPF 864, SCD 282 in WDS v3; Δ Procurement Regulations for IPF Borrowers (4th ed., Nov 2020, 142 pp., text layer) downloaded; WDS full-text "procurement guidance note" 202 hits | **Partially** (Δ) | Guidance notes are published as individual PDFs on worldbank.org (no API); thematic / country documents have no project link | Curate a guidance corpus list (manual, versioned) |
| S-11 | CPIA & WGI | Inventory F; ER J13–J15 | **Covered** | Regional operations (12.4% of contract rows) | D-08 |
| S-12 | Use of "publicly available data from international development repositories" | All sources public; no credentials; access log | **Covered** | — | — |

### 2.3 Module 1: Ingestion pipeline & multimodal knowledge representation

| ID | Assessment requirement | Evidence | Status | Identified gap | Follow-up action |
|---|---|---|---|---|---|
| M1-1 | ETL + entity-resolution pipeline for CSV / JSON and PDF / **Word** | Formats observed: CSV, JSON, XLSX (v3 bulk), HTML (notice text), PDF, TXT renditions. ER classes E / EN / C / P / U defined and measured (ER report). Discovery scripts are prototypes, not a pipeline. | **Partially** | **No Word documents were encountered** in any examined source (WDS exposes PDF + TXT only). The production pipeline design has not started. | Confirm whether Word ingestion is still a requirement; Phase 2 design |
| M1-2 | ER schema / KG reconciling disjoint keys, e.g. contract row → specific ICR lesson section via Project ID, **sector codes**, **temporal proximity** | ER report (candidate ERD, 18 measured joins). Contract → ICR is High (E) at project level. Contract → specific lesson: 0 contract identifiers in 7 evaluation texts. Δ G5: **no shared code** between the Global Practice taxonomy (DS00005 16 labels; IEG 18; exact overlap: 1, "Education") and the sector taxonomy (v3 `major_sectors` 82.9%, 35 codes; WDS sector labels 25/25 match v3). DS00005 GP labels have internal variants; v3 `major_sector_name` is comma-joined (56.5% multi-valued) with "FY17 -" prefixes (67.4%). | **Partially** (Δ) | The assessment's example link (contract → lesson via sector + time) is **probabilistic** (class P) and must be scored, not asserted. A GP ↔ sector crosswalk is needed. Relational-vs-KG choice not made. | D-19 (KG vs relational + link tables); D-21 (GP ↔ sector crosswalk); define "contextual relevance" link type with score and evidence scope |
| M1-3 | Parsing, chunking, embedding for 100+ page reports with tables / annexes | RAG §2, §5 (22 PDFs). Δ G3: PADs median 98 pp., 46% ≥ 100; PPAR 16% ≥ 100; max 203 pp. No bookmarks (0/22); tables in 18/22; scanned pages mainly pre-2010. | **Partially** | **Embedding strategy not assessed** (out of Phase 1 scope); only 3 sampled documents ≥ 94 pages | Parsing + embedding evaluation pilot on ≥ 50 long PADs (Phase 2, after approval) |

### 2.4 Module 2: Predictive & prescriptive engines

| ID | Assessment requirement | Evidence | Status | Identified gap | Follow-up action |
|---|---|---|---|---|---|
| M2-1 | Predict tender completion times | ML §1: conditionally feasible; 3,782 valid labels in cluster; validity rule; leakage table | **Covered** (feasibility) | Population coverage extrapolated (≈48k, unverified) | Population-level measurement |
| M2-2 | Predict **amendment** probabilities | ML §2: no public contract-amendment data (14 / 23,672 award notices mention amendment; notice versions ≠ amendments) | **Missing** (data not public) | Not supportable without non-public STEP data | Product-owner decision: drop, or substitute project restructuring / closing-date extension (D-10) |
| M2-3 | Predict **re-bidding** risks | Δ Plans: 30.8% activities cancelled; Δ 41 / 350 cancelled activities (11.7%) have a near-identical successor (30 signed / completed); only 3 via exact reference; within-plan lower bound | **Partially** (Δ, was "Not supported") | Labels are **probabilistic** (description similarity); no successor identifier; cancellation semantics unverified; requires plan extraction at scale | Plan-extraction pilot with a manually validated sample of 200 cancelled → successor pairs (D-11) |
| M2-4 | Use historical project variables and governance indicators | ER J13–J15; ML §4a (WGI as-of 83.7% FY1998+) | **Covered** | Regional operations | D-08 |
| M2-5 | Define the algorithmic model (GBT / survival / probabilistic networks) | ML report gives baseline recommendations only | **Missing** (design) | Model specification is Phase 2 | Phase 2 design after D-10 |
| M2-6 | Agentic RAG: surface operational precedents | RAG §3, §7; ER (package entity) | **Partially** | Retrieval / agent design not started; no evaluation set | Gold-set creation (§6, action 6) |
| M2-7 | Agentic RAG: **similar past technical specifications** | Δ G4 (specs in < 9% of notices); RAG §3 | **Missing** (data not public) | Specifications not in public sources | D-17 (revised) |
| M2-8 | Agentic RAG: historical lessons | RAG §2 (lessons headings in all English ICR / ICRR / PPAR samples) | **Partially** | Multilingual; section validation | Parsing pilot |
| M2-9 | Triggered when a user initiates a new procurement package | Package entity derived (ER §1); plan activities give package attributes (method, market approach 99.9%, estimated amount 89.3%) | **Partially** | Package-input schema not designed | Phase 2 design |

### 2.5 Module 3: Guardrails, faithfulness, attribution

| ID | Assessment requirement | Evidence | Status | Identified gap | Follow-up action |
|---|---|---|---|---|---|
| M3-1 | Validation layers so every recommendation / risk flag is anchored in verifiable documents and open data | Metadata dictionary (evidence scope, link class, knowledge-available date); page citations feasible (20/22 `.txt` form-feeds; template-B ISRs print "Page n of m"); every structured record has a stable source ID | **Partially** | Guardrail design (claim extraction, verification, abstention) not started; no faithfulness test set | Phase 2 design; build a citation-verification test set |
| M3-2 | Methodology for **conflicting evidence** (e.g. ICR vs ISR ratings) | Δ G2 (latest ISR vs IEG outcome, n = 4,928): side agreement 85.6%; **644 (13.1%)** projects rated satisfactory in the last ISR but unsatisfactory by IEG; 66 the reverse. ISR Procurement vs IEG outcome agreement 79.0% (n = 4,604). Δ **Two official IEG sources disagree for 793 projects**; 782 are a later PPAR (DS00053) superseding an earlier ICR Review / PCR (v3 `ieg_ratings`). | **Partially** (Δ, was Missing) | Conflict *frequency* and a natural precedence (evaluation depth + recency) are now evidenced. The resolution policy itself is a design decision. | D-18 (evidence precedence policy: PPAR > ICRR > ICR self-rating > ISR, recency-aware, always show both) |

### 2.6 Module 4: Continuous learning & implementation

| ID | Assessment requirement | Evidence | Status | Identified gap | Follow-up action |
|---|---|---|---|---|---|
| M4-1 | Closed loop: new milestones, supervision notes, **contract amendments** update models and indices | Refresh report (snapshot diffing; late arrival; no record timestamps); Δ ISR duplicates; Δ IEG overwrites earlier evaluations (782 cases) | **Partially** | Contract amendments are not public; feedback-loop design not started | D-13; amendments out of scope unless a source is found |
| M4-2 | Phased pilot rollout strategy | — | **Missing** (design) | Not a discovery deliverable | Phase 2 planning |
| M4-3 | KPIs: planning-cycle-time reduction, retrieval precision / recall, adoption | Possible public baselines: observed tender durations (ML §1); plan planned-vs-actual dates | **Missing** | No KPI definitions; **no ground-truth set** for retrieval precision / recall; adoption needs product telemetry | D-23 (KPI baselines + gold set with procurement SMEs) |

### 2.7 Rubric criteria (assessment §5)

| ID | Criterion | Evidence | Status | Gap / follow-up |
|---|---|---|---|---|
| R-01 | Robust multimodal ingestion | Formats, API limits, refresh behaviour measured | **Partially** | Pipeline design pending |
| R-02 | Depth / scalability of KG / relational schema | Candidate ERD, entity inventory, 18 measured joins, link classes | **Partially** | D-19 |
| R-03 | Unstructured text & document hierarchy | RAG §2 / §5; ISR templates (Δ) | **Partially** | Section validation pilot |
| R-04 | Soundness of predictive risk modelling | Label feasibility, class balance, leakage rules | **Covered** (data side) | Model design Phase 2 |
| R-05 | Sophistication of retrieval pipeline | — | **Missing** | Phase 2 design |
| R-06 | Practical hallucination mitigation | Evidence-scope rule; citation feasibility | **Partially** | Guardrail design |
| R-07 | Strict citation & grounding mechanics | Page / section citation feasibility; provenance metadata implemented in discovery (MANIFEST, checksums, access log) | **Partially** | Section-level validation |
| R-08 | Effective use of specified open data sources | All §3 sources examined (Δ OKR, guidance, CPF / SCD added) | **Covered** | — |
| R-09 | Understanding of procurement stages | Stage dates in notices / plans; method-specific behaviour (direct selection unadvertised, back-filled notices) | **Covered** | — |
| R-10 | Realistic handling of missing / incomplete reporting | Missingness buckets, late-arrival, back-fill, gaps documented | **Covered** | — |
| R-11 | Feasible deployment roadmap | Recommended initial scope (Summary) | **Partially** | Phased plan Phase 2 |
| R-12 | Concrete validation metrics & KPIs | — | **Missing** | D-23 |
| R-13 | Continuous learning / data feedback loops | Refresh assessment | **Partially** | Loop design |

---

## 3. Missing or partially addressed requirements (prioritised)

**Missing because the data is not public.** These need a product-owner decision, not more engineering:
- contract amendment prediction (M2-2)
- similar technical specifications (M2-7, part of A-07)
- supplier performance shortfalls and specification mismatches (A-11)

**Missing because they are design / implementation deliverables** (Phase 2, after approval):
- model specification (M2-5)
- retrieval pipeline (R-05)
- rollout plan (M4-2)
- KPIs and evaluation sets (M4-3, R-12)

**Partially covered, needing more discovery before or early in Phase 2:**
1. Population-level contract ↔ notice join (tender-duration coverage is still extrapolated).
2. Procurement-plan extraction at scale: the main source for market approach, planned-vs-actual dates and cancellation /
   re-procurement labels.
3. Long-document parsing and embedding evaluation on ≥ 50 PADs of 100+ pages.
4. GP ↔ sector crosswalk, and a scored "contextual relevance" link for contract ↔ lesson.
5. OKR ↔ WDS overlap; guidance-corpus curation.
6. PAD disbursement-estimate tables (projected schedules).

---

## 4. New findings from the alignment review

| # | Finding | Evidence |
|---|---|---|
| N-1 | **Public ISR documents do not contain the Procurement (or FM, M&E, Project Management) component ratings.** Only PDO, IP, overall risk and SORT risk categories (incl. Fiduciary) are present. 0 of 27 ISR texts across both templates had a Procurement rating. | G7; [`wbdisc/isr.py`](../../discovery/wbdisc/isr.py) |
| N-2 | ISR text renditions have **three layouts**: (A) column-aligned; (B, ~late 2024+) glyph-delimited with SORT "Last Approved / Proposed" columns and printed page labels; (C) vertically flattened values (unparseable from text; 2/27). Rating values are prefixed by a private-use glyph (U+F06C). Parsed PDO / IP ratings matched the API's latest values for both test projects. | G7; `tests/test_isr.py` |
| N-3 | **Duplicate ISR records**: 43 records for 17 sequence numbers (Seq 12 ×6), and 31 for 14 (Seq 10 ×8). All English, so these are re-uploads, not translations. | G7 |
| N-4 | **Conflicting official evidence is common and structured.** 793 projects have different outcome ratings in v3 `ieg_ratings` and DS00053; 782 are later PPARs superseding ICRR / PCR ratings. 13.1% of projects were satisfactory in their last ISR but rated unsatisfactory by IEG. | G2; `ieg_source_disagreement.json` |
| N-5 | **Correction:** procurement-plan cancellation rate is **30.8%** (350 / 1,137), not 18.2%. Status is 100% populated (not 42% blank); market approach 99.9%; estimated amount 89.3%. Cause: wrapped PDF header cells created 123 header variants for 71 logical columns, and Phase 1 read one variant. | p05 re-run; `wbdisc/procurement_plan.py` |
| N-6 | **Re-procurement after cancellation is partly observable:** 11.7% of cancelled plan activities have a near-identical activity in the same plan (30 of 41 signed / completed). Only 3 share the reference, so linkage is mostly probabilistic. | p08 |
| N-7 | E&S risk classification **is available**: legacy EA category (55.6% of projects) and ESF `esrc_ovrl_risk_rate` (51.5% of cluster projects). Phase 1 did not pull the latter for the population. | G1 |
| N-8 | **Co-financing structures** are available in v3 `financers` (64.5% populated; 26.0% of projects have external co-financiers). | G1 |
| N-9 | Long-form documents: PADs median 98 pages, **46% ≥ 100 pages** (max 203). PPARs 16% ≥ 100. ICRs median 59. | G3 (v2 `no_of_pages`, n = 1,198) |
| N-10 | Sector linkage: Global Practice and sector taxonomies share **no code**. v3 `major_sector_name` is an ambiguous comma-joined string with FY17 / Historic variants; the structured `major_sectors` (35 codes) and WDS sector objects align 25 / 25. | G5 |
| N-11 | Tender-design substitutes: REOI notices carry ToR content (71.7%) and qualification / experience criteria (83.0%). IFBs carry qualification criteria (57.4%) but rarely specifications (7.2%). Award notices give rejection reasons. | G4 |
| N-12 | Additional repositories: OKR API is open (DSpace 7) and its sampled items map to WDS by GUID. Procurement Regulations PDF is retrievable. CPF 864 / SCD 282 / IEG Evaluation 1,019 / Aide Memoire 2,815 are in WDS. The debarment list is an HTML page backed by a client-side service, not an open dataset, and was not scraped. | G6 |
| N-13 | No Word documents were found in any examined source. Documents are PDF with WB-generated TXT. | Inventory D3; G6 |

## 5. Corrections made to Phase 1 reports

| Report | Correction | Reason |
|---|---|---|
| ML_FEASIBILITY_REPORT | §3: plan cancellation 18.2% → **30.8%**; "blank 42%" removed; re-bidding re-graded to *conditionally feasible (probabilistic candidate labels)* | N-5, N-6 |
| ML_FEASIBILITY_REPORT | §4b / §6: "temporal label requires parsing ISR documents" → **incorrect**; procurement-rating history is not in public ISRs | N-1 |
| RAG_DATA_FEASIBILITY | §4 plan statistics; ISR layouts; long-document distribution; tender-design substitutes | N-2, N-5, N-9, N-11 |
| SCHEMA_AND_DATA_PROFILING | Issue #21 resolved (header merge); new issues #23–#29 | N-2, N-3, N-5, N-10 |
| DATA_SOURCE_INVENTORY | Added OKR, Procurement Regulations, CPF / SCD / IEG Evaluation / Aide Memoire, debarment page, E&S and co-financing fields | N-7, N-8, N-12 |
| ENTITY_RELATIONSHIP_FEASIBILITY | Sector / temporal linking measured; evaluation-supersession relationship; ISR entity | N-4, N-10 |
| METADATA_ASSESSMENT | E&S risk, co-financing, PDO, evaluation precedence fields; sector field guidance | N-4, N-7, N-8, N-10 |
| DATA_REFRESH_ASSESSMENT | ISR duplicates; IEG supersession; API ISR ratings must be snapshotted | N-1, N-3, N-4 |
| DISCOVERY_SUMMARY | Alignment addendum | All |

Every corrected passage is marked "**Corrected 2026-10-21**" or carries an "Alignment update (2026-10-21)" section. Phase 1
text that is not wrong was left unchanged. The Phase 1 plan summary is preserved as `profiling/procurement_plan_summary_phase1.json`.

## 6. Implications for the proposed architecture

1. **Evidence precedence must be a first-class model concept.** The platform has to store multiple evaluations per project
   (ISR series → ICR → ICRR → PPAR) with type, date and source. It should resolve displays via an explicit, approved
   precedence policy, while always exposing the conflicting values. Source datasets overwrite history, so the platform must
   keep it.
2. **Contextual links ≠ factual links.** The assessment's contract → lesson example (via sector + time) maps to a *scored,
   class-P contextual-relevance link* with `evidence_scope = project`. A KG topology can represent this naturally. If
   relational, a typed link table with score, method and scope is needed. Either way, this is decision D-19.
3. **Procurement plans are the backbone for planning and tender-design features**: market approach, estimated amounts,
   planned vs actual dates, cancellation and re-procurement. They justify a dedicated, versioned extraction component with
   header normalisation and multilingual vocabularies.
4. **ISR handling:**
   - API values are the only public source of component ratings, so they must be **snapshotted** from now on.
   - ISR documents provide PDO / IP / risk history, after de-duplication by (project, sequence number).
   - PDF-table parsing is preferable to text renditions.
5. **Reference-data layer grows:** country crosswalk (Phase 1), plus a GP ↔ sector crosswalk, a two-scheme E&S
   classification, and multilingual plan-status / method / review-type vocabularies (*A posteriori*, *Demande de prix*, …).
6. **Scope boundaries to state in the proposal:**
   - Amendment prediction and specification comparison are not supportable from public data.
   - Re-bidding is supportable only with probabilistic, validated labels.
   - Supplier performance is not observable.
7. **Long-document strategy is mandatory, not optional:** about half of PADs exceed 100 pages, with no bookmarks. A
   hierarchical, table-aware design is required (RAG §5).

## 7. Recommended actions before Phase 2

| # | Action | Type | Depends on |
|---|---|---|---|
| 1 | Architect review of this report + decisions D-01…D-25 | Governance | — |
| 2 | Product-owner decision on unsupported requirements (amendments, specifications, supplier performance) and their substitutes | Scope | 1 |
| 3 | Population-level contract ↔ notice join (partitioned procnotices crawl, ≈ 425 calls) | Read-only discovery | 1 |
| 4 | Procurement-plan extraction pilot (~500 plans incl. versions) with 200 manually validated cancelled → successor pairs | Discovery pilot | D-11 |
| 5 | Long-document parsing / embedding evaluation on ≥ 50 PADs ≥ 100 pages + ICR / ICRR (EN / FR / ES), with a hand-labelled section / citation set | Discovery pilot | D-16, D-19 |
| 6 | Build gold sets: (a) retrieval relevance for ~50 procurement planning queries; (b) citation faithfulness. Requires procurement SMEs. | Evaluation design | D-23 |
| 7 | Start **snapshotting** volatile public values (v3 `isr_ratings`, `ieg_ratings`, DS00053, DS00005), since history is otherwise lost | Requires approval (storage) | D-15, D-20 |
| 8 | Curate the guidance corpus (Procurement Regulations, guidance notes, SPD templates) as a versioned list | Read-only | D-17 |

## 8. Decisions requiring architectural approval

The existing **D-01 … D-17** remain open (see [`docs/decisions/PENDING_DECISIONS.md`](../decisions/PENDING_DECISIONS.md)).
D-17 is revised. New items added in this review:

| ID | Decision |
|---|---|
| D-17 (revised) | Tender-design scope: specifications are not public. Accept substitutes (REOI ToR text, IFB qualification criteria, plan packaging, award rejection reasons, Procurement Regulations), or descope. |
| D-18 | Evidence precedence & conflict policy (PPAR > ICRR > ICR self-rating > latest ISR; recency rules; always surface conflicts) |
| D-19 | Knowledge representation: property graph / KG vs relational with typed link tables for contextual (class-P) links |
| D-20 | Begin periodic snapshots of v3 ISR / ICR / IEG ratings (only public source of component-rating history) |
| D-21 | Ownership of a Global Practice ↔ sector crosswalk |
| D-22 | Re-bidding target definition using probabilistic plan-based labels (validation threshold before use) |
| D-23 | KPI baselines and gold-set creation with procurement SMEs |
| D-24 | Debarment / sanctions data: use only if an official open dataset or documented API exists; no scraping of client-side services |
| D-25 | OKR inclusion and de-duplication against WDS by GUID |
