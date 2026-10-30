# Stage 3A Review and Decision: Models 2 and 3 (simulated outcomes)

**Date:** 2026-10-25 · **Status:** Stage 3A complete. No Random Forest or XGBoost trained; test sets sealed. Awaiting approval.

> **SYNTHETIC OUTCOMES — NOT VALIDATED WORLD BANK RISK ACCURACY**

## 1. What was built

| Step | Output |
|---|---|
| Real feature tables + splits | `ml/stage3a/features.py` → `ml/features/{m2_contract,m3_tender}_features_v1.parquet`, `FEATURES_MANIFEST.json` |
| Frozen generator config (assumptions) | `ml/synthetic/configs/sim_v1.json` |
| Generator | `ml/synthetic/generator.py` → `ml/synthetic/{m2,m3}_sim_v1/{labels/, generator/, PROVENANCE.json}` |
| Pre-training tests | `ml/stage3a/leakage_tests.py` → `ml/eda/stage3a_leakage_20261025T151418Z/` (**all pass**) |
| Baselines + logistic regression | `ml/stage3a/classify.py` → `ml/eda/stage3a_m2_20261025T151651Z/`, `ml/eda/stage3a_m3_20261025T151550Z/` |
| Audits | `recovery_audit.py` → `stage3a_recovery_20261025T152117Z/`; `ceiling_audit.py` → `stage3a_ceiling_20261025T152224Z/` |
| Tracking | `ml/experiments/runs.jsonl` (stage "3A", `label_source: synthetic`) |

**Superseded runs** (kept as an audit trail; their records are in `runs.jsonl`): `stage3a_m3_20261025T150930Z` and `stage3a_m2_20261025T151031Z`. They used the v1 risk-band rule and a single shuffle. Model metrics are identical.

## 2. Summary results (validation, L2 logistic regression; all simulated)

| | Model 2 S0 | Model 2 S1 | Model 2 S2 | Model 3 S0 | Model 3 S1 | Model 3 S2 |
|---|---|---|---|---|---|---|
| Rows (train / validation) | 73,998 / 24,859 | | | 17,253 / 5,673 | | |
| Prevalence (validation) | 15.4% | 14.7% | 14.6% | 7.8% | 8.2% | 8.1% |
| PR-AUC (rule baseline) | 0.151 (0.154) | **0.235** (0.175) | **0.252** (0.192) | 0.083 (0.080) | **0.170** (0.104) | **0.335** (0.119) |
| ROC-AUC (CI) | 0.493 (0.484–0.503) | 0.628 | 0.663 | 0.521 (0.495–0.550) | 0.633 | 0.747 |
| Recall / precision / F1 at the frozen threshold | 0.27 / 0.15 / 0.20 | 0.49 / 0.21 / 0.29 | 0.54 / 0.24 / 0.34 | 0.34 / 0.09 / 0.14 | 0.45 / 0.12 / 0.19 | 0.31 / 0.43 / 0.36 |
| Alert rate | 27% | 34% | 32% | 30% | 30% | 6% |
| ECE | 0.008 | 0.012 | 0.011 | 0.015 | 0.014 | 0.012 |
| Feature-model ceiling, PR-AUC (audit) | 0.154 | 0.233 | 0.277 | 0.078 | 0.165 | 0.354 |
| Project-held-out PR-AUC (prevalence) | 0.165 (0.167) | 0.272 (0.172) | 0.289 (0.184) | 0.074 (0.083) | 0.075 (0.068) | 0.384 (0.082) |

## 3. Findings

1. **Null scenario:** behaves as null everywhere. ROC-AUC CIs contain 0.5, PR-AUC ≈ prevalence, F1 equals random alerting, and 20 label shuffles centre on 0.50. **No leakage detected.**
2. **L2 logistic regression is essentially at the achievable ceiling** for linear-additive simulations (S1). In S2 it falls short by 0.019–0.025 PR-AUC, because of the deliberately non-linear terms and interactions.
3. **Calibration is good** (ECE ≤ 0.017). **Overfitting is small.** Leading coefficients are stable, and the assumed directions are recovered in S1.
4. **Precision is inherently low** for these weak simulated signals: about 0.2 for Model 2, and 0.12 (S1) / 0.43 (S2) for Model 3. Raising recall costs many false alerts (Model 3 S2: recall 0.31 → 0.55 halves precision). The capacity curves should accompany any operating point.
5. **Project-held-out:** generalises for Model 2 (S1 / S2) and Model 3 S2. **Model 3 S1 does not** on unseen projects (PR-AUC 0.075 vs 0.068 prevalence, 760 rows).

## 4. Are Random Forest and XGBoost justified?

**Only narrowly, and only for S2.** The audit ceiling shows:
- **S1:** no headroom (logistic regression ≥ the ceiling on PR-AUC).
- **S2:** at most +0.025 (Model 2) and +0.019 (Model 3) PR-AUC to gain, which is about the width of the current validation CIs.

**Options for approval:**
- **(A) Recommended, given the timeline:** adopt **L2 logistic regression** as the PoC classifier for both models. Then move to the final sealed-test evaluation on the pre-declared scenario (S2, the most realistic of the three, with S0 as a control) and on to RAG and the application.
- **(B) Optional, time-boxed Stage 3B:** one pre-declared XGBoost (or Random Forest) per model on **S2 only, with S0 as a null control**, fixed small budget, accepted only if the PR-AUC gain is CI-separated. The expected gain is at most about 0.02.

## 5. Unresolved issues (simulation and features)

1. **Explanations are distorted by collinearity and rare levels:**
   - Model 3 `notice_type` duplicates method / category.
   - The ">180-day window" deadline status and rare countries get large coefficients even under S0.

   Before the dashboard uses "main contributing factors", the proposal is to drop `notice_type` (as Model 1 dropped first notice type), raise `min_frequency` for country and deadline status (e.g. to 200), and present factors at the original-feature level. This needs approval because it changes the feature set.
2. **Threshold drift:** training-derived thresholds give validation alert rates above the 30% cap (Model 2 S1 34%). The same effect appeared in Model 1. Thresholds must be re-derived at each refit, and alert rates monitored.
3. **Model 3 units:** repeat notices outside the 184 reference projects cannot be collapsed. The 460 duplicates removed are the identifiable part only.
4. **Model 2 packages:** 196 packages span split boundaries. Labels carry no package effect, so the impact is limited to shared project effects.
5. **Risk-band rule changed from v1 to v2** after a degenerate empty Medium band appeared in the training out-of-fold shares. This is disclosed. v2 uses fixed training quantiles (top 10% / next 20%).
6. **Prevalence sweeps (low / high) and drift scenarios** were not run (deferred, as instructed).
7. **Nothing here is evidence about real World Bank amendments or re-bids.**

## 6. Approval needed

| # | Decision |
|---|---|
| D1 | Adopt L2 logistic regression as the PoC model for both (option A), or run the time-boxed Stage 3B on S2 + S0 (option B) |
| D2 | Feature clean-up for explanations (drop `notice_type` in Model 3; raise rare-level thresholds) before final evaluation |
| D3 | Final sealed-test evaluation scope: which scenario(s) are primary (proposed: S2 primary, S0 control, S1 reported), single scoring with frozen thresholds and bands |
