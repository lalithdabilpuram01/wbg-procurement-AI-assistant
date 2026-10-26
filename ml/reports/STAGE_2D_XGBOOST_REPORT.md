# Model 1, Stage 2D: XGBoost Regressor (validation only)

**Date:** 2026-10-25 · **Runs:** `ml/eda/stage2c_asof_b1_ridge_rf_xgb_20261025T075740Z` and `ml/eda/stage2c_cfw365_b1_ridge_rf_xgb_20261025T075905Z`. Same pre-registration, protocols, validation rows and metrics as Stage 2C. **The test set was not scored.**

**Artefacts** in each run:
- `models/xgb_<protocol>.joblib` (preprocessing + booster);
- `shap_values_xgb_validation.csv`, `shap_mean_abs_by_feature_xgb_validation.csv`;
- `permutation_importance_xgb_validation.csv`;
- `charts/05_importance_xgb.png`.

## Setup

**Search** (pre-declared):
- 30 random configurations (seed 20261025) over depth {2, 3, 4}, learning rate {0.03, 0.05, 0.1}, min_child_weight {5, 10, 20}, subsample {0.7, 0.9}, colsample {0.5, 0.8}, λ {1, 5, 10}, α {0, 0.5}.
- Squared error on ln(days); `hist` trees.

**Number of trees, from training data only:**
- Averaged per-iteration RMSE across the 5 project-grouped CV folds, with patience-50 early stopping on that curve, at most 1,000 trees.
- No configuration hit the cap.
- The final model is refitted on all training rows with that number of trees.

**Selection:** CV MAE in days. Preprocessing is fitted inside each fold.

## Results (validation, n = 353)

| | As-of | cfw365 |
|---|---|---|
| Chosen configuration | depth 2, learning rate 0.1, 141 trees, min_child_weight 10, subsample 0.9, colsample 0.5, λ 1, α 0.5 | depth 4, learning rate 0.03, 123 trees, min_child_weight 5, subsample 0.9, colsample 0.8, λ 5, α 0 |
| Training / CV / validation MAE | 59.0 / 79.1 / **76.3** | 59.0 / 82.4 / **77.0** |
| Validation CI (project clusters) | 60.6–92.5 | 66.5–87.0 |
| MAE reduction vs B1, CI project / package | −8.6..11.8 / −5.7..7.8 | −7.2..15.8 / −4.6..10.8 |
| MAE reduction vs Ridge, CI project / package | −6.1..5.5 / −4.0..3.1 | −8.3..4.4 / −6.3..2.0 |
| vs Random Forest (validation MAE) | 76.3 vs 70.7, worse | 77.0 vs 75.6, worse |
| Median absolute error / RMSE / bias | 49.0 / 113.4 / −29.6 | 58.3 / 105.1 / −0.6 |
| Unseen / seen projects | 69.8 / 79.8 | 84.6 / 68.4 |
| Prior review: MAE (bias) | 145 (−116) | 120 (−61) |
| QCBS MAE | 180 (worst of all models) | 152 |
| Long (> 365 d) bias | −242 | −188 |
| P10–P90 coverage | 0.83 | 0.84 |
| Long detection: PR-AUC | 0.50 | 0.56 |
| Primary alert: TP / FN / FP | 19 / 26 / 15 | **34 / 11 / 27** |
| Recall / precision / F1 / alert rate | 0.42 / 0.56 / 0.48 / 9.6% | **0.76 / 0.56 / 0.64** / 17.3% |
| F1 CI (project clusters) | 0.23–0.68 | 0.39–0.79 |

## Audit

1. **Generalisation:**
   - The training-to-CV gap (59 → 79–82) is smaller than Random Forest's.
   - Validation MAE does not beat Ridge or RF in either protocol, and every paired CI includes zero.
   - Unseen projects are worse than Ridge (as-of 69.8 vs 67.8; cfw365 84.6 vs 78.0).
2. **Instability:**
   - The chosen configuration changes completely between protocols (depth 2 → 4, learning rate 0.1 → 0.03).
   - Long-tender detection swings from the worst model (as-of F1 0.48) to the best (cfw365 F1 0.64).
   - That swing is larger than any model-to-model difference, so it reflects training-set composition, not a stable advantage.
3. **Long-tender trade-off (cfw365):** the best thresholded detector. It catches 34 of 45 long tenders with 27 false alerts, an alert rate of 17%.
   - Ridge (cfw365) catches 27 with 35 false alerts.
   - At a fixed top-20% capacity, as-of XGBoost catches 38 of 45 at 0.54 precision. Its *ranking* is good even where the frozen threshold under-alerts.
4. **By method:**
   - RFB: long recall 0.65 under cfw365, against 0.24 as-of.
   - QCBS: 0.91 under cfw365, against 0.57 as-of.
   - Individual consultants: over-predicted (bias +28 under cfw365).
5. **Feature attribution** (validation, as-of), aggregated to the original features:
   - **Mean |SHAP| on ln(days):** method 0.23, country 0.14, bid window 0.12, sector 0.09, review type 0.08, project age 0.08. SHAP additivity error is at most 0.003.
   - **Permutation importance:** method 15.8 days, review type 7.9, bid window 7.3. Sector is negative (−2.4), so its SHAP share reflects fitting noise, not useful signal.
6. **Leakage:** same policy as Stage 2C. Early-stopping data comes only from training folds; validation was never used for tuning.

## Conclusion

**XGBoost did not outperform Random Forest or Ridge on MAE.** Its long-tender advantage appears under one training protocol only. Added complexity is **not justified** for the primary regression task.
