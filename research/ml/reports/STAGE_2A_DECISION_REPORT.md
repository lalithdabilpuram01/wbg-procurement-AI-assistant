# Model 1, Stage 2A: Baselines Decision Report

**Date:** 2026-10-25 · **Scope:** tender-duration baselines B0 / B1, scored on **validation only**. The test set was not scored.

**Run:** `ml/eda/stage2a_baselines_20261025T072603Z/`
- `baseline_metrics.json`
- `segments_by_*.csv`
- `validation_predictions.csv`
- `charts/01–05`

**Tracking:** 6 records in `ml/experiments/runs.jsonl` (local JSONL, as instructed).

**Code:** `ml/stage2a/baselines.py`, with the split from `ml/stage2a/split.py`.

**These are validation scores, not final generalisation performance.**

## 1. Did the split pass the audit?

**Yes: 10 of 10 checks.** See `STAGE_2A_SPLIT_AUDIT_REPORT.md`.
- 1,615 unique contracts (646 / 353 / 616).
- 0 duplicate IDs; 0 packages crossing splits.
- Method and category agree with the tender notice in 99.9–100% of rows.
- Every row has at least 733 days of follow-up.
- Project overlap is moderate and reported.

## 2. Baselines

- **B0:** the training median, **177 days**.
- **B1:** the median by method × category, with fallback method × category → method → category → B0. The minimum group size k was chosen by 5-fold project-grouped CV **inside training**: k = 1. The CV curve is flat (MAE 118.5 at k = 1 vs 122.0 at k = 50), and all 353 validation rows were served by a method × category cell.
- **B1m:** the method-only median (k = 1 by the same procedure).

## 3. Validation results (n = 353; 60 projects; 276 packages)

| Metric | B0 | B1 | B1m |
|---|---:|---:|---:|
| MAE (days) | 104.6 | **82.2** | 80.9 |
| 95% CI (project-cluster bootstrap, 60 clusters) | 88.1–122.8 | 68.2–97.4 | 67.9–95.6 |
| Median absolute error | 79.0 | 66.5 | 61.0 |
| RMSE | 143.0 | 113.9 | 113.2 |
| Mean bias (predicted − actual) | −5.9 | −3.9 | −5.8 |
| Absolute error p75 / p90 / p95 | 121 / 209 / 293 | 103 / 200 / 241 | 104 / 201 / 256 |
| Within 30 days of actual | 12.5% | 31.4% | 30.6% |
| Within ±50% of actual | 43.1% | 58.6% | 61.5% |
| MAE, package-weighted | 99.3 | 80.0 | 78.7 |

**B1 improvement over B0:**
- MAE 21.4%, median absolute error 15.8%, RMSE 20.4%.
- Paired MAE reduction: 95% CI **10.4–33.5 days** (project clusters) and 14.0–31.5 days (package clusters). It is above zero in every one of the 2,000 resamples.

**Sensitivities:**

| Variant | MAE (days) | Note |
|---|---:|---|
| Package-deduplicated B1 | 80.9 | Lots do not drive the result |
| Strictly as-of (training outcomes signed by 2022-06-30 only; 462 rows) | B0 97.8, B1 77.5 | |

The as-of variant looks *better* only because it drops slow training tenders: its median is 150 days, nearer the validation period's shorter durations. It is not a better method, but it does signal **temporal drift**.

## 4. Does B1 meaningfully improve on B0?

**Yes.** The improvement is about 22 days of MAE, and its confidence interval excludes zero under both project and package clustering.

However, **B1m (method only) is as good as B1**: the category dimension adds nothing on validation. B1 stays the pre-declared reference baseline for Stage 2B.

## 5. Error patterns and representativeness

**Typical behaviour:** the baselines compress towards the group median, so they under-predict long tenders and over-predict short ones (chart `02_residuals.png`).
- **Long tenders** (actual above the training p75 of 321 days; n = 52): B1 under-predicts 81% of them, MAE 196. For tenders over 365 days (n = 45), MAE is 211.
- **Short tenders** (below the training p25 of 102 days; n = 106): B1 over-predicts 98%, MAE 74.

