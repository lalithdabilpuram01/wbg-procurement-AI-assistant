# Module 2: Predictive models (`ml/`)

Phase 1 (data readiness and EDA) is complete. Training has **not** started; it waits for approval of
[`reports/PHASE_1_REVIEW.md`](reports/PHASE_1_REVIEW.md).

```
ml/
  phase1/common.py                  read-only Databricks guard, run folders, chart style
  phase1/s01_extract.py             read-only Silver/Gold extract -> eda/extract_<UTC>/data/*.parquet (+ Delta versions before/after)
  phase1/s02_tender_labels.py       cohort tender-duration label funnel (documented p02 rule), censoring, coverage
  phase1/s03_label_evidence_recheck.py  amendment / re-bidding evidence re-measured on Silver (counts only)
  phase1/s04_eda.py                 EDA statistics + 17 charts (real observed data only)
  phase1/s05_feature_inventory.py   feature inventory with measured missingness and leakage decisions
  stage2a/split.py                  Model 1 population + chronological split (test targets stripped on load)
  stage2a/split_audit.py            10-check split audit (no test targets read)
  stage2a/baselines.py              B0 / B1 / B1m median baselines, validation-only scoring, cluster bootstrap
  stage2b/regression.py             OLS / Ridge / Lasso / Elastic Net (--protocol asof|asof_fu365|retro --upto ...)
  stage2b/summarize.py              cross-protocol comparison of Stage 2B runs
  stage2c/PREREGISTRATION.json      frozen threshold (365 d), alert rules, protocols, search budgets (+ .sha256 lock)
  stage2c/advanced.py               B1 / Ridge / Random Forest / XGBoost (--protocol asof|cfw365 --models ...), saves models/*.joblib
  stage2c/survival_feasibility.py   survival-label audit (FAILED: no valid censoring indicator; no survival model fitted)
  stage2c/compare.py                consistent cross-model / cross-protocol validation tables
  final/FINAL_EVAL_CONFIG.json      frozen final-test protocol (cutoff 2023-06-30, 806 refit rows, rules, metrics)
  final/final_refit.py              refit B1 / Ridge / RF and freeze hashed test predictions (no test targets read)
  final/final_score.py              ONE-TIME test scoring (hash checks; --smoke uses validation rows; lock prevents re-scoring)
  final/frozen/                     FROZEN.json, TEST_SCORED.json, models/*_final.joblib, test_results/
  stage3a/features.py               real feature tables + chronological splits for Models 2 / 3 -> ml/features/
  synthetic/configs/sim_v1.json     frozen generator assumptions (S0 null, S1 linear, S2 non-linear); NOT World Bank findings
  synthetic/generator.py            simulated labels only -> synthetic/<m>_sim_v1/{labels/, generator/ (audit-only), PROVENANCE.json}
  stage3a/leakage_tests.py          pre-training leakage / reproducibility tests (audit; may read generator internals)
  stage3a/classify.py               prior / rule / logistic / L2 logistic baselines (never reads generator internals; test sealed)
  stage3a/recovery_audit.py, ceiling_audit.py   audit-only comparisons with the generator
  final23/common23.py               Model 2 / 3 feature policies, pipelines, explanation helpers
  final23/m3_cleanup_compare.py     single pre-declared Model 3 feature-cleanup comparison (validation)
  final23/FINAL_EVAL_CONFIG_M23.json frozen final protocol (S2 primary, S0 control)
  final23/final_refit23.py          refit + freeze test predictions, schemas, example API outputs (no test labels)
  final23/final_score23.py          ONE-TIME test scoring (--smoke on validation; lock frozen/TEST_SCORED.json)
  eda/<stage>_<UTC>/                run outputs (new folder per run; nothing overwritten)
  experiments/runs.jsonl            local append-only experiment log (MLflow not used yet)
  reports/                          Phase 1 and Stage 2A deliverables
```

Stage 2A (2026-10-25): `python ml/stage2a/split_audit.py`, then `python ml/stage2a/baselines.py`. Training of
linear or tree models waits for approval of [`reports/STAGE_2A_DECISION_REPORT.md`](reports/STAGE_2A_DECISION_REPORT.md).

## Re-run (from `procurement-intelligence/`, Python with databricks-connect: `/opt/anaconda3/bin/python3`)

```bash
python ml/phase1/s01_extract.py                                   # prints the extract dir
python ml/phase1/s02_tender_labels.py ml/eda/extract_<UTC>
python ml/phase1/s03_label_evidence_recheck.py ml/eda/extract_<UTC>
python ml/phase1/s04_eda.py ml/eda/extract_<UTC> ml/eda/tender_labels_<UTC>
python ml/phase1/s05_feature_inventory.py ml/eda/extract_<UTC> ml/eda/eda_<UTC>
```

## Rules

- Databricks access is read-only (`common.ro_sql`); no table, view or Volume is written.
- Outputs never contain notice text, bid or contract references, descriptions, supplier names or IDs, or contact data.
  Packages are grouped by a SHA-256 prefix of (project, normalised reference).
- `eda/extract_*/data/` is a local cache of public Gold/Silver columns. Keep it out of version control.
- Synthetic outcomes (Models 2 and 3) are not generated in Phase 1; when approved, they live under `ml/synthetic/`,
  separate from features, and every result is labelled as simulated.
