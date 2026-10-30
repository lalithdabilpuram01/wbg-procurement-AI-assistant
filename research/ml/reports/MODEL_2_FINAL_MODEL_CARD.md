# Model Card: Model 2, Simulated Contract Amendment Probability

> **SYNTHETIC OUTCOMES — NOT VALIDATED WORLD BANK RISK ACCURACY**

| | |
|---|---|
| **Model** | L2 logistic regression (C = 0.032), trained on scenario S2 of `sim_v1`. Artefact `ml/final23/frozen/models/m2_S2_l2_final.joblib` (`{"pipeline", "policy", "min_frequency", "unstable_design_columns"}`) |
| **Negative-control twin** | `m2_S0_l2_final.joblib` (null scenario; evaluation only, never displayed) |
| **Status** | Demonstration of a contract-management risk flag. **Not a real amendment-risk model:** no verified amendment records exist publicly |

## Intended use

- **Question:** at contract signing, what is the *simulated* probability of an amendment within 24 months?
- **Output:** probability as a percentage, plus up to 3 displayed contributing inputs.
- **Optional demonstration review list:** flag when p ≥ 0.167. This was set for at most 30% of refit contracts as an assumed review capacity; on test it flagged 31%.
- **Not for:** decisions about contracts, suppliers, borrowers or staff.

## Inputs (all known at signing)

**Schema:** `ml/final23/frozen/m2_input_schema.json`.

| Input | Notes |
|---|---|
| `procurement_method` | |
| `procurement_category` | |
| `review_type` | |
| `region_name` | |
| `country_grp` | WDI code or "REGIONAL" |
| `sector` | |
| `days_from_approval` | |
| `signed_before_approval` | |
| `approval_fiscal_year` | |
| `income_ordinal` | L = 1 … H = 4; missing allowed |

Unknown or rare categories are mapped to "infrequent".

**Excluded:** contract amount (DS00005 snapshot may include revisions), supplier and JV data, descriptions, governance and macro values.

## Training data and simulation

- **Features:** real cohort contracts, FY2020–23, 98,857 rows.
- **Outcome:** **simulated** under the assumptions in `ml/synthetic/configs/sim_v1.json` (S2):
  - main effects: Works, prior review, Direct / individual / RFB methods (+); RFQ (−);
  - an approval-stage U-shape;
  - Works × prior review;
  - project and country random effects;
  - target prevalence 15%.

## Performance (sealed test FY2024, scored once; simulated)

| Metric | S2 | S0 (null control) |
|---|---|---|
| PR-AUC | 0.232 (prevalence 0.138) | 0.154 |
| ROC-AUC | 0.655 | 0.504 (chance; passes) |
| Recall / precision / F1 at threshold | 0.51 / 0.22 / 0.31 | – |
| ECE | 0.014 | – |
| Unseen projects: PR-AUC | 0.247 | – |

## Explanations

**Method:** coefficient × transformed input, aggregated to original inputs.

**Not displayed as drivers** (pooled):
- country contributions, so no country is implied to be risky;
- design columns with project-bootstrap sign agreement below 0.8 (65 of 166, mostly rare countries).

**Stable leading effects (S2, log-odds):**

| Effect | Coefficient |
|---|---:|
| Works | +0.62 |
| Consulting | −0.43 |
| Post review | −0.47 |
| Prior review | +0.29 |
| Direct Selection | +0.33 |
| RFQ | −0.28 |
| Individual consultant | +0.23 |

**Validity caveat:**
- Region and sector frequently appear as displayed factors, although the generator assigns them no effect. They absorb simulated country and project effects.
- The approval-stage U-shape cannot be represented linearly.
- **Explanations describe what the model learned from simulated data, not World Bank risk factors.**

## Limitations

- **Assumed truth:** labels, prevalence and drivers are assumptions. Precision is low (about 0.22–0.28) by design.
- **Threshold drift:** alert-rate caps do not transfer exactly across periods (30% → 31%). Re-derive at each refit.
- **Path to a real model:** STEP amendment records (not public) or verified extraction from award notices, ISRs and ICRs.