**By method:**

| Method | n | B0 MAE | B1 MAE |
|---|---:|---:|---:|
| Individual consultants | 116 | 73 | **48** |
| RFB | 124 | 93 | 91 (B1 adds almost nothing) |
| QCBS | 45 | 230 | 153 (bias −15) |
| CQS | 50 | 92 | 84 |
| RFQ | 11 | 104 | 48 (sparse) |
| LCS / FBS / QBS / RFP | 1–4 each | – | Unreliable; FBS and RFP rest on 1–2 training contracts (k = 1) |

**Review type (not used by B1):**

| Review | n | B1 MAE | B1 bias |
|---|---:|---:|---:|
| Prior | 62 | 149 | **−129** (large under-prediction) |
| Post | 291 | 68 | +23 |

This is the largest systematic error that an available feature could explain.

**By category:** Consulting 78, Goods 89, Works 89 (bias −50), Non-consulting (n = 5) 76.

**By region:**
- East Asia & Pacific (n = 21): 115.
- West & Central Africa (n = 50): 101, bias −30.
- LAC (n = 20): 134, bias −82.
- South Asia: 54.
- Countries with n ≥ 20: Mozambique 93 (n = 60, 3 projects), Comoros 66 (one project, bias +66), Bangladesh 50, India 58, Kyrgyz Republic 66. The rest are sparse (`segments_by_project_country_name.csv`).

**Project concentration:**
- The top 5 validation projects hold 128 of 353 rows. MAE excluding them: B0 103.2, B1 86.7.
- Per-project B1 MAE ranges from 35 to 117.
- Rows in **new projects** have B1 MAE 75.7 (n = 100), against 84.7 in projects seen in training. Baseline accuracy does not depend on project overlap.

**Lots:** contracts in multi-contract packages have B1 MAE 97.0, against 75.0 for single contracts.

**Temporal instability:**
- Training medians by FY: 176.5 / 166 / 190. The validation median is 129.
- B1 MAE by validation quarter: 77 / 82 / 95 / 77.
- Durations are getting shorter over time. A static historical median is biased upward for newer tenders.

**Sanity:** all predictions are strictly positive. B1 ranges from 35 to 645.5 days.

**Representativeness:** results apply only to advertised methods in the 184 reference-linked projects (Phase 1 limits), with very few Latin American labels. They must not be generalised to the cohort or to direct contracting.

## 6. Changes to the synthetic-data method

Documented in `ML_SYNTHETIC_DATA_DESIGN_REVIEW_STAGE2A.md`; nothing generated. In summary:
- generator inputs restricted to prediction-time features in the `include` class;
- contract amount moved to an optional S-amt scenario;
- "simulated prevalence" labelling;
- S0 genuinely null (no random effects, a separate random stream, an AUC ≈ 0.5 acceptance test);
- latent effects isolated, with grouped and project-held-out evaluation;
- three separated artefacts;
- interpretation guard;
- an optional drift scenario.

## 7. Is Stage 2B (OLS, Ridge, Lasso, Elastic Net) justified?

**Yes, conditionally.** B1 leaves systematic, feature-explainable error:
- prior review bias of −129 days;
- a published bid-window association (rho 0.31 in the EDA);
- regional and country offsets;
- a downward time trend.

A regularised linear model on ln(days) can use these, where B1 cannot.

**Proposed conditions for Stage 2B:**
1. **Reference to beat:** B1 (MAE 82.2; CI 68–97). A linear model counts as better only if the paired MAE reduction CI excludes zero.
2. **Fixed inputs:** primary features only (inventory `include`), with preprocessing fitted on training folds. Countries with fewer than 20 rows are grouped. Sector taxonomy prefixes are normalised.
3. **Tuning:** project- and package-grouped CV inside training for α and l1_ratio. Back-transform with a smearing estimate.
4. **Drift:** decide whether to include the tender FY trend term, an `investigate`-class feature, as a declared variant. Report it separately, because it extrapolates.
5. **Reporting:** the same segment, cluster-bootstrap and residual reporting as here. The test set stays unscored until Stage 6.
