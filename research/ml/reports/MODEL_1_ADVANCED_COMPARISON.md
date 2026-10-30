# Model 1 (Tender Duration): Advanced Model Comparison and Recommendation

**Date:** 2026-10-25 · **Status:** validation complete; **the test set (616) has not been scored.** Awaiting approval before final held-out evaluation.

## Sources

| Item | Location |
|---|---|
| Stage reports | `STAGE_2C_RANDOM_FOREST_REPORT.md`, `STAGE_2D_XGBOOST_REPORT.md`, `STAGE_2E_SURVIVAL_FEASIBILITY_REPORT.md` |
| Consistent tables | `ml/eda/stage2c_comparison_20261025T080300Z/`: `regression_metrics.csv`, `long_detection.csv` (3 alert rules × 4 models × 2 protocols), `protocol_effects.csv`, `model_protocol_comparison.png` |
| Per-run charts | `charts/01_mae_ci`, `02_pr_curves_long`, `03_pred_vs_actual`, `04_bias_by_band`, `05_importance_*` |
| Saved models | `models/*.joblib` + `MODEL_CARD.json` |
| Tracking | `ml/experiments/runs.jsonl` (stage "2C/2D") |
| Pre-registration (frozen before any advanced fit) | `ml/stage2c/PREREGISTRATION.json`, with SHA-256 lock |

## 1. What was frozen in advance

**Long-duration threshold: 365 days** (more than a year from first advertisement to signing).
- **Business basis:** a one-year planning yardstick.
- **Training-only basis:** about the p80 of complete training outcomes.
- **Disclosure:** the validation count of long tenders (45) was already known from Stage 2A; no validation performance was.

**Long-tender score and alert rules:**
- **Score:** P(duration > 365).
  - B1: the training exceedance share of the contract's cell.
  - Regression models: log-normal, with the model's out-of-fold residual SD.
- **Primary alert:** the threshold that maximises F1 on the model's **training out-of-fold** scores, subject to an alert rate of at most 30%. It was never adjusted on validation.
- **Also reported:** a fixed rule (point prediction above 365) and a capacity view (top 20% of scores).

**Protocols:**
- `asof` (reference, 462 rows).
- `cfw365` (372 rows): first examined post hoc in Stage 2B, now frozen. No other window was tried.

**Experiment budget:**
- Random Forest: grid of 36.
- XGBoost: 30 random configurations with grouped-CV early stopping.
- Ridge: the Stage 2B grid.
- All tuning used 5-fold GroupKFold by project inside training.

## 2. Validation results (identical 353 contracts; 45 long)

| Protocol | Model | Training MAE | CV MAE | **Validation MAE** (95% CI, project clusters) | vs B1, CI project / package | Unseen projects MAE | Long (> 365 d) bias | P10–P90 coverage |
|---|---|---:|---:|---|---|---:|---:|---:|
| asof | B1 | 81.1 | 91.3 | 77.5 (61.8–95.5) | – | 71.5 | −223 | – |
| asof | **Ridge** | 66.0 | **75.9** | 75.8 (61.2–91.0) | −5.6..9.0 / −4.0..7.4 | 67.8 | −245 | 0.84 |
| asof | Random Forest | 49.7 | 82.0 | **70.7** (57.9–85.0) | −1.4..16.4 / **1.1..12.5** | 67.6 | −209 | 0.88 |
| asof | XGBoost | 59.0 | 79.1 | 76.3 (60.6–92.5) | −8.6..11.8 / −5.7..7.8 | 69.8 | −242 | 0.83 |
| cfw365 | B1 | 86.0 | 96.5 | 79.6 (66.0–95.5) | – | 82.7 | −207 | – |
| cfw365 | **Ridge** | 72.4 | **83.8** | **74.7** (61.9–87.5) | −4.3..15.5 / −1.8..12.1 | 78.0 | −223 | 0.80 |
| cfw365 | Random Forest | 53.1 | 84.1 | 75.6 (65.6–85.8) | −5.4..16.3 / −2.8..11.4 | 81.0 | −183 | 0.83 |
| cfw365 | XGBoost | 59.0 | 82.4 | 77.0 (66.5–87.0) | −7.2..15.8 / −4.6..10.8 | 84.6 | −188 | 0.84 |

