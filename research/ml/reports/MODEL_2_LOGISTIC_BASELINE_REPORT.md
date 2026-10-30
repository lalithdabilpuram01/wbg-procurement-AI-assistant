# Model 2 (Simulated Contract Amendment): Stage 3A Baselines and Logistic Regression

> **SYNTHETIC OUTCOMES — NOT VALIDATED WORLD BANK RISK ACCURACY**

## Run

- **Final run:** `ml/eda/stage3a_m2_20261025T151651Z`.
- **Superseded:** `…T151031Z`, which had identical metrics but a single-shuffle check.
- **Code:** `ml/stage3a/classify.py --model m2`.
- **Artefacts:**
  - `models/{logreg,logreg_l2}_{S0,S1,S2}.joblib` (full preprocessing + model pipelines);
  - `coefficients_l2_*.csv` (with project-bootstrap SD and sign stability);
  - `segments_*`, `charts/S*_pr_calibration_capacity.png`, `stage3a_metrics.json`.
- **Rows:** training 73,998, validation 24,859. The test set (28,949) is sealed.

**Models:**
- **Class prior:** a constant probability.
- **Rule:** "Works category OR prior review" (from the documented assumptions).
- **Logistic regression:** unpenalised (150 design columns, drop-first).
- **L2 logistic regression:** C chosen from 0.001–10 by project-grouped CV on PR-AUC.

**Threshold:** the training out-of-fold F1 maximum with an alert rate of at most 30%.

## Validation results (n = 24,859)

| Scenario | Model | PR-AUC (95% CI, project) | ROC-AUC (CI) | Recall | Precision | F1 (CI) | Alert rate | Brier | ECE |
|---|---|---|---|---:|---:|---|---:|---:|---:|
| **S0** (prevalence 15.4%) | prior | 0.154 | 0.500 | 0 | – | 0 | 0% | 0.130 | – |
| | rule | 0.154 | 0.501 (0.493–0.508) | 0.29 | 0.15 | 0.20 | 29% | – | – |
| | logreg | 0.150 | 0.490 (0.479–0.501) | 0.18 | 0.14 | 0.16 | 20% | 0.130 | 0.015 |
| | **L2** (C 0.001) | 0.151 (0.145–0.157) | **0.493 (0.484–0.503)** | 0.27 | 0.15 | 0.20 | 27% | 0.130 | 0.008 |
| **S1** (14.7%) | rule | 0.175 | 0.578 | 0.43 | 0.21 | 0.28 | 29% | – | – |
| | logreg | 0.231 | 0.626 | 0.43 | 0.22 | 0.29 | 29% | 0.122 | 0.011 |
| | **L2** (C 0.003) | **0.235 (0.215–0.257)** | 0.628 (0.605–0.651) | 0.49 | 0.21 | **0.29 (0.28–0.32)** | 34% | 0.121 | 0.012 |
| **S2** (14.6%) | rule | 0.192 | 0.611 | 0.48 | 0.24 | 0.32 | 29% | – | – |
| | logreg | 0.244 | 0.661 | 0.54 | 0.24 | 0.33 | 33% | 0.120 | 0.017 |
| | **L2** (C 0.01) | **0.252 (0.212–0.278)** | 0.663 (0.633–0.677) | 0.54 | 0.24 | **0.34 (0.28–0.38)** | 32% | 0.119 | 0.011 |

**Confusion matrices (L2 at the threshold):**
- **S1:** TP 1,771, FP 6,598, FN 1,874.
- **S2:** TP 1,974, FP 6,094, FN 1,663.

The prior baseline flags nothing.

**Capacity view (L2, S1):**

| Units flagged | Recall | Precision |
|---:|---:|---:|
| 5% | 0.11 | 0.33 |
| 10% | 0.20 | 0.30 |
| 20% | 0.34 | 0.25 |
| 30% | 0.45 | 0.22 |

Halving the review load from 30% to 15% loses about a third of detections. Precision stays low (about 0.2–0.3) at any useful recall, because the simulated signal is deliberately weak.

## Audit

1. **Null scenario (S0):** passes.
   - The ROC-AUC CIs of all feature-based models contain 0.5, and PR-AUC ≈ prevalence.
   - The F1 of about 0.2 equals what random 27–29% alerting achieves at 15% prevalence (2·0.15·0.28 / 0.43 ≈ 0.20).
   - 20 shuffles: ROC-AUC 0.500 ± 0.003.
2. **Against the baselines:**
   - L2 beats the rule on PR-AUC in S1 (0.235 vs 0.175) and S2 (0.252 vs 0.192), with non-overlapping CIs.
   - F1 gains are small (S1 +0.01, S2 +0.02). The rule already captures the two strongest assumed drivers.
   - Unpenalised logistic regression ≈ L2.
3. **Ceiling** (audit, generator probabilities without project effects):

   | | Ceiling PR-AUC | L2 PR-AUC | Ceiling ROC-AUC | L2 ROC-AUC |
   |---|---:|---:|---:|---:|
   | S1 | 0.233 | **0.235** | 0.628 | 0.628 |
   | S2 | 0.277 | 0.252 | 0.686 | 0.663 |

   In S1 the L2 model is at the ceiling. In S2 the remaining headroom is +0.025 PR-AUC, from the U-shape and interaction a linear model cannot express.
4. **Recovery of assumptions** (`ml/eda/stage3a_recovery_20261025T152117Z`):
   - **S1:** category order recovered exactly (Spearman 1.0: Works +0.40 … Consulting −0.24); method ordering 0.63 (RFB +0.30, RFQ −0.33); approval interval −0.19 per SD (assumed −0.25); prior vs post review contrast 0.47 (assumed 0.6); income negative.
   - **S2:** the approval interval comes out **+0.11 (wrong sign)**, because the U-shaped assumption cannot be represented linearly.
5. **Overfitting:** small. PR-AUC in-sample / out-of-fold / validation: S1 0.247 / 0.233 / 0.235; S2 0.289 / 0.232 / 0.252. The S2 in-sample gap comes from country dummies fitting the simulated country effects. CV strongly preferred strong regularisation (C 0.001–0.01).
6. **Coefficient stability** (20 project bootstraps): the leading coefficients have sign agreement 1.0 and bootstrap SD of about 0.02–0.04 (S1). Rare country levels are unstable (e.g. S2 "RO": sign agreement 0.3).
7. **Calibration:** good. ECE is 0.008–0.017 and Brier is below the prior's in S1 / S2.
8. **Alert rate drift:** the L2 S1 threshold gives a 33.7% alert rate on validation (training out-of-fold rate ≤ 30%). Thresholds move with population shift.
9. **Project-held-out** (2,044 rows, 160 unseen projects; S1 / S2): PR-AUC 0.272 / 0.289 against prevalence 0.172 / 0.184, and ROC-AUC 0.61 / 0.61. The signal generalises to unseen projects.
10. **Segments (S1, L2):**
    - PR-AUC is highest for RFB (0.33) and Works (0.31); LAC is lowest (0.18).
    - Every segment is above its own prevalence except LCS (10 positives).

## Conclusion

L2 logistic regression is calibrated, stable and recovers the assumed linear drivers. It is at the theoretical ceiling in S1 and within 0.025 PR-AUC of it in S2. All results are simulated.
