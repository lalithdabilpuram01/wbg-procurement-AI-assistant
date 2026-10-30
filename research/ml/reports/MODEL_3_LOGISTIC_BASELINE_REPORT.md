# Model 3 (Simulated Re-bidding Risk): Stage 3A Baselines and Logistic Regression

> **SYNTHETIC OUTCOMES — NOT VALIDATED WORLD BANK RISK ACCURACY · "Simulated — Demonstration Only"**

## Run

- **Final run:** `ml/eda/stage3a_m3_20261025T151550Z`.
- **Superseded:** `…T150930Z` (v1 risk bands, single-shuffle check).
- **Code:** `ml/stage3a/classify.py --model m3`.
- **Artefacts:**
  - `models/{logreg,logreg_l2}_{S0,S1,S2}.joblib`;
  - `coefficients_l2_*.csv`, `segments_*`;
  - **`risk_band_examples_{S}.csv`** (probability, band, top simulated factors, disclaimer, for validation tenders);
  - `charts/`.
- **Rows:** training 17,253, validation 5,673. The test set (20,954) is sealed.

**Models:**
- **Prior:** constant probability.
- **Rule:** "Works OR window < 14 days OR no usable window", from the assumptions. It flags 47% of tenders, so it is a deliberately coarse baseline.
- **Logistic regression:** unpenalised (110 columns).
- **L2 logistic regression:** project-grouped CV over C.

## Validation results (n = 5,673)

| Scenario | Model | PR-AUC (95% CI, project) | ROC-AUC (CI) | Recall | Precision | F1 (CI) | Alert rate | Brier | ECE |
|---|---|---|---|---:|---:|---|---:|---:|---:|
| **S0** (prevalence 7.8%) | rule | 0.080 | 0.507 | 0.49 | 0.08 | 0.14 | 47% | – | – |
| | logreg | 0.083 | 0.521 (0.495–0.549) | 0.32 | 0.09 | 0.14 | 29% | 0.073 | 0.017 |
| | **L2** (C 10) | 0.083 (0.074–0.094) | **0.521 (0.495–0.550)** | 0.34 | 0.09 | 0.14 | 30% | 0.073 | 0.015 |
| **S1** (8.2%) | rule | 0.104 | 0.602 | 0.66 | 0.11 | 0.20 | 47% | – | – |
| | logreg | 0.161 | 0.627 | 0.47 | 0.13 | 0.20 | 30% | 0.074 | 0.017 |
| | **L2** (C 0.003) | **0.170 (0.083–0.263)** | 0.633 (0.556–0.702) | 0.45 | 0.12 | 0.19 (0.13–0.27) | 30% | 0.073 | 0.014 |
| **S2** (8.1%) | rule | 0.119 | 0.661 | 0.77 | 0.13 | 0.22 | 47% | – | – |
| | logreg | 0.331 | 0.744 | 0.32 | 0.42 | 0.36 | 6.3% | 0.064 | 0.010 |
| | **L2** (C 0.32) | **0.335 (0.280–0.379)** | 0.747 (0.713–0.774) | 0.31 | 0.43 | **0.36 (0.28–0.42)** | 5.8% | 0.064 | 0.012 |

**Confusion matrix (L2, S2):** TP 141, FP 189, FN 316.

**Capacity view (L2):**

| Units flagged | S1 recall / precision | S2 recall / precision |
|---:|---|---|
| 5% | 0.15 / 0.24 | 0.28 / 0.45 |
| 10% | 0.24 / 0.19 | 0.41 / 0.33 |
| 20% | 0.36 / 0.15 | 0.55 / 0.22 |
| 30% | 0.45 / 0.12 | 0.64 / 0.17 |

In S2, moving from the F1 operating point (6% alerts) to a 20% review capacity raises recall from 0.31 to 0.55 at the cost of precision falling from 0.43 to 0.22. That is about 111 extra detections for about 694 extra false alerts: roughly 6 false alerts per extra detection.

The rule's precision–recall "curve" is a single operating point; the straight line drawn in the chart is an interpolation artefact.

## Risk bands (v2, training-OOF quantiles; validation shares and simulated event rates)

