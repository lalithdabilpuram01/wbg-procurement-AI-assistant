# Model 2 (Contract Amendment Probability): Synthetic Outcome Data Report

> **SYNTHETIC OUTCOMES — NOT VALIDATED WORLD BANK RISK ACCURACY.** The features are real, sanitised World Bank contract rows. Only the outcome `sim_amended_24m` is simulated, from **assumed** relationships. Phase 1 found 0 verified positive and 0 verified negative real amendment labels.

## 1. Unit, prediction point and population

| Item | Value |
|---|---|
| Unit | Contract (`wb_contract_number`), from Gold `gold_contract_analytics` (178,921 cohort contracts) |
| Prediction point | Contract signing date |
| Simulated outcome | Amended within 24 months of signing (`sim_amended_24m`) |
| Population | Contracts signed FY2020–FY2024. The 24-month window is complete at the 2026-10-20 snapshot. FY2025–27 (51,115 contracts) are out of window |
| Splits (by signing FY) | Train FY2020–22: **73,998** · Validation FY2023: **24,859** · Test FY2024: **28,949 (sealed)** |
| Projects | Train 1,401 · validation 1,060 · test 1,053. 900 validation projects also appear in training (22,815 of 24,859 validation rows); 160 are unseen (2,044 rows) |
| Shared packages | 196 multi-contract packages (lots) are signed across fiscal-year boundaries and so span splits. Labels have no package effect, so lots share only the project effect |

**Features:** `ml/features/m2_contract_features_v1.parquet` (SHA-256 in `FEATURES_MANIFEST.json`), all known at signing.
- **Included:** method, category, review type, region, grouped country, sector, days from board approval to signing, signed-before-approval, approval FY, income group at signing FY (ordinal).
- **Missing values:** income 8.2% (regional projects); approval interval 3 contracts.
- **Excluded:** contract amount, supplier and JV fields, descriptions, snapshot aggregates, governance and macro values.

## 2. Generator (`ml/synthetic/generator.py`, config `ml/synthetic/configs/sim_v1.json`)

Coefficients were fixed before any classifier was fitted and were not tuned on results. They are domain **assumptions**, not World Bank findings.

**Model:** logit P = b0 + main effects (+ S2 terms) + project effect u_p (+ country effect v_c).
- b0 is solved so that the population mean probability is **15%**. This is the central assumed prevalence; the only public floor is 0.03% of contracts with amendment wording, which is not a rate.
- y ~ Bernoulli(p), drawn with a fixed seed per scenario.

| Scenario | Content | σ project / σ country | Seed |
|---|---|---|---:|
| **S0** | **Null.** Constant p; independent of every feature; no random effects | 0 / 0 | 20262025 |
| **S1** | Linear-additive main effects (below) | 0.4 / 0 | 20262026 |
| **S2** | S1 + Works × prior review (+0.6) + U-shaped project stage (+0.3·(z² − 1)) | 0.4 / 0.3 | 20262027 |

**S1 main effects (log-odds):**

| Driver | Assumed effect |
|---|---|
| Category | Works +0.8, Goods +0.2, Consulting −0.2 |
| Review type | Prior +0.6 |
| Method | Direct Selection +0.4, Individual Consultant +0.3, RFB +0.3, RFQ −0.3 |
| Days from approval to signing (standardised) | −0.25 per SD |
| Signed before approval | +0.2 |
| Income group | L +0.3, LM +0.15, UM 0, H −0.1 |

A missing driver contributes 0.

**Realised prevalence:**

| Scenario | Train | Validation | Test | Log-odds SD |
|---|---:|---:|---:|---:|
| S0 | 15.4% | 15.4% | 15.2% | 0 |
| S1 | 15.4% | 14.7% | 14.6% | 0.61 |
| S2 | 15.6% | 14.6% | 13.8% | 0.85 |

## 3. Separation, provenance and reproducibility

| Artefact | Path | Read by training? |
|---|---|---|
| Real features | `ml/features/m2_contract_features_v1.parquet` | Yes |
| Synthetic labels | `ml/synthetic/m2_sim_v1/labels/labels.parquet` (`unit_id, scenario, sim_label, seed, sim_version, data_kind=SYNTHETIC`) | Yes, joined on `unit_id` only |
| Generator internals | `ml/synthetic/m2_sim_v1/generator/internals.parquet` (logit, p, u_project, v_country) and `constants.json` (b0, standardisation constants) | **No.** Audit scripts only |
| Provenance | `ml/synthetic/m2_sim_v1/PROVENANCE.json` (SHA-256 of config, features, labels, internals) | – |

**Pre-training tests** (`ml/stage3a/leakage_tests.py`; run `ml/eda/stage3a_leakage_20261025T151418Z`; **all pass**):
- **Reproducibility:** `generator.py --verify` regenerates identical labels.
- **Feature integrity:** the feature file is unchanged; it has no label or latent column names; label units equal feature units.
- **Static check:** `classify.py` never references internals, `generator/` or `constants.json`.
- **S0 null:** 1 of 8 label-vs-feature tests has p < 0.05 (chance level), and project-rate overdispersion is **1.02** (binomial = 1).
- **S1 / S2:** 8 of 8 significant, with overdispersion 2.1 and 3.0, as designed.
- **Correlation guard:** no numeric feature has |Spearman| ≥ 0.95 with the logit.
- **After training:** 20 label shuffles give a validation ROC-AUC mean of 0.500 (S0), 0.506 (S1) and 0.501 (S2).

## 4. Limitations

- **Assumed truth:** the relationships, prevalence and effect sizes are assumptions. Feature importance learned from these labels reflects the generator, not real amendment drivers.
- **Unobservable effects:** project effects (σ 0.4) are unobservable by design, which caps achievable discrimination. The validation ceiling with true probabilities is ROC-AUC 0.66 (S1) and 0.71 (S2).
- **Snapshot attributes:** real-world amendment risk would depend on contract value and supplier attributes, which are excluded because the DS00005 snapshot may already reflect revisions (Stage 2A review).
