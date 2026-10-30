# Model 1 (Tender Duration): Final Held-Out Test Report

**Date:** 2026-10-25 · **Models:** B1, Ridge (primary candidate) and Random Forest (pre-declared challenger) · **Test set:** 616 contracts, scored **once** (2026-10-25T08:15:52Z).

## 1. Protocol frozen before any test target was read

| Item | Value |
|---|---|
| Configuration | `ml/final/FINAL_EVAL_CONFIG.json` (SHA-256 `b3e7e8c9…`) |
| Refit script | `ml/final/final_refit.py`. Reads train and validation targets only; writes test *predictions* from test features alone |
| Freeze record | `ml/final/frozen/FROZEN.json` (SHA-256 of the config, labels, features, `split.py`, all 3 models, the predictions and the refit details) |
| One-time scorer | `ml/final/final_score.py`. Verifies every hash, then writes the lock `ml/final/frozen/TEST_SCORED.json` and refuses to run again |
| Historical cutoff | 2023-06-30 |
| Refit rows (audited before test) | **806** = 615 train + 191 validation rows signed by 2023-06-30. 101 projects, 722 packages, 98 longer than 365 days. Overlap with test: 0 contracts, 0 packages |
| Features / preprocessing | The approved `include` set (Stage 2B), fitted inside training folds |
| Hyperparameters | Existing grids, 5-fold GroupKFold by project. **Ridge** α = 31.6. **Random Forest:** depth unlimited, max_features 0.3, min_samples_leaf 5 (the smallest leaf size in the grid) |
| Long-tender alert thresholds (from training out-of-fold scores, alert rate ≤ 30%) | B1 0.25, Ridge 0.287, RF 0.382 |
| Seed | 20261025 |

**Execution notes:**
- **Smoke test first:** the scorer was run on validation rows using the frozen models. That run read no test rows and wrote no lock.
- **One aborted attempt:** the first real scoring run stopped with a key-type error (contract IDs read as integers) **while joining predictions to targets, before any metric was computed or shown**. The fix only reads the IDs as text; the frozen files and hashes were unchanged. The second run is the one-time evaluation reported here.

**Test set:**
- 616 contracts in 59 projects and 374 packages.
- First tenders published 2023-07-01 → 2024-10-20.
- 64 long tenders (10.4%).
- **The top 5 projects hold 324 rows (52.6%).**

## 2. Duration prediction

| | B1 | Ridge | Random Forest |
|---|---:|---:|---:|
| **MAE (days)** | 74.3 | 76.7 | **73.5** |
| 95% CI, project clusters | 60.0–92.5 | 62.7–89.3 | 54.3–92.6 |
| 95% CI, package clusters | 64.6–85.4 | 68.6–84.9 | 61.8–84.4 |
| Median absolute error | 55.0 | 62.4 | **47.6** |
| RMSE | 108.0 | 106.5 | **105.1** |
| Mean bias (predicted − actual) | **−27.3** | −37.3 | −43.0 |
| Package-weighted MAE | 84.1 | 78.9 | **77.1** |
| P10–P90 interval coverage (nominal 0.80) | – | **0.84** (median width 242 d) | 0.77 (206 d) |
| MAE improvement vs B1 | – | −3.1% | +1.2% |
| Paired MAE reduction vs B1, CI project / package | – | −11.8..12.4 / −11.4..7.4 | −8.6..16.5 / −7.4..9.2 |
| Paired reduction RF vs Ridge, CI project / package | – | – | −6.8..14.7 / −2.5..10.6 |

**No pairwise MAE difference is statistically reliable.** All point estimates are within 3.2 days of each other.

## 3. Long-duration detection (> 365 days; 64 positives; frozen thresholds)

| | B1 | Ridge | Random Forest |
|---|---:|---:|---:|
| TP / FN / FP / TN | 38 / 26 / 35 / 517 | 25 / 39 / 20 / 532 | 19 / 45 / 9 / 543 |
| **Recall** | **0.59** | 0.39 | 0.30 |
| **Precision** | 0.52 | 0.56 | **0.68** |
| **F1** | **0.56** | 0.46 | 0.41 |
| F1 95% CI, project / package clusters | 0.36–0.66 / 0.41–0.67 | 0.24–0.59 / 0.32–0.58 | 0.22–0.53 / 0.27–0.56 |
| Alert rate (training out-of-fold rate in brackets) | 11.9% (15.8%) | **7.3% (15.5%)** | **4.5% (9.1%)** |
| PR-AUC / ROC-AUC | 0.37 / 0.77 | 0.54 / 0.91 | **0.56 / 0.92** |
| Point prediction > 365 days: recall / precision | 0.02 / 0.33 | 0.13 / 0.73 | 0.08 / 0.45 |
| Top 20% of scores (124 alerts): recall / precision | 0.67 / 0.35 | 0.83 / 0.43 | **0.86 / 0.44** |

