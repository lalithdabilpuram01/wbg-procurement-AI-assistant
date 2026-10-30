# Model 3 (Simulated Re-bidding Risk): Final Test Report

> **SYNTHETIC OUTCOMES — NOT VALIDATED WORLD BANK RISK ACCURACY · Simulated — Demonstration Only.**

## Feature cleanup (validation, before freezing)

**One pre-declared comparison** (`ml/final23/m3_cleanup_compare.py` → `ml/eda/stage3b_m3_cleanup_20261025T160031Z/`):
- **Original** = Stage 3A policy.
- **Cleaned:**
  - `notice_type` removed, because it duplicates method and category;
  - rare levels grouped at min_frequency = ceil(20 / training prevalence), which is 268 in S2 training;
  - window and project age winsorised at training p1 / p99.

**Results on S2 validation:**

| Measure | Original | Cleaned |
|---|---|---|
| PR-AUC | 0.335 | **0.339** |
| F1 | 0.358 | **0.375** |
| ECE | 0.012 | 0.010 |
| Design columns | 117 | 52 |
| Columns with bootstrap sign agreement < 0.8 | 63 | 22 |
| Top-10 sign-stable share | 0.6 | 0.8 |

- **S0:** top-10 sign stability 0.5 → 0.9, and validation ROC-AUC 0.521 → 0.511 (closer to chance). The largest single |coefficient| is unchanged (1.23 → 1.25): a sparse grouped level under weak null-scenario regularisation. The S0 model is a control only and is never displayed.
- **Rule outcome:** adopt the cleaned policy. There was no degradation.
- **Effect of dropping notice type:** none detectable (S1 PR-AUC −0.001, S2 +0.004), even though notice type is an assumed input to the generator. Method and category carry the same information.

## Protocol (frozen before test labels)

**Inputs:**
- **Configuration:** `ml/final23/FINAL_EVAL_CONFIG_M23.json`.
- **Refit population:** train + validation (FY2016–22; **22,926 tenders**, 1,266 projects).
- **Model:** L2 logistic regression, policy `m3_v2_clean` (min_frequency 263 for the S2 refit), C by 5-fold project-grouped CV (S2: C = 0.32; S0: C = 3.16).

**Frozen rules (from refit out-of-fold probabilities):**
- **Risk bands (v2):**
  - High ≥ q90 (p ≥ 0.148);
  - Medium q70–q90 (p ≥ 0.072);
  - Low below q70.
- **Secondary alert threshold:** max F1 with an alert rate of at most 30% (p ≥ 0.218).

**Scoring:**
- **Test set:** FY2023–25, **20,954 tenders**, 1,192 projects (458 unseen in refit; 7,712 rows).
- **Scored once:** 2026-10-25T16:08:14Z.
- **Not fully blind:** scenario test prevalence was printed earlier.

## Results (test)

| | **S2 (primary)** | **S0 (null control)** |
|---|---|---|
| Realised prevalence (assumed population: 8%) | 8.2% | 8.0% |
| Mean predicted probability | 8.0% | 8.3% |
| PR-AUC (95% CI, project clusters) | **0.334 (0.297–0.370)** | 0.080 (0.076–0.086) ≈ prevalence |
| ROC-AUC (CI) | 0.735 (0.710–0.755) | **0.501 (0.487–0.514)**, contains 0.5 |
| At the alert threshold: recall / precision / F1 | 0.32 / 0.44 / **0.37** | 0.31 / 0.08 / 0.13 (= random) |
| Confusion (TP / FP / FN / TN) | 543 / 701 / 1,174 / 18,536 | 520 / 5,828 / 1,159 / 13,447 |
| Alert rate | 5.9% | 30.3% |
| Brier / ECE | 0.065 / 0.012 | 0.074 / 0.013 |
| Unseen projects: PR-AUC / ROC-AUC | 0.341 / 0.729 | 0.074 / 0.482 |

**Capacity view (S2):**

| Tenders reviewed | Recall | Precision |
|---:|---:|---:|
| 5% | 0.29 | 0.48 |
| 10% | 0.41 | 0.34 |
| 20% | 0.54 | 0.22 |
| 30% | 0.62 | 0.17 |

## Risk bands (primary dashboard output)

| Band | S2: count (share) | S2 simulated event rate | Most frequent displayed factors (S2) | S0 event rate |
|---|---|---:|---|---:|
| Low | 14,382 (68.6%) | **4.4%** | Project age, income group, method | 7.9% |
| Medium | 4,236 (20.2%) | **8.4%** | Method, category, project age | 7.9% |
| High | 2,336 (11.1%) | **31.5%** | Category, method, project age | 8.5% |

- **S2:** **ordered** (Low < Medium < High). The High rate is 7.2× the Low rate.
- **S0:** flat (7.9 / 7.9 / 8.5%), as a null should be.
- **Meaning of High:** the top 10% of simulated model scores, **not** a validated absolute risk category.

## Checks

- **Null control passes.**
- **Generalisation:** unseen-project performance equals the overall result (PR-AUC 0.341).
- **Validation vs test, S2:** close (validation 0.339 / 0.748; test 0.334 / 0.735).
- **Segments (S2, PR-AUC):** 0.16 (QCBS) to 0.38 (RFQ); by region 0.21 (ECA) to 0.44 (EAP). All are above prevalence.

**Artefacts:** `ml/final23/frozen/test_results/final_test_metrics_m23.json`, `charts/m3_final.png`.
