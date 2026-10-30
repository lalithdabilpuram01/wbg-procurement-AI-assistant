# Model 1, Stage 2B: Linear and Regularised Regression (validation only)

**Date:** 2026-10-25 · **Models:** baselines, then OLS, Ridge, Lasso, Elastic Net, run in order with an audit after each. **Not run:** Random Forest, XGBoost, survival models, Models 2 and 3. **The test set was not scored.**

**Note on instructions:** the Stage 2B brief arrived truncated in section 4 ("Historical…"). Everything after that point follows the Stage 2A conventions: validation-only scoring, local JSONL tracking, reports in `ml/reports/`, and a stop for approval.

## 1. Runs and code

| Run (`ml/eda/…`) | Content |
|---|---|
| `stage2b_asof_ols_20261025T073356Z` | OLS audit, attempt 1: **superseded** (rank-deficient design, see §4.1). Kept as the audit trail; its records in `runs.jsonl` carry this run directory |
| `stage2b_asof_ols_…073443Z`, `…ridge_…073454Z`, `…lasso_…073525Z` | Sequential audit steps (as-of) |
| **`stage2b_asof_enet_20261025T073543Z`** | **Primary protocol, final:** all models |
| `stage2b_asof_fu365_enet_20261025T073731Z` | Sensitivity protocol (added post hoc, §3) |
| `stage2b_retro_enet_20261025T073602Z` | Secondary, retrospective protocol |
| `stage2b_summary_20261025T073816Z` | Cross-protocol table (`comparison.csv`) and chart |

- **Code:** `ml/stage2b/regression.py` (`--protocol asof|asof_fu365|retro --upto ols|ridge|lasso|enet`) and `ml/stage2b/summarize.py`. The split and test masking are reused from `ml/stage2a/split.py`.
- **Tracking:** records appended to `ml/experiments/runs.jsonl`.
- **Each run contains:** `regression_metrics.json`, `coefficients_*.csv`, `cv_curve_*.csv`, `ols_statsmodels_coefficients.csv`, `segments_by_*.csv`, `validation_predictions.csv` and `charts/01–04`.

## 2. Setup

**Target and prediction:**
- **Target:** ln(`duration_days`), where `duration_days` = signing date − first tender publication date.
- **Point prediction:** exp(ŷ), the conditional median, which suits MAE. A smearing-corrected mean is reported too: it is worse on MAE in every model (for example, Ridge as-of 77.2 vs 75.8).

**Features** (inventory `include` only):
- method, category, review type, region, grouped country (regional = "REGIONAL"), sector (the "FY17 -" prefix stripped);
- log1p(bid window), log1p(prior tender notices), project age, approval FY, income group (ordinal).

**First notice type is excluded:** in training it is identical to category (every REOI is Consultant Services), which is exact collinearity.

**Preprocessing:** one scikit-learn `Pipeline`, refitted inside every CV fold.
- One-hot encoding with levels below 20 training rows merged into "infrequent". OLS drops the first level.
- Median imputation; StandardScaler on numeric features.

**Tuning:**
- 5-fold `GroupKFold` by project on the training rows. Packages are nested in projects, so lots never straddle folds.
- Scoring: MAE in days.
- Every chosen hyperparameter lies **inside** its grid.

**Time-ordered CV feasibility.** An expanding-window scheme with as-of truncation inside each fold gives only two usable origins:

| Origin | Training rows | Evaluation rows |
|---|---:|---:|
| 2020-06-30 | 76 | 96 |
| 2021-06-30 | 259 | 90 |

76 rows cannot tune a 40-column design, so grouped K-fold is the tuning method. The two time-ordered origins are reported as a stability check for each chosen model.

## 3. Training protocols

