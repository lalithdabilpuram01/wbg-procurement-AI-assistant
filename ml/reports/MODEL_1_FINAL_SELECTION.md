# Model 1 (Tender Duration): Final Model Selection

**Date:** 2026-10-25 · **Basis:** the one-time held-out test (`MODEL_1_FINAL_TEST_REPORT.md`), read together with the validation history.

## 1. Pre-declared rule (frozen in `FINAL_EVAL_CONFIG.json` before the test)

> Recommend Random Forest only if its MAE reduction vs Ridge is positive, with both project- and package-cluster CIs excluding zero, and it is not worse on unseen projects or long-tender F1. Otherwise prefer the simpler stable model: Ridge if it is not significantly worse than B1, else B1.

**Mechanical outcome:**
- RF vs Ridge: CI −6.8..14.7 (project) and −2.5..10.6 (package) → **not convincing**.
- Ridge vs B1: CI −11.8..12.4 and −11.4..7.4 → **not significantly worse**.
- **Rule selects: Ridge.**

## 2. Objective comparison

| Criterion | B1 | Ridge | Random Forest | Better on test |
|---|---|---|---|---|
| Test MAE (CI, project clusters) | 74.3 (60–93) | 76.7 (63–89) | 73.5 (54–93) | **Tie** (all within 3.2 d) |
| Median absolute error / RMSE | 55.0 / 108.0 | 62.4 / 106.5 | 47.6 / 105.1 | RF (not tested for significance) |
| Package-weighted MAE | 84.1 | 78.9 | 77.1 | RF ≈ Ridge |
| Unseen projects (212 rows) | 58.4 | 65.3 | **45.3** | RF |
| Excluding the 5 dominant projects (post hoc) | 87.1 | 72.4 | 66.7 | RF > Ridge > B1 |
| Prior-review MAE | 147 | 117 | 108 | Models > B1 |
| Long-tender ranking (PR-AUC) | 0.37 | 0.54 | 0.56 | Ridge ≈ RF ≫ B1 |
| Long-tender F1 at the frozen threshold (recall / precision) | **0.56** (0.59 / 0.52) | 0.46 (0.39 / 0.56) | 0.41 (0.30 / 0.68) | B1 (the model thresholds under-alert on test) |
| Interval coverage, P10–P90 | – | **0.84** | 0.77 | Ridge |
| Stability across validation protocols and test | MAE 77.5 / 79.6 / 74.3 | 75.8 / 74.7 / 76.7 | 70.7 / 75.6 / 73.5 | Ridge most stable; RF most variable |
| Overfitting (training vs CV MAE at refit) | – | 71.0 vs 81.2 | 52.6 vs 81.9 | Ridge |
| Interpretability | Lookup table | Additive multiplicative effects | Needs SHAP / permutation | B1 > Ridge > RF |
| Operational complexity | Trivial | Low (one pipeline, about 50 coefficients) | Moderate (500 deep trees) | B1 < Ridge < RF |

## 3. Decision

**Selected model for the PoC: Ridge regression (log-duration, as-of training), with B1 retained as the displayed historical benchmark.**

*Rationale:*
1. **The frozen rule selects Ridge.** It was declared before the test, and changing the choice now would be post-hoc.
2. **Accuracy:** no model is reliably more accurate on the test. Ridge is the most stable across validation and test.
3. **Uncertainty:** Ridge has well-calibrated intervals (0.84 coverage on both validation and test).
4. **Explainability:** officers can read its effects (×1.48 for QCBS, ×1.25 vs ×0.80 for prior vs post review, ×1.24 per SD of log bid window).
5. **Ranking:** much better ranking of long tenders than B1 (PR-AUC 0.54 vs 0.37).

*What the test does not support:*
- Ridge is **not** more accurate than B1 (−3.1%, within noise), and it is worse than B1 on unseen projects and at the frozen alert threshold.
- **Random Forest** shows the best point estimates on several measures (median absolute error, unseen projects, PR-AUC), but none reaches the pre-declared bar. It also has the weakest interval coverage, the most overfitting and the largest protocol sensitivity. It remains the recommended challenger to re-evaluate when more labelled data exists.
- **B1** stays a strong benchmark: it has the best thresholded F1 on the test. If the priority were minimum complexity, B1 would be a defensible alternative. The pre-declared rule and B1's poor ranking (PR-AUC 0.37) and prior-review error (147 days) argue against it.

## 4. How the selected model should be used in the application

**Show:**
- the Ridge median estimate;
- the P10–P90 range;
- the B1 historical median for the method × category cell as context;
- the main drivers (coefficients).

**Long-tender risk:**
- Use Ridge's P(> 365 d) as a **ranked risk score**, not as a fixed alert.
- The training-derived threshold under-alerted on the test (recall 0.39), because durations shorten over time.
- Before any operational alerting, the threshold must be **re-derived on the most recent as-of data** (same out-of-fold rule) and drift-monitored. A capacity-based rule ("flag the top 20%": test recall 0.83, precision 0.43) was a pre-declared reported view and needs explicit approval before use.

**Labelling:** each prediction carries its scope ("advertised methods; based on linked signed contracts") and an "indicative" label for QCBS and long tenders, where errors are largest.

## 5. Readiness for integration

**Ready for the PoC application as decision support**, with the labelling above:
- artefacts are frozen (`ml/final/frozen/models/ridge_final.joblib`, `b1_final.joblib`);
- it works from features available at tender publication;
- it has a documented scope and calibrated intervals.

**Not ready:**
- for automated alerting (threshold transfer is not validated);
- for portfolio-wide use (184 linked projects, advertised methods only);
- as a production model (no drift monitoring or retraining procedure yet).

No further Model 1 optimisation is recommended now. Work can move to Models 2 and 3, RAG and the application.
