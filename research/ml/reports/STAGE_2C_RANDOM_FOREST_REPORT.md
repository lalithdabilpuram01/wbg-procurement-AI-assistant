# Model 1, Stage 2C: Random Forest Regressor (validation only)

**Date:** 2026-10-25 · **Pre-registration:** `ml/stage2c/PREREGISTRATION.json`. SHA-256 locked in `PREREGISTRATION.sha256` before any Random Forest fit; the script refuses to run if the file changes. **The test set was not scored.**

**Runs:**
- **Audit run:** `ml/eda/stage2c_asof_b1_ridge_rf_20261025T075623Z`.
- **Final runs:** `ml/eda/stage2c_asof_b1_ridge_rf_xgb_20261025T075740Z` and `ml/eda/stage2c_cfw365_b1_ridge_rf_xgb_20261025T075905Z`. All models are deterministic, and the RF numbers are identical in the audit and final runs.

**Code:** `ml/stage2c/advanced.py`. **Saved artefacts:** `models/rf_<protocol>.joblib` (the full preprocessing + model pipeline) and `MODEL_CARD.json`.

## Setup

**Inputs:**
- **Data:** the same 353 validation contracts and the same features and train-only preprocessing as Stage 2B; target ln(days).
- **Training protocols:** `asof` (reference, 462 rows) and `cfw365` (complete-follow-up window, 372 rows). cfw365 was first examined post hoc in Stage 2B; it is now frozen and no other window was tried.

**Tuning and long-tender scoring:**
- **Tuning:** pre-declared grid of 36 (max_depth {4, 6, 10, None} × min_samples_leaf {5, 10, 20} × max_features {0.3, 0.6, 1.0}), 500 trees, seed 20261025, 5-fold GroupKFold by project, CV metric MAE in days.
- **Long-tender score:** P(duration > 365) from a log-normal, using the model's out-of-fold residual SD.
- **Alert threshold:** the training out-of-fold F1 maximum with an alert rate of at most 30%.

## Results (validation, n = 353; 45 long tenders)

| | As-of: B1 | As-of: Ridge | **As-of: RF** | cfw365: B1 | cfw365: Ridge | **cfw365: RF** |
|---|---:|---:|---:|---:|---:|---:|
| Chosen hyperparameters | – | α = 10 | depth None, leaf 5, max_features 1.0 | – | α = 31.6 | depth None, leaf 5, max_features 0.6 |
| Training MAE (in-sample) | 81.1 | 66.0 | **49.7** | 86.0 | 72.4 | **53.1** |
| CV MAE (training, grouped) | 91.3 | **75.9** | 82.0 | 96.5 | **83.8** | 84.1 |
| **Validation MAE** | 77.5 | 75.8 | **70.7** | 79.6 | **74.7** | 75.6 |
| 95% CI (project clusters) | 61.8–95.5 | 61.2–91.0 | 57.9–85.0 | 66.0–95.5 | 61.9–87.5 | 65.6–85.8 |
| MAE reduction vs B1, CI project / package | – | −5.6..9.0 / −4.0..7.4 | −1.4..16.4 / **1.1..12.5** | – | −4.3..15.5 / −1.8..12.1 | −5.4..16.3 / −2.8..11.4 |
| MAE reduction vs Ridge, CI project / package | – | – | **0.8..9.5 / 1.4..8.7** | – | – | −6.9..5.2 / −5.3..3.6 |
| Median absolute error / RMSE | 47.0 / 116.1 | 47.5 / 113.0 | 42.9 / 107.0 | 56.0 / 113.8 | 52.2 / 108.7 | 54.9 / 104.1 |
| Package-weighted MAE | 76.2 | 77.4 | 72.4 | 77.6 | 74.1 | 75.3 |
| Unseen projects / seen projects | 71.5 / 80.7 | 67.8 / 80.1 | 67.6 / **72.3** | 82.7 / 76.1 | 78.0 / 71.1 | 81.0 / 69.4 |
| Prior review: MAE (bias) | 168 (−148) | 146 (−123) | 133 (−104) | 158 (−135) | 127 (−86) | 123 (−54) |
| QCBS (n = 45) MAE | 151 | 169 | 149 | 152 | 161 | 139 |
| Long (> 365 d) bias | −223 | −245 | −209 | −207 | −223 | −183 |
| P10–P90 coverage (nominal 0.80) | – | 0.84 | 0.88 | – | 0.80 | 0.83 |
| Long detection: PR-AUC | 0.38 | 0.45 | **0.58** | 0.38 | 0.50 | **0.56** |
| Primary alert: recall / precision / F1 | 0.53 / 0.52 / 0.53 | 0.69 / 0.48 / 0.57 | 0.60 / 0.59 / **0.59** | 0.53 / 0.51 / 0.52 | 0.60 / 0.44 / 0.51 | 0.64 / 0.40 / 0.50 |

## Audit

1. **Overfitting:** strong. Training MAE is about 50 days against a CV MAE of about 82–84. The chosen min_samples_leaf (5) is the **smallest value in the pre-declared grid**, so CV preferred the least-regularised trees on offer. The grid was not extended; that would breach the budget and invite validation-driven search.
2. **Contradictory evidence:** inside training CV, RF is **worse than Ridge** in both protocols (82.0 vs 75.9; 84.1 vs 83.8). The 5-day validation gain (as-of) is therefore not supported by training-only evidence.
3. **Where the gain comes from:**
   - Projects already seen in training: 72.3 vs Ridge 80.1.
   - Unseen projects: about the same as Ridge (67.6 vs 67.8).
   - Consistent with RF fitting project and country structure (country_grp has non-zero importance).
4. **Protocol dependence:** under cfw365 the advantage disappears (75.6 vs Ridge 74.7, CI −6.9..5.2). RF's as-of win does not survive a change of training protocol.
5. **Long tenders:**
   - RF has the best ranking of long tenders (PR-AUC 0.58 / 0.56) and the smallest under-prediction among the regression models.
   - It still under-predicts long tenders by about 180–210 days. No model solves the long tail.
6. **By method (as-of):**
   - QCBS long recall 0.91 at 0.60 precision.
   - RFB long recall 0.29.
   - CQS and individual consultants have essentially no long tenders (2 each).
7. **Regions (as-of):** better in ESA (63 vs 71), South Asia and WCA; worse in MENA (87 vs 76); LAC unchanged at about 143. These segments are small.
8. **Importance** (validation permutation, MAE increase in days, as-of): method 26.6, bid window 11.8, review type 7.0, country 2.0, project age 1.3, sector 1.2. All other features are below 0.5.
9. **Leakage:** same feature policy as Stage 2B, and nothing post-publication. The alert threshold comes only from training out-of-fold scores.

## Conclusion

Random Forest has the **lowest as-of validation MAE (70.7)** and the **best long-tender ranking**. The improvement is **not robust**:
- it is contradicted by training CV;
- it is concentrated in projects seen in training;
- it vanishes under the alternative protocol;
- the model is heavily overfitted.

**Not adopted as the primary model. Kept as the strongest challenger.**