**Reading the results:**
- **Ranking:** Ridge and RF rank long tenders far better than B1 (PR-AUC 0.54 / 0.56 vs 0.37; ROC-AUC 0.91 / 0.92 vs 0.77).
- **Thresholds:** their training-derived thresholds **transfer poorly**. Durations shorten over time, so test probabilities are lower, and the alert rate halves (Ridge 15.5% → 7.3%; RF 9.1% → 4.5%). Recall falls accordingly.
- **B1's threshold** transfers better, because it alerts on cell membership (mostly QCBS).
- **At a fixed 20% review capacity,** Ridge catches 53 of 64 long tenders and RF 55, against 43 for B1.
- **The trade-off:** raising recall from about 0.4 to 0.8 would roughly triple the alerts (45 → about 124) and drop precision from 0.56 to 0.43. Charts: `02_test_pr_curves.png`, `03_test_confusion_matrices.png`.

## 4. Reliability and representativeness

| Segment | n | B1 | Ridge | RF | Note |
|---|---:|---:|---:|---:|---|
| Long tenders (> 365 d), MAE / bias | 64 | 194 / −189 | 209 / −207 | 197 / −195 | All models under-predict long tenders by about 190–210 days |
| Short (< refit p25), MAE | 127 | 60 | 42 | **38** | |
| Prior review, MAE (bias) | 83 | 147 (−109) | 117 (−73) | **108 (−57)** | Models use review type; B1 cannot |
| Post review, MAE | 533 | **63** | 71 | 68 | |
| QCBS, MAE (bias) | 69 | **100 (−24)** | 139 (−100) | 121 (−80) | |
| RFB, MAE | 256 | 74 | **65** | 69 | |
| Projects seen in refit | 404 | **82.7** | **82.7** | 88.3 | |
| **Projects unseen in refit** | 212 | 58.4 | 65.3 | **45.3** | |
| **Top 5 projects only** | 324 | **62.9** | 80.5 | 79.6 | The largest project (85 rows, 0 long): B1 36.2, Ridge 71.9, RF 36.6 |
| **Excluding the top 5 projects** | 292 | 87.1 | 72.4 | **66.7** | |
| Projects where the model has the lowest MAE | 59 | 18 | 17 | 25 | |

- **Regions:** B1 is best in ESA (378 rows, mostly the top projects). Ridge and RF are better in ECA, WCA, LAC, MENA and EAP (each 24–54 rows).
- **Quarters:** no consistent winner. The 2024Q2 quarter favours B1 (60 vs 84–85 days).
- **Categories:** Ridge and RF are better for Goods, Works and Non-consulting; B1 is better for Consulting (72 vs 79–88).

**Interpretation:**
- **Concentration drives the headline.** B1's overall parity comes from the five largest projects (above all one 85-contract project where Ridge over-predicts by about 36 days on average).
- **On the remaining 292 contracts in 54 projects,** Ridge and RF beat B1 by 15–20 days. This is a post-hoc descriptive split, not a pre-declared test.

## 5. Independent audit

| Check | Result |
|---|---|
| No test rows in preprocessing, tuning, model selection or thresholds | **Pass.** `split.load()` strips test targets; the refit used train + validation only; test predictions were frozen and hashed before targets were read. Random Forest has no early stopping |
| No duplicate contracts or shared packages across splits | **Pass.** Stage 2A audit (0 / 0) and refit audit (0 contracts, 0 packages overlapping the test) |
| No future or outcome-derived inputs | **Pass.** Same Stage 2A-audited feature set (method and category agree with the notice 99.9–100%; prior counts computed as-of). Outcome columns are absent from the test feature frame |
| Historical outcome availability respected | **Pass.** Refit rows all signed by 2023-06-30 (latest 2023-06-29); latest first tender 2023-06-16 |
| Test evaluated once | **Pass, with disclosure.** One aborted attempt failed at the join before any metric was computed. The lock file prevents re-scoring |
| Uncertainty and selection bias disclosed | See §6 |

## 6. Disclosures and limitations

- **Not fully blind:** Phase 1 diagnostics printed the median of the test window (141.5 days), and the Phase 1 EDA showed medians for overlapping fiscal years.
- **Selection bias:** Ridge and RF were chosen as candidates after validation results were known, and validation was reused across Stages 2A–2D.
- **Population:** linked, observed, **signed** contracts from advertised tenders in the 184 reference-linked cohort projects. **Not** all published tenders, not cancelled tenders, and not the World Bank portfolio. About 39% of packages never link (Stage 2E).
- **Concentration:** the top 5 projects hold 52.6% of the test. Results depend heavily on a few projects (one project shifts Ridge's MAE by about 5 days).
- **Statistics:** MAE CIs are about ±15 days. No model is reliably more accurate than B1.
- **Long tail:** every model under-predicts tenders over 365 days by about 190–210 days.
- **Geography and time:** LAC has 39 test rows and EAP 24. The test covers tenders from 2023-07 to 2024-10 only; durations have been shortening over time.

## 7. Files

All under `ml/final/frozen/`:
- `test_results/final_test_metrics.json`
- `test_results/segments_by_*.csv` (including `segments_by_project_id.csv` for all 59 projects)
- `test_results/test_predictions_with_outcomes.csv`
- `test_results/charts/01–06`: MAE CI, PR curves, confusion matrices, predicted vs actual, bias by band, top-10 projects
- `models/{b1,ridge,rf}_final.joblib`
- `refit_details.json`, `FROZEN.json`, `TEST_SCORED.json`

**Tracking:** `ml/experiments/runs.jsonl` (stage `final_test`).
