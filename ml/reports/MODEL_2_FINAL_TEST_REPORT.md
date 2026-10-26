# Model 2 (Simulated Contract Amendment): Final Test Report

> **SYNTHETIC OUTCOMES — NOT VALIDATED WORLD BANK RISK ACCURACY.** These scores measure recovery of an *assumed* data-generating process (`sim_v1`), not real World Bank amendment risk.

## Protocol (frozen before any test label was read)

| Item | Value |
|---|---|
| Configuration | `ml/final23/FINAL_EVAL_CONFIG_M23.json` |
| Refit | `ml/final23/final_refit23.py`: train + validation (FY2020–23; **98,857 contracts**, 1,561 projects) |
| Freeze record | `ml/final23/frozen/FROZEN.json` (SHA-256 of the config, features, labels, generator config, models and predictions) |
| One-time scorer | `final_score23.py`. Smoke-tested on validation rows (in-sample) first; scored 2026-10-25T16:08:14Z; lock `frozen/TEST_SCORED.json` |
| Test set | FY2024: **28,949 contracts**, 1,053 projects (166 unseen in refit; 2,311 rows) |
| Model | L2 logistic regression, feature policy `m2_v1` (unchanged from Stage 3A), C by 5-fold project-grouped CV. S2: C = 0.032; S0: C = 0.001 |
| Review threshold | Max F1 on refit out-of-fold probabilities with an alert rate of at most 30% (an assumed review capacity of 30% of contracts). S2 threshold: p ≥ 0.167 |

**Not fully blind:** realised scenario test prevalence had been printed in the synthetic-data report.

## Results (test)

| | **S2 (primary)** | **S0 (null control)** |
|---|---|---|
| Realised prevalence (assumed population: 15%) | 13.8% | 15.2% |
| Mean predicted probability | 14.9% | 15.4% |
| PR-AUC (95% CI, project clusters) | **0.232 (0.201–0.253)** | 0.154 (0.150–0.159) ≈ prevalence |
| ROC-AUC (CI) | 0.655 (0.630–0.668) | **0.504 (0.497–0.512)**, contains 0.5 |
| Recall / precision / F1 at the review threshold | 0.51 / 0.22 / **0.31** | 0.27 / 0.15 / 0.20 (= random alerting) |
| Confusion (TP / FP / FN / TN) | 2,021 / 7,007 / 1,969 / 17,952 | 1,180 / 6,523 / 3,221 / 18,025 |
| Alert rate | 31.2% (cap 30% on refit out-of-fold) | 26.6% |
| Brier (constant-prevalence Brier) | 0.114 (0.119) | 0.129 (0.129) |
| ECE | 0.014 | 0.005 |
| Unseen projects: PR-AUC / ROC-AUC (prevalence) | 0.247 / 0.624 (15.6%) | 0.154 / 0.483 (15.8%) |

**Capacity view (S2):**

| Contracts reviewed | Recall | Precision |
|---:|---:|---:|
| 5% | 0.10 | 0.28 |
| 10% | 0.19 | 0.26 |
| 20% | 0.37 | 0.25 |
| 30% | 0.49 | 0.23 |

Precision stays at about 0.23–0.28 at any capacity, because the simulated signal is weak by design.

**Segments (S2, PR-AUC):**
- **Highest:** RFB 0.28, Works 0.28, South Asia 0.26.
- **Lowest:** CQS 0.10, MENA 0.15.
- Every segment is at or above its prevalence.

## Checks

- **Null control passes:** S0 ROC-AUC CI contains 0.5, PR-AUC CI contains the prevalence, and F1 equals random alerting at that rate.
- **Threshold transfer:** the 30% refit cap became 31.2% on test. The cap is not guaranteed across periods.
- **Calibration:** predictions slightly over-estimate the test rate (14.9% vs 13.8%) because simulated test prevalence is lower. ECE is 0.014.
- **Validation vs test, S2:** close (validation PR-AUC 0.252 / ROC-AUC 0.663; test 0.232 / 0.655).

**Artefacts:** `ml/final23/frozen/test_results/final_test_metrics_m23.json`, `charts/m2_final.png`.