All values are MAE in days.

**Separating the two sources of variation:**

| Effect | Range | Detail |
|---|---|---|
| **Protocol effect** (cfw365 − asof, same model) | −1.1 to +4.9 days | B1 +2.1, Ridge −1.1, RF +4.9, XGB +0.6 |
| **Model effect** (vs B1, same protocol) | −6.8 to +2.8 days | |

Both effects are smaller than the CI widths (about 30 days). Only one comparison is significant under **both** cluster schemes: **RF vs Ridge, as-of** (0.8..9.5 / 1.4..8.7). It reverses under cfw365.

## 3. Long-duration detection at the frozen primary rule

| Protocol | Model | PR-AUC | TP | FN | FP | Recall | Precision | F1 (95% CI, project clusters) | Alert rate |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|
| asof | B1 | 0.38 | 24 | 21 | 22 | 0.53 | 0.52 | 0.53 (0.23–0.74) | 13% |
| asof | Ridge | 0.45 | **31** | 14 | 33 | **0.69** | 0.48 | 0.57 (0.31–0.74) | 18% |
| asof | Random Forest | **0.58** | 27 | 18 | 19 | 0.60 | **0.59** | **0.59** (0.27–0.78) | 13% |
| asof | XGBoost | 0.50 | 19 | 26 | 15 | 0.42 | 0.56 | 0.48 (0.23–0.68) | 10% |
| cfw365 | B1 | 0.38 | 24 | 21 | 23 | 0.53 | 0.51 | 0.52 | 13% |
| cfw365 | Ridge | 0.50 | 27 | 18 | 35 | 0.60 | 0.44 | 0.51 | 18% |
| cfw365 | Random Forest | 0.56 | 29 | 16 | 43 | 0.64 | 0.40 | 0.50 | 20% |
| cfw365 | XGBoost | 0.56 | **34** | 11 | 27 | **0.76** | **0.56** | **0.64** (0.39–0.79) | 17% |

**The recall / false-alert trade-off:**
- **Cost of extra recall:** catching about 6–7 more long tenders (for example, asof Ridge 31 vs RF 27 of 45) costs about 14 more false alerts, so precision falls from 0.59 to 0.48.
- **Fixed rule:** "prediction > 365" is useless for screening: recall 0.02–0.38. Median predictions rarely exceed a year, because every model under-predicts long tenders by 180–245 days. Screening must use the probability score, not the point estimate.
- **Fixed capacity (top 20% flagged, 71 alerts):** recall ranges from 0.58 (B1) to 0.84 (asof XGBoost), with precision 0.37–0.54.
- **B1's alerts are just "is QCBS"** (all 45 QCBS contracts are flagged). The models add RFB detection (recall 0.24–0.65), which B1 cannot do.
- **Uncertainty:** F1 confidence intervals overlap for every pair, and the best detector changes with protocol (asof RF, cfw365 XGBoost).

## 4. Survival analysis

**Not scientifically feasible** with current data (Stage 2E). At the training cutoff, 721 unsigned packages mix:
- 145 that later sign;
- 179 awarded without a contract record;
- 369 with no signal;
- 0 recorded cancellations.

Linkage failure is strongly group-dependent (35–68% by region). No valid censoring indicator exists, so no survival model was fitted.

## 5. Model selection

**Strict pre-registered rule:** the simplest model within the CI of the best, with a reliable MAE reduction vs B1 under project **and** package clusters. **No candidate satisfies it.** Under the strict rule, B1 remains the accuracy reference.

**Recommendation for the PoC: Ridge regression (log-normal), as-of training protocol**, with B1 as the published reference.