| Scenario | Low (< q70) | Medium (q70–q90) | High (≥ q90) | Cut-offs (p) |
|---|---|---|---|---|
| S0 | 70.1% · 7.4% | 20.1% · 9.4% | 9.8% · 7.9% | 0.094 / 0.112 |
| S1 | 69.8% · 6.5% | 16.9% · 8.6% | 13.3% · **16.9%** | 0.094 / 0.122 |
| S2 | 69.5% · 4.0% | 19.4% · 9.7% | 11.1% · **30.4%** | 0.080 / 0.153 |

- **S0:** the bands are flat (no ordering), as expected under the null.
- **S1 / S2:** the bands are ordered, with the High event rate 2.6× (S1) and 7.5× (S2) the Low rate.
- **"High" is a relative simulated rank,** not a real-world risk category.
- **Dashboard rows** (`risk_band_examples_S*.csv`) carry the band, the probability, the top 3 contributing features and "Simulated — Demonstration Only".

## Audit

1. **Null (S0):** passes. The ROC-AUC CI contains 0.5; PR-AUC 0.083 ≈ prevalence 0.078; 20 shuffles centre on 0.498.
   - **However, large coefficients appear in S0** (REOI −0.55, ">180-day window" −1.23, rare countries up to ±0.9) with weak regularisation (CV chose C = 10).
   - These are **collinearity and rare-level artefacts:** notice type duplicates method / category (as in Model 1), and the >180-day status has about 50 training rows.
   - They do not produce discrimination, but they would surface as misleading "contributing factors" in the dashboard. Feature-quality issue: see the decision report.
2. **Against the baselines:**
   - PR-AUC: L2 is far above the rule in S2 (0.335 vs 0.119). In S1 it is 0.170 vs 0.104 with wide CIs.
   - F1: S1 L2 0.19 vs rule 0.20 (no gain, at less than two-thirds of the alert burden). S2 0.36 vs 0.22.
3. **Ceiling** (audit; project effects removed):

   | | Ceiling PR-AUC | L2 PR-AUC | Ceiling ROC-AUC | L2 ROC-AUC |
   |---|---:|---:|---:|---:|
   | S1 | 0.165 | **0.170** | 0.652 | 0.633 |
   | S2 | 0.354 | 0.335 | 0.769 | 0.747 |

   S1 is at the ceiling. S2 headroom is +0.019 PR-AUC.
4. **Recovery:**
   - **S1:** window −0.195 per SD (assumed −0.2); Works +0.20 (positive); REOI below IFB; RFB +0.14; income negative.
   - **S2:** the U-shaped window effect is captured through the deadline-status categories (">180 days" +2.8 vs positive-window −2.6) plus the window term −0.67 per SD. The linear model approximates the curvature with category dummies.
5. **Overfitting:** in-sample / out-of-fold / validation PR-AUC: S1 0.163 / 0.124 / 0.170; S2 0.292 / 0.249 / 0.335. Validation is above out-of-fold in S2, consistent with the shared country effects and the slightly higher validation prevalence. No damaging overfit.
6. **Calibration:** ECE 0.010–0.017; Brier below the prior's in S2.
7. **Project-held-out** (760 rows, 148 projects):
   - **S1: PR-AUC 0.075 vs prevalence 0.068, ROC-AUC 0.54. Weak:** with σ_project = 0.4 and only about 52 positives, the unseen-project signal is not detectable.
   - **S2:** PR-AUC 0.384, ROC-AUC 0.79, which generalises.
8. **Segments:**
   - **S1:** signal concentrated in RFB (PR-AUC 0.25), Works (0.29) and South Asia (0.31). Consulting methods are near prevalence.
   - **S2:** all methods are above prevalence (0.22–0.47).

## Conclusion

L2 logistic regression is the right baseline. It is near the theoretical ceiling, well calibrated and gives sensibly ordered risk bands in S1 / S2. Two issues remain before the dashboard explanations can be trusted:
- the factor explanations are distorted by collinear and rare features;
- unseen-project performance in S1 is weak.

All results are simulated.