| Protocol | Training rows | Definition | Status |
|---|---:|---|---|
| **as-of (primary)** | **462** (78 projects, 451 packages) | Training-period labels signed on or before 2022-06-30, as reported in Stage 2A | Deployment-realistic |
| as-of, ≥ 365 days follow-up | 372 (64 projects) | As-of 2022-06-30, **and** first tender on or before 2021-06-30 | **Sensitivity, added after the primary results were seen:** exploratory; its validation scores must not drive model selection |
| retrospective | 646 | All training-period labels; 184 outcomes became known after the cutoff | **Retrospective, not a deployment backtest** |

**Baselines** B0 / B1 / B1m are refitted on each protocol's own training rows. Validation is the same 353 contracts throughout.

## 4. Sequential model audit (primary, as-of)

### 4.1 OLS

**Attempt 1** (superseded):
- **Rank-deficient design:** 41 of 42 columns. A "REGIONAL" income level duplicated the country "REGIONAL" dummy, giving an infinite VIF.
- **Fix,** decided on training data only: income becomes ordinal (L < LM < UM < H), regional projects are median-imputed, and the regional effect is carried by country alone.

**Attempt 2:**
- Full rank (40 / 40); condition number 55.
- Training R² (log) 0.59; adjusted R² 0.56.
- 14 coefficients with p < 0.05.

**OLS findings:**
- **Overfitting:** training MAE 65.7 → CV 80.1 → validation 80.3 days.
- **Structural VIFs:** RFB 49 and Goods / Works 33 / 29, because method and category nest. Region vs country: up to 14. **Project age vs approval FY: 16 / 15** (together they encode the tender date).
- **Implausible country coefficients** from few projects: China −1.28, Kyrgyz Republic −1.05 on the log scale.
- **Validation MAE 80.3 vs B1 77.5,** worse in 72% of project-cluster resamples.

**Conclusion:** unregularised OLS is not adequate. Regularisation is needed.

### 4.2 Ridge (α = 10)

- **Fit:** training MAE 66.0, CV 75.9, validation **75.8**. The CV gap shrinks relative to OLS.
- **Coefficients are shrunk to plausible sizes:** QCBS +0.44, RFQ −0.36, bid window +0.26 per SD, project age −0.24 per SD.
- **Time-ordered checks:** beats B1 at both origins (60.5 vs 62.3; 55.0 vs 59.2).
- **Against B1:** paired MAE reduction 1.6 days, project-cluster CI **−5.6 to +9.0**. Not significant.
- **Persistent bias:** prior-review contracts are still under-predicted by 123 days.

### 4.3 Lasso (α = 0.0032)

- **Selection:** keeps 33 of 45 columns. Income and prior-tender count are near zero.
- **Validation:** **76.3** (CI vs B1 −7.3 to +9.0). Same bias pattern as Ridge.

### 4.4 Elastic Net (α = 0.01, l1_ratio = 0.1)

- **Validation:** **76.6** (CI vs B1 −6.8 to +8.3).
- **Coefficients:** 42 of 45 non-zero, close to Ridge.

## 5. Results across protocols (validation, n = 353)

| Protocol | B0 | B1 | OLS | Ridge | Lasso | Elastic Net | Best linear vs own B1 (95% CI, project / package clusters) |
|---|---:|---:|---:|---:|---:|---:|---|
| **As-of (primary)** | 97.8 | 77.5 | 80.3 | **75.8** | 76.3 | 76.6 | Ridge: −5.6..+9.0 / −4.0..+7.4. **Not significant** |
| As-of, ≥ 365 d follow-up (post hoc) | 101.8 | 79.6 | 78.4 | 74.7 | **72.3** | 72.8 | Lasso: −1.1..+16.5 / **+0.8..+13.7** |
| Retrospective | 104.6 | 82.2 | 74.5 | 74.8 | **71.0** | 71.1 | Lasso: −0.8..+23.4 / **+2.9..+20.0** |

All values are MAE in days.

**Other metrics, as-of:**
- Median absolute error: B1 47.0, Ridge 47.5.
- RMSE: B1 116.1, Ridge 113.0.
- Package-weighted MAE: B1 76.2, Ridge 77.4.