*Why Ridge over B1* (not on MAE significance, which neither achieves):
- **Best training-only evidence:** the lowest CV MAE in both protocols (75.9 / 83.8), so the choice does not rest on validation alone.
- **Never worse than B1 on validation,** and better on unseen projects (67.8 vs 71.5).
- **Better long-tender screening:** PR-AUC 0.45–0.50 vs 0.38, recall 0.60–0.69 at an 18% alert rate. It detects long RFB tenders, which B1 cannot.
- **Calibrated uncertainty:** P10–P90 coverage 0.80–0.84 against a nominal 0.80.
- **Explainable:** additive coefficients (×exp(b) per feature) that officers can read. In the as-of fit: QCBS is ×1.55; prior vs post review is ×1.41 (exp(0.171 + 0.171)); and each SD of log bid window is ×1.30.

*Why not the advanced models:*
- **Random Forest** wins validation MAE only under one protocol. It loses to Ridge in training CV, is heavily overfitted (training 50 vs CV 82), and its gain comes from seen projects. It is the best ranker of long tenders (PR-AUC 0.58 / 0.56), which makes it the challenger to retest if more data arrive.
- **XGBoost** has no MAE gain and is unstable across protocols.

**Protocol:** keep **as-of** for the final model. It is pre-declared, deployment-realistic and has the larger training set. cfw365 changed Ridge by only −1.1 days and was first chosen post hoc.

## 6. Limitations that remain

- **Long tail:** every model under-predicts tenders over 365 days by about 180–245 days. Point predictions for long tenders are unreliable; the probability score and the interval are the useful outputs.
- **Small training set:** 462 contracts in 78 projects, and 60 validation projects. CIs are about ±15 days, and model differences fall inside them.
- **Scope:** advertised methods in the 184 reference-linked projects only, with thin LAC data (validation MAE about 140 for every model).
- **Validation is not independent proof:** several models and protocols were compared on it. The test set is the only unbiased estimate.
- **Not covered:** time-ordered stability checks (two rolling origins) were run for Ridge in Stage 2B, but not repeated for RF and XGBoost.

## 7. Ready for the final held-out test?

**Yes, conditionally.** The candidate, features, preprocessing, threshold and alert rule are frozen.

**Proposed test protocol, for approval:**
1. **Refit:** Ridge (α by the same grouped-CV grid) and B1 on **training + validation rows observable by 2023-06-30**, which is the as-of rule moved to the end of validation.
2. **Re-derive** the alert threshold on that refit's training out-of-fold scores, by the same rule.
3. **Score the 616 test contracts once.** Report MAE (with project / package CIs), bias, interval coverage, long-tender recall / precision / F1 / PR-AUC, and every segment.
4. **Optional, pre-declared secondary:** Random Forest scored once as the challenger.
5. **Reporting caveat:** the test window is highly concentrated (the top 5 projects hold 52.6% of rows), so results are reported per project too.

## 8. Evaluation framework for Models 2 and 3 (from the F1 / recall addendum; not yet executed)

**Metrics:** precision, recall, F1, PR-AUC, ROC-AUC, Brier score and a calibration curve, plus the confusion matrix at the chosen threshold. Grouped (project / package) bootstrap CIs and class prevalence are reported with every result.

**Thresholds:** chosen by **training-only grouped CV**. The rule is to maximise F1 subject to a minimum precision (or maximum alert rate) declared before fitting. Precision–recall trade-off curves are shown, not just one point.

**Re-bidding risk display:** Low / Medium / High bands, with cut-offs fixed on training CV (for example, Medium at or above the F1 threshold, High at or above a precision ≥ 2× prevalence point). Underlying probabilities are retained for evaluation.

**Synthetic labels:** every result is labelled **"simulated outcome — not World Bank predictive accuracy"**, following `ML_SYNTHETIC_DATA_DESIGN_REVIEW_STAGE2A.md`.

**No single metric decides:** neither recall nor F1 alone selects a model. The trade-off and the alert burden are reported together.
