# Synthetic Outcome Design: Stage 2A Review Note (proposed adjustments)

**Date:** 2026-10-25 · **Applies to:** `ML_SYNTHETIC_DATA_DESIGN.md`. The original is unchanged; this note lists adjustments for approval.

**Status:** no synthetic outcomes generated and no Model 2 or 3 training. Nothing in this note has been implemented.

## 1. Only values knowable at the prediction time drive the generator

| Model | Generator inputs in primary scenarios (inventory class `include`, known at the prediction date) | Removed from primary scenarios |
|---|---|---|
| Model 2 (prediction at signing) | Procurement category, method, review type, days from board approval to signing, signed before approval, region, income group at signing FY, primary sector | Contract amount; multi-supplier (JV) flag; supplier-country relation; anything from Gold that is snapshot-derived or post-signing |
| Model 3 (prediction at publication) | Notice type, method, category, published response window and deadline status (DS00979 single published deadline), project age at publication, region, income group at publication FY, primary sector | API notice status and versions; reference-sharing counts; identical-metadata group size; any re-bid marker; governance and macro values |

**Rule:** the generator reads its inputs from the same feature table the models will use, filtered to `decision == include`. A unit test asserts the generator's input list is a subset of that set.

## 2. Contract amounts stay out of the primary amendment generator

DS00005 is a single snapshot, and whether amounts are revised in place is unverified (needs D-20 snapshots). The original design's size effect and "Works × large amount" interaction therefore move to an **optional scenario, S-amt**, clearly labelled.

The primary interaction becomes **Works × prior review**. Prior review is assigned from value thresholds at planning time, so it carries size information legitimately.

## 3. Prevalence is always presented as simulated

- Scenario configs carry `prevalence_assumed: true` and a `basis` text.
- The basis cites only the observed *floors* (0.03% amendment wording; 0.4% explicit re-bid markers) and states that they are not rates.
- Report tables show the prevalence column as "simulated prevalence".
- No chart may show a simulated prevalence beside a real World Bank figure without the "SIMULATED" caption.

## 4. S0 must be genuinely null

The original formula includes project and country random effects in every scenario. For S0 this is corrected:
- `y ~ Bernoulli(π)`, independent and identically distributed.
- σ_project = σ_country = 0; no time trend; no dependence on row order, unit ID or any feature.
- A dedicated random stream is used, seeded separately from the feature pipeline.

**Acceptance test:** on S0, every model's validation ROC-AUC 95% CI (cluster bootstrap) must contain 0.5, and PR-AUC must be ≈ π. Otherwise the pipeline has a leak and all synthetic runs stop.

## 5. No leakage of random effects or generator metadata through joins

- **Storage of latent values:** `u_project`, `v_country`, the logit and any latent risk are written only to `ml/synthetic/<sim_version>/generator/`. The feature builder must not import the generator module or read that folder (unit test: import graph plus path check).
- **No project identity as a feature:** no `project_id`, no project-level target encoding, and no "project prior outcome rate" built from simulated labels.
- **Country:** country target encoding, if used, is computed out-of-fold only.
- **Grouped evaluation:** CV is grouped by project, and a project-held-out evaluation is mandatory, so a model cannot memorise `u_project` across folds.
- **Row order and keys:** label files are keyed by the real unit ID and shuffled with a recorded seed, so row order carries no latent information.
- **Correlation guard:** a leakage test fits a model on all features plus noise and checks that no single feature reaches |rho| > 0.95 with the simulated logit. (Only declared drivers should correlate, and they should correlate partially because of noise.)

## 6. Three separated artefacts

| Artefact | Content | Readable by |
|---|---|---|
| Features | Built from Gold by the normal pipeline (real data) | Training code |
| Labels `ml/synthetic/<sim_version>/labels.parquet` | `unit_id`, `sim_label`, `scenario`, `seed`, `sim_version`, `data_kind = SYNTHETIC` | Training code (join on `unit_id` only) |
| Generator internals `ml/synthetic/<sim_version>/generator/` | Config, coefficients, σ, seeds, latent values | Generator and audit tests only |

Synthetic labels are never written to Databricks Silver or Gold.

## 7. Interpretation guard

**Synthetic results:**
- Every synthetic metric is reported under the heading "Recovery of the assumed data-generating process (simulated outcomes)".
- The MLflow / JSONL tags carry `label_source=synthetic`.
- Synthetic results never share a table with Model 1's real metrics.
- The decision report states that synthetic scores are **not** World Bank predictive accuracy.

## 8. Additional adjustments from Stage 2A findings

- **Temporal drift:** the real tender-duration data drifts (training median 177 days vs validation 129). Add an optional scenario S-drift, with a prevalence or effect trend by fiscal year, to test temporal robustness. Primary scenarios stay stationary.
- **Same split discipline:** the same temporal splits and the same "test scored once" rule apply to synthetic experiments.
- **Model 3 unit de-duplication:** DS00979 has no package reference, so a REOI and the later IFB for one package are separate units. Report this dependence. Where API references exist (184 projects), report a package-grouped sensitivity.
