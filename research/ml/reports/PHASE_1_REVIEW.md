# Module 2: Phase 1 Review (ML data readiness and EDA)

**Date:** 2026-10-25 · **Outcome:** Phase 1 complete. **No models trained; no synthetic labels generated.** Awaiting approval before Stage 2.

## 1. Work completed

| Task | Status | Evidence |
|---|---|---|
| Review existing research (assessment, alignment report, ML feasibility, label audit, Silver/Gold reports, decisions) | Done | Cited in each report |
| Task 1: prediction problems for 3 models | Done; prediction points **proposed** | `ML_DATA_READINESS_REPORT.md` §2 |
| Task 2: tender-duration label validation (cohort) | Done, **measured** | `ml/eda/tender_labels_20261025T070133Z/` |
| Task 3: amendment and re-bidding evidence re-check | Done, **measured** (Silver) plus historical items marked | `ml/eda/label_evidence_20261025T070231Z/` |
| EDA (overview, numeric, categorical, relationships, quality / bias, 17 charts) | Done, real data only | `ML_EDA_REPORT.md`, `ml/eda/eda_20261025T070501Z/` |
| Feature inventory and point-in-time audit (70 entries across 3 models) | Done | `ML_FEATURE_INVENTORY.csv` |
| Preprocessing design | Done | `ML_EXPERIMENT_PLAN.md` §1 |
| Synthetic outcome design | Design only | `ML_SYNTHETIC_DATA_DESIGN.md` |
| Experiment, validation and tracking plan | Done (not executed) | `ML_EXPERIMENT_PLAN.md` |
| MLflow availability | Verified (read-only API call) | `ML_EXPERIMENT_PLAN.md` §5 |

## 2. Evidence of validation

**Read-only by construction:**
- All Databricks access goes through `ml/phase1/common.py`, which refuses anything other than SELECT / WITH / SHOW / DESCRIBE.
- Delta versions of the 10 tables read were **identical before and after**: Gold contract analytics v73, notices v68, portfolio v69, country context v52; Silver contract_awards v12, procurement_notices v30, notice_details v39, link_candidates v15, links v20, projects v27.
- Cohort manifest SHA-256: `82a43185…dac940`.

**Label linking cross-check:** the Silver tender candidate pairs and an independent re-derivation give the same set (2,496 = 2,496, 0 differences).

**Reproduced historical counts:** every p09 evidence count recomputed on Silver matched exactly: 56 · 654 · 221 · 142 · 4,002 · 11 · 8 · 89 · 140 · 2,564.

**Sensitivity:** changing the deadline source, or adding award-text references, leaves the valid count at 2,114.

**Unchanged:**
- No existing report, manifest, pipeline or table was modified.
- All outputs are new files under `ml/`.
- Two of my own superseded run folders (a failed extract, plus first runs before the truncation, income-year and caption fixes) were deleted and re-run. No earlier project artefacts were touched.

## 3. Key results

**Tender-duration labels:**
- **2,114 valid real labels** in 145 projects: 13.9% of the contracts in the 184 projects with references, 1.18% of the cohort.
- **1,615** of them have a full two-year follow-up; this is the recommended modelling set.
- The earlier 3,782 (276-project sample) and "≈ 1,271" (Gold report) figures are **not** cohort counts and are superseded.

**EDA:**
- Duration median 144 days (p25–p75: 92–251), right-skewed. Model ln(days).
- Method and review type dominate: QCBS 375 days vs RFQ 72; prior review 324 vs post 123.
- Bid window is the strongest numeric signal (rho 0.31).
- Governance and macro signals are weak (|rho| ≤ 0.19) and current-vintage.

**Biases:**
- Advertised methods only (Direct 0%, RFQ 2.9%).
- Linkable for 184 projects only; LAC coverage 3.9% (Bolivia 1 of 2,474).
- The top 10 projects hold 36% of labels.
- Left and right truncation by the DS00005 window and the snapshot.

**Amendment:**
- 0 confirmed positives, 0 confirmed negatives.
- 56 + 5 candidates; only 2 link to an original through a quoted reference (13 through the same supplier, historical).

**Re-bidding:**
- 0 confirmed; no negative rule.
- Candidates are partly false positives: 19 of 100 API candidates are in known false-positive contexts.

## 4. Features

**Ready for primary experiments, Model 1:**
- method, category, review type, first notice type, published bid window;
- region, country (grouped), primary sector (normalised);
- project age, approval FY, prior tender notices (as-of);
- income group at tender FY (point-in-time).

Models 2 and 3 have equivalent sets, listed in the inventory.

**Excluded for leakage or privacy:**
- signed amount (Model 1);
- supplier fields and identifiers;
- lot / notice counts to the snapshot;
- award-notice dates and links;
- snapshot-wide reference sharing;
- ISR / IEG / ICR ratings;
- notice status and versions;
- re-bid markers and amendment wording (label sources);
- bidder counts;
- later plan versions.

**Investigate (sensitivity runs only):**
- WDI / WGI / CPIA at t−2 (current vintage);
- project commitment (snapshot);
- tender FY trend;
- contract amount, JV and supplier-country relation for Model 2 (DS00005 snapshot).

**Unavailable for the cohort:** plan estimated amount, market approach and planned dates (need plan extraction at scale, D-11).

## 5. Outstanding issues

1. Prediction points and the contract- vs package-level target need approval.
2. Bolivia / LAC: the cause of the label gap is not yet investigated.
3. No point-in-time vintages for WDI / WGI / CPIA. Archived vintages would need a separate approval (download).
4. Whether DS00005 amounts are revised in place is unknown (needs D-20 snapshots).
5. The Model 1 training slice is small (646 labels); expect wide confidence intervals.
6. The local `mlflow` client is not installed.
7. Local working data: `ml/eda/extract_*/data/*.parquet` (public data, no free text or identifiers) is a cache. It should be excluded from Git if repository setup resumes (not done; `.gitignore` untouched).

## 6. Recommended next phase

**Stage 2 baselines**, after approval:
- **Model 1:** global and method × category median baselines on the 646 / 353 / 616 temporal split (primary features only).
- **Models 2 and 3:** generate the scenario grid S0–S5 per the synthetic design, then class-prior and rule baselines.

## 7. Approval needed before training

| # | Decision |
|---|---|
| A1 | Model 1 target: notice-to-signing, contract level, prediction at tender publication; scope limited to advertised methods in the 184 reference projects |
| A2 | Model 1 modelling set: tenders published 2019-07-01 to 2024-10-20 (1,615 labels); split 646 / 353 / 616; left-truncated (56) excluded; right-truncated (443) for monitoring and survival checks |
| A3 | Use synthetic outcomes for Models 2 and 3 per `ML_SYNTHETIC_DATA_DESIGN.md`, including prevalence scenarios and driver directions (domain assumptions) |
| A4 | Feature policy: `include` only for primary experiments; current-vintage indicators in labelled sensitivity runs only |
| A5 | Experiment tracking: install `mlflow` locally and create 3 experiments under `/Shared/wbg_module2/`, or use the local JSONL fallback |
| A6 | Optional, in parallel: human verification of the re-bidding candidates (100 API + 123 DS00979 pairs + 38 plan pairs) to build a small real plausibility set (D-22 / D-23) |