**Prediction intervals (P10–P90, from out-of-fold training residuals):**
- Validation coverage 0.81–0.85 as-of (nominal 0.80); median width about 200 days.
- Retrospective: 0.84–0.91; width about 300 days.

**Sanity:** all predictions are strictly positive.

## 6. Key finding: training-label truncation, not model capacity, is the bottleneck

Under the as-of rule, training tenders published late in the training period appear only if they were signed quickly. 184 slower outcomes are missing.

**Consequences:**
- The as-of training median is 150 days, against 177 days in the retrospective set.
- **Every as-of model under-predicts long tenders.** Bias in the "long" band (actual above the training p75) is about −170 days.
- **Prior-review contracts stay under-predicted:** Ridge as-of −123 days, but retrospective Elastic Net only −25.

**Evidence that truncation is the cause:**
- With complete outcomes (retrospective) or a complete follow-up window (as-of ≥ 365 days), Lasso and Elastic Net gain about 9–14% over their own B1, with package-cluster CIs above zero.
- Under the truncated primary set, the gain disappears.

**Caveats:**
- Project-cluster CIs include zero in every protocol; 60 validation projects is a small number of clusters.
- The ≥ 365-day variant was chosen after seeing primary results, so its scores are exploratory.

## 7. Other error patterns (primary, as-of, Ridge unless stated)

| Pattern | Evidence |
|---|---|
| Long tenders under-predicted | Long band (n = 76): MAE 185, bias −172 |
| Short tenders over-predicted | Short band (n = 86): MAE 43 (B1 58.5), bias +38 |
| Prior review | MAE 146 (B1 168); bias −123 |
| QCBS (n = 45) | Hardest method. Ridge MAE: 169 as-of, 161 with ≥ 365-day follow-up, 132 retrospective. B1 is about 151–153 in every protocol, so **as-of linear models are worse than B1 on QCBS**; this improves only with complete long outcomes |
| Regions | LAC (n = 20) MAE about 142 in every model; East Asia & Pacific improves (101 → 74); Europe & Central Asia gets worse (71 → 83) |
| Project overlap | New projects (n = 122): Ridge 67.8 vs B1 71.5. Seen projects (n = 231): 80.1 vs 80.7. No memorisation advantage |
| Project concentration | Excluding the top 5 projects: Ridge 81.5 vs B1 85.1 |
| Temporal | By validation quarter, Ridge ranges from 51 to 86, against B1's 65 to 85. Drift remains |
| CV vs validation (retrospective) | CV MAE (107–130) is far above validation (71–75), because the training period contains longer tenders than validation: the drift seen in Stage 2A |

## 8. Decision and recommendation

**Primary result:**
- Under the deployment-realistic as-of protocol, **no linear or regularised model significantly beats the method × category median (B1)**.
- Ridge is the best linear model (75.8 vs 77.5 days), with the best stability at the time-ordered checkpoints and interval coverage close to nominal.
- **B1 remains the reference baseline. No linear model is adopted yet.**

**Is Stage 3 (Random Forest) justified now? Not yet.** The limiting factor is that the label set loses its long durations when training is restricted to outcomes observable at the cutoff. More flexible models would not fix this, and with 462 rows they would overfit more. Proposed next steps, for approval:
1. **Decide the training-label design** before any non-linear model:
   - **(A)** an as-of *complete-follow-up window* as the primary rule (pre-registered this time, so it can be validated properly); or
   - **(B)** a censoring-aware model: a log-normal AFT survival model in which training-period tenders that are still unsigned at the cutoff enter as right-censored.

   B needs a decision on how to treat tenders that never link to a contract (Phase 1: about 39% within two years, mostly cancellations or reference changes, which are not true censoring).
2. **Pre-registered linear variants** under the chosen design: Ridge / Elastic Net plus two interactions (method × review type; bid window × method) and the declared tender-FY trend term (an `investigate` feature, reported separately).
3. **Only then, Stage 3** (Random Forest), if residuals still show structure that linear terms cannot capture.

**Unchanged:** the test set (616) is still unscored, and no amendment or re-bidding work has started.
