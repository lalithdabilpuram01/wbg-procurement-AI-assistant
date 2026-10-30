# Module 2: Experiment Plan (Stages 2–6; not executed)

**Status:** plan for approval. Nothing in this document has been run. Split sizes come from the Phase 1 label set (measured 2026-10-25).

## 1. Common protocol

Every stage follows the same eight steps:

1. Train on the training data only.
2. Score with the pre-defined metrics.
3. Compare with the previous stage on the **identical** splits.
4. Run residual and error analysis, including the train–validation gap.
5. Run the leakage checks: feature timing audit, label-shuffle test, and for synthetic data the null scenario S0.
6. Report metrics per segment (method, category, region, review type, FY).
7. Log the run to MLflow.
8. Record a written go/no-go on adding complexity.

**Preprocessing:** one scikit-learn `Pipeline` / `ColumnTransformer`, fitted on the training fold only and applied unchanged to validation and test. The fitted pipeline object is logged with each model.

| Step | Linear / logistic models | Random Forest / XGBoost |
|---|---|---|
| Missing numeric values | Median imputation (training fold) plus a missing indicator | Native NaN handling (XGBoost) or median + indicator (RF) |
| Missing categorical values | An explicit "Unknown" or "REGIONAL" level | Same |
| Rare categories | Fewer than 20 rows in the training fold → "Other" | Same |
| Encoding | One-hot (drop-first for OLS); country by out-of-fold target encoding, or grouped one-hot | Ordinal / one-hot; no scaling |
| Skewed features | log1p for bid window, prior notices, amounts; splines for project age | None needed |
| Scaling | Standardise (training fold) | None |
| Multicollinearity | One WGI composite or PCA (training fold); L2 / Elastic Net | Note in the importance analysis |
| Outliers | Kept. Huber loss is a robustness check | Kept |
| Class imbalance (Models 2 and 3) | Class weights; threshold tuned on validation | `scale_pos_weight` / class weights; no SMOTE as the default |
| Dependence | Folds grouped by package (Model 1) or project (Models 2 and 3) | Same |

**Feature sets** come from `ML_FEATURE_INVENTORY.csv`:
- **Primary** = `include` only.
- **Sensitivity** = `include` + `investigate`; this adds WGI / macro / CPIA, commitment and contract amount.

## 2. Model 1: tender completion time (real labels)

### Population and splits (temporal, by first tender publication date)

| Set | Tender publication window | Labels | Projects |
|---|---|---:|---:|
| Train | 2019-07-01 to 2022-06-30 (FY2020–22) | 646 | 88 |
| Validation | 2022-07-01 to 2023-06-30 (FY2023) | 353 | 60 |
| **Test (held out, evaluated once)** | 2023-07-01 to 2024-10-20 | 616 | 59 |
| Excluded: left-truncated | before 2019-07-01 | 56 | – |
| Monitoring only: right-truncated | after 2024-10-20 | 443 | – |

**Why this split:** only labels with **at least 730 days of follow-up** enter train, validation and test (1,615 in total), so the durations in each split are complete apart from 27 that exceed 730 days.

**Tuning:** rolling-origin CV inside the training period. Folds are grouped by package so that lots never straddle folds. After model selection, refit on train + validation, then score the test set once.

**Project vs time conflict.** 27 of the 59 test projects (271 of 616 test rows) also appear in training.
- **Primary metric:** temporal, matching the real use case of predicting new tenders in known projects.
- **Sensitivity:** test restricted to the 345 rows from projects unseen in training. This measures generalisation to new projects.
- **Uncertainty:** 95% CIs by **project-cluster bootstrap** (2,000 resamples).

### Stages

| Stage | Models | Notes |
|---|---|---|
| 2: baselines | (a) global training median; (b) median by method × category, falling back to method, then global, when a cell has fewer than 20 rows | Fixes the bar every model must clear |
| 3: linear | OLS, Ridge, Lasso, Elastic Net on ln(days) | α and l1_ratio by grouped CV. Audit coefficients, residuals by segment, VIF, train/validation gap. Back-transform with smearing; report both median and mean predictions. Prediction intervals by split-conformal calibration on validation |
| 3b: survival check | Log-normal / Weibull AFT on contract labels including the right-truncated 443 (censored at the snapshot) | Decides whether censoring materially changes the estimates. Unlinked packages are **not** used as censored (they are mostly cancellations or link failures) |
| 4: Random Forest | RandomForestRegressor on ln(days) | Only if Stage 3 residuals show non-linearity or interactions (method × review, bid window × method). Small grid: depth, min_samples_leaf, max_features |
| 5: XGBoost | XGBRegressor (squared error on ln days; quantile objective for P10 / P90) | Only if RF or residual evidence justifies it. Controlled random search (40 trials) with early stopping on validation. SHAP; stability across seeds |
| 6: selection | Simplest model within the bootstrap CI of the best validation MAE | Then a single test evaluation |

