# Classification Evaluation Framework (Models 2 and 3)

**Applies to:** every amendment and re-bidding experiment, starting with Stage 3A.

**Required caption** on every chart and table: **"SYNTHETIC OUTCOMES — NOT VALIDATED WORLD BANK RISK ACCURACY"**.

## 1. Data discipline

- **Splits:** chronological by the prediction-date fiscal year.
  - Model 2: train FY2020–22, validation FY2023, test FY2024.
  - Model 3: train FY2016–21, validation FY2022, test FY2023–25.
- **Sealed test:** test rows are never loaded into the modelling frame before an approved final evaluation.
- **Separation:** features come from `ml/features/`; labels from `ml/synthetic/<model>_sim_v1/labels/`. Generator internals and config are read **only** by audit scripts (`leakage_tests.py`, `recovery_audit.py`, `ceiling_audit.py`).
- **Preprocessing:** one-hot (`min_frequency` 50), median imputation and standardisation, all fitted on training folds only.
- **Tuning:** 5-fold GroupKFold by project.

## 2. Metrics (validation; test only at final evaluation)

| Group | Metrics |
|---|---|
| **Primary** | Recall, precision, F1 at the operational threshold; PR-AUC |
| **Additional** | ROC-AUC, confusion matrix (TP / FP / FN / TN), Brier score, calibration curve (10 quantile bins) and mean absolute calibration gap (ECE), positive prevalence, alert rate |
| **Capacity view** | Recall and precision when 5 / 10 / 20 / 30% of units are flagged |
| **Uncertainty** | Project-cluster bootstrap (500 resamples) 95% CIs for PR-AUC, ROC-AUC, F1, recall and precision |
| **Generalisation** | Project-held-out validation (projects unseen in training); gap between training in-sample, training out-of-fold and validation |
| **Segments** | Method, category, region, where at least 10 positives and 10 negatives exist |
| **Null and leakage controls** | S0 must give ROC-AUC CIs containing 0.5 and PR-AUC ≈ prevalence. 20 label shuffles must centre on 0.5 |
| **Recovery** | Learned coefficients vs the documented assumptions (audit); headroom against the generator ceiling (audit) |

## 3. Thresholds and risk bands (training-only)

- **Operational threshold:** the probability threshold that maximises F1 on **training out-of-fold** probabilities, subject to an **alert rate ≤ 30%**. It is never adjusted on validation or test.
- **Trade-off reporting:** the precision–recall curve and the capacity curve are always shown alongside the single operating point. Maximising recall by alerting on most units is not acceptable.
- **Model 3 risk bands (v2):** from training out-of-fold probabilities.
  - **High:** at or above the 90th percentile (top 10%).
  - **Medium:** 70th–90th percentile (next 20%).
  - **Low:** below the 70th percentile.

  Bands show the probability band, the top 3 positive contributing features (coefficient × standardised value) and the label **"Simulated — Demonstration Only"**. "High" is a relative simulated rank, **not** an established real-world risk category.

  *Version history:* v1 (Medium ≥ F1 threshold; High ≥ max(F1 threshold, q90)) produced an empty Medium band whenever the F1 threshold exceeded q90. That was visible in the training out-of-fold shares alone (M3 S2), and it was replaced before reporting.
- **Model 2 display:** the simulated probability as a percentage, with the same disclaimer.

## 4. Interpretation rules

- Synthetic metrics measure how well a model **recovers the assumed data-generating process**. They are not evidence about real amendments or re-bids.
- Neither recall nor F1 alone selects a model. Comparisons need PR-AUC, the operating point, calibration, grouped CIs and project-held-out results.
- A more complex model is justified only if its validation gain over L2 logistic regression is CI-separated **and** the audit ceiling shows headroom.
