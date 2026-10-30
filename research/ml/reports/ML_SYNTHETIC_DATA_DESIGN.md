# Module 2: Synthetic Outcome Design for Amendment and Re-bidding (design only)

**Status:** design for approval. **No synthetic labels or data have been generated.** Synthetic outcomes are used only because Phase 1 found 0 verified positive and 0 verified negative real labels for both targets (`ML_DATA_READINESS_REPORT.md` §4).

## 1. Principle: real features, simulated outcomes

The simulation is **semi-synthetic**:
- **Inputs:** real, sanitised cohort rows.
  - Model 2: cohort contracts from Gold `gold_contract_analytics`.
  - Model 3: cohort tender notices from Gold `gold_procurement_notices`.
- **Outputs:** only the outcome label is simulated.

This keeps the feature distributions, missingness, class mix and correlations realistic without inventing feature data. No supplier identifiers, free text or notice text are used (D-14 / D-14b).

> **Scope of any synthetic metric.** Performance on simulated labels measures how well a model recovers the *assumed* data-generating process. It is **not** evidence of accuracy on real World Bank amendments or re-bids, and must never be presented as such.

## 2. Prediction units and windows

| | Model 2: amendment | Model 3: re-bidding |
|---|---|---|
| Unit | Contract (`wb_contract_number`) | Tender notice (`notice_op_id`; IFB / REOI / prequalification) |
| Population | All 178,921 cohort contracts. Primary experiments use the 127,806 contracts signed FY2020–FY2024 | All 55,223 cohort tender notices. Primary experiments use the 44,300 notices published FY2016–FY2025 |
| Prediction time | Signing date | Publication date |
| Simulated outcome | `sim_amended_24m` | `sim_rebid_12m` |

## 3. Data-generating process

For unit *i* in project *p*:

```
logit P(y_i = 1) = b0 + f(x_i) + g(x_i) + u_p + v_c(i) + e_i
y_i ~ Bernoulli(P)
```

| Term | Content |
|---|---|
| `b0` | Solved numerically so the mean prevalence matches the scenario target |
| `f(x)` | Additive main effects from the declared driver list (§4) |
| `g(x)` | Pre-registered non-linear terms and interactions (§4) |
| `u_p ~ N(0, σ_p²)` | Project random effect: unobserved implementing-agency capacity, which creates within-project correlation |
| `v_c ~ N(0, σ_c²)` | Country random effect |
| `e_i ~ Logistic noise` | Irreducible randomness; sets the Bayes-optimal ceiling |

**Reproducibility:**
- Seeds are recorded in the simulation manifest: one master seed and per-scenario derived seeds.
- The generator is deterministic given seed and scenario.

## 4. Domain assumptions (stated assumptions, not World Bank findings)

The directions below are hypotheses drawn from general public-procurement practice. Effect sizes are scenario parameters, chosen and frozen **before** any model is fitted.

### Model 2 drivers

- **Category:** Works > Goods > Consulting > Non-consulting (variation orders are common in civil works).
- **Contract size:** larger contracts are more likely to be amended. The effect is non-linear, with a log10-amount spline that saturates above about US$10M. The amount enters the *generator* only, as a contract attribute.
- **Review type:** prior review raises the probability.
- **Method:** Direct Selection and individual consultants are more likely to have time extensions.
- **Project stage:** contracts signed late in the project have less time to be amended (U-shaped).
- **Interaction:** Works × large amount.

### Model 3 drivers

- **Bid window:** short published windows (< 14 days), and no usable window, raise the risk.
- **Method:** RFB and QCBS carry more risk than RFQ; consulting REOIs carry less.
- **Project age:** early-project tenders carry more risk (capacity is still being built).
- **Category:** works and complex goods carry more risk.
- **Interaction:** Works × short window.
- **Governance:** country WGI enters **only** in a declared governance scenario. Its history is current-vintage, so a primary scenario must not make it the dominant driver.

### Prevalence scenarios

| Scenario | Model 2 prevalence | Model 3 prevalence |
|---|---:|---:|
| Low | 5% | 3% |
| Central | 15% | 8% |
| High | 30% | 15% |

These values are **assumptions**. The only related public measurements are floors, not rates:
- 0.03% of contracts carry amendment wording;
- 0.4% of tender notices carry an explicit re-bid marker;
- 31.9% of plan activities are cancelled, which is not the same as failed.

## 5. Scenario and sensitivity grid (to avoid favouring a model family)

| Scenario | Purpose |
|---|---|
| S0: **null** (labels independent of features) | Leakage guard: every model must score PR-AUC ≈ prevalence and ROC-AUC ≈ 0.5 |
| S1: linear-additive | A logistic model should be near-optimal; tree models must not win by chance |
| S2: non-linear + interactions (central) | Tests whether Random Forest or XGBoost add value |
| S3: high noise (small signal-to-noise ratio) | Tests stability and calibration under weak signal |
| S4: strong project / country random effects | Tests grouped generalisation (project-held-out) |
| S5: prevalence sweep (low / central / high) | Imbalance handling and threshold behaviour |

Every model family runs on every scenario with the same protocol, and the results are reported as a grid. A model is preferred only if it wins on the scenarios it should win **and** does not overfit S0.

## 6. Provenance and separation

**Storage:**
- Simulated labels go to `ml/synthetic/<sim_version>/labels.parquet`, keyed by unit ID.
- Columns: `sim_*` label, `sim_version`, `scenario`, `seed`, `generated_at`, `data_kind = "SYNTHETIC"`.
- Generator configuration (coefficients, σ, seeds) and latent values (`u_p`, `v_c`, logit) go to `ml/synthetic/<sim_version>/generator/`. **No feature pipeline may read this folder.**

**Feature isolation:**
- The feature table is built by the same pipeline as for real data, from Gold only, without access to the generator.
- A unit test asserts that the feature matrix has no `sim_*` columns, no latent columns, and no column hashed identical to a generator latent.

**Leakage tests before any synthetic training run:**
- the S0 null check;
- a label-shuffle check;
- a "generator inputs only" upper-bound model that reports the ceiling.

**Tracking:** every experiment carries MLflow tags `label_source=synthetic` and `sim_version`, and every chart and report is captioned "SIMULATED OUTCOMES".

**Storage policy:** synthetic labels are never written to Databricks Silver or Gold. If storage is ever approved, they belong in a separate, clearly named sandbox schema.

## 7. Limitations

- **Learned assumptions only:** the models learn the assumed relationships, so feature importance reflects the generator, not reality.
- **Missing real drivers:** contractor performance, site conditions and scope changes have no public proxy.
- **Not decision-grade:** synthetic results cannot be used to set operational thresholds. They demonstrate the pipeline, monitoring and explainability only.
- **Path to real labels:**
  - Re-bidding: human verification of the 476 existing candidates (D-22 / D-23), plus plan-version comparison.
  - Amendment: STEP amendment records, which are not public.