**Metrics:**
- MAE (days, primary), median absolute error, RMSE;
- MAE improvement over the segmented-median baseline (with CI);
- P10–P90 interval coverage and width;
- per-segment MAE;
- for survival: C-index and integrated Brier score.

**Size caution:** 646 training labels with about 40 encoded levels. Regularised linear models are expected to be competitive; tree ensembles must show a CI-separated gain.

## 3. Models 2 and 3: amendment and re-bidding (synthetic outcomes)

**Splits:** temporal by prediction date (unit counts measured 2026-10-25).
- **Model 2 (signing FY):**
  - train FY2020–22: 73,998 contracts;
  - validation FY2023: 24,859;
  - test FY2024: 28,949;
  - FY2025+ excluded, because the 24-month window is incomplete at the 2026-10-20 snapshot.
- **Model 3 (publication FY):**
  - train FY2016–21: 17,358 tender notices;
  - validation FY2022: 5,895;
  - test FY2023–25: 21,047;
  - FY2026+ excluded, because the 12-month window is incomplete;
  - pre-FY2016 notices (2,009) excluded, because IFB deadlines were systematically equal to the publication date.

**Grouping and uncertainty:** GroupKFold by project inside training; a project-held-out sensitivity; cluster-bootstrap CIs.

| Stage | Models |
|---|---|
| 2 | Class-prior baseline; a simple rule (e.g. Works & prior review; short window & RFB), scored as a ranking |
| 3 | Logistic regression (unpenalised, L1, L2, Elastic Net), with class weights. Audit calibration (reliability curve, Brier), coefficients versus the generator's true coefficients (recovery check) |
| 4 | Random Forest classifier, only if Stage 3 misses scenario non-linearity |
| 5 | XGBoost classifier, only if justified. Platt / isotonic calibration on validation; SHAP |
| 6 | Selection across the scenario grid (S0–S5), not one scenario |

**Metrics:**
- Precision, recall, F1;
- PR-AUC (primary), ROC-AUC, Brier score, calibration plot;
- confusion matrix at an operational threshold: the top 10% of units by risk, matching an assumed review capacity, or the threshold giving precision ≥ 2× prevalence, chosen on validation.

Accuracy is never reported alone.

**Labelling:** every synthetic result is labelled "simulated outcome; not real-world accuracy".

## 4. Model selection criteria (Stage 6)

1. Held-out performance against the baseline (CI-separated).
2. Generalisation: temporal and project-held-out gaps.
3. Calibration (classifiers) and interval coverage (Model 1).
4. Stability across methods, regions, FY and seeds.
5. Interpretability: coefficients or SHAP consistent with domain logic.
6. Operational cost: training and inference time, dependencies.
7. Reproducibility.

**Rule:** choose the simplest model whose primary metric falls within the bootstrap CI of the best.

## 5. Experiment tracking

**Workspace check (2026-10-25, read-only):**
- Databricks MLflow tracking is **available**: `/api/2.0/mlflow/experiments/search` responds, with no experiments yet.
- The legacy workspace model registry is **disabled**, so models would be registered in Unity Catalog.
- The `mlflow` client is **not installed** in the local environment (`/opt/anaconda3`).

**Proposal (needs approval):**
- Install `mlflow` locally.
- Create one experiment per model: `/Shared/wbg_module2/{m1_tender_time, m2_amendment_sim, m3_rebid_sim}`.
- Fallback if not approved: a local append-only `ml/experiments/runs.jsonl` with the same fields.

**Logged per run:**
- model family and hyperparameters;
- dataset version (Gold Delta versions, extract manifest), cohort manifest SHA-256;
- label rule version (`tender_label_rule_p02_v1`) or `sim_version` + scenario + seed;
- feature list with timing class, preprocessing pipeline;
- split definition and CV folds, random seeds;
- metrics (overall and per segment), the CI method;
- artefacts: charts, residuals, SHAP;
- selection rationale.

**Never logged:** notice text, contact details, supplier identifiers or credentials.

## 6. Proposed first experiment

**Model 1, Stage 2:**
- the global median and the method × category median baselines;
- on the 646 / 353 / 616 temporal split;
- primary feature set.

This needs no new data, no synthetic data and no new dependencies apart from optional MLflow.
