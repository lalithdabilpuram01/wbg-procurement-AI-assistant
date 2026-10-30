# Model Card: Model 3, Simulated Re-bidding Risk

> **SYNTHETIC OUTCOMES — NOT VALIDATED WORLD BANK RISK ACCURACY · Simulated — Demonstration Only**

| | |
|---|---|
| **Model** | L2 logistic regression (C = 0.32), scenario S2 of `sim_v1`, feature policy `m3_v2_clean`. Artefact `ml/final23/frozen/models/m3_S2_l2_final.joblib` |
| **Negative-control twin** | `m3_S0_l2_final.joblib` (evaluation only) |
| **Status** | Demonstration of an early-warning flag at tender publication. **No verified re-bid labels exist** |

## Intended use and output

- **Question:** at first publication of a tender, how does its *simulated* re-bid probability (within 12 months) rank?
- **Dashboard output:** **Low / Medium / High** simulated risk band. Cut-offs are frozen from refit out-of-fold probabilities:
  - **High:** p ≥ 0.148 (top 10%);
  - **Medium:** p ≥ 0.072 (next 20%);
  - **Low:** below 0.072.

  The display adds up to 3 displayed contributing inputs and the label "Simulated — Demonstration Only".
- **Probability:** kept for technical evaluation.
- **Meaning of "High":** a relative rank within the simulated population, not an established real-world risk category.

## Inputs (known at publication)

**Schema:** `ml/final23/frozen/m3_input_schema.json`.

| Input | Notes |
|---|---|
| `procurement_method` | |
| `procurement_category` | |
| `region_name` | |
| `country_grp` | |
| `sector` | |
| `deadline_status` | |
| `window_days` | Winsorised |
| `has_positive_window` | |
| `project_age_days` | Winsorised |
| `income_ordinal` | |

Rare levels (fewer than 263 refit rows) are grouped as "infrequent".

**Removed:**
- `notice_type` (duplicates method and category). It remains an *assumed generator input*; no predictive loss was detected.

**Never used:** re-bid wording or markers, notice status and versions, bidder counts.

## Training data and simulation

- **Features:** real de-duplicated cohort tender notices, FY2016–22, 22,926 rows.
- **Outcome:** **simulated** (S2):
  - main effects: short or missing window, RFB / QCBS, Works / Goods, early project, lower income (+); REOI, RFQ (−);
  - Works × short-or-no window;
  - U-shaped window length;
  - project and country random effects;
  - target prevalence 8%.

## Performance (sealed test FY2023–25, scored once; simulated)

| Metric | Value |
|---|---|
| PR-AUC | 0.334 (prevalence 0.082) |
| ROC-AUC | 0.735 |
| Alert threshold: recall / precision / F1 | 0.32 / 0.44 / 0.37 |
| ECE | 0.012 |
| Bands, simulated event rate | Low 4.4% · Medium 8.4% · High 31.5% (ordered) |
| Unseen projects | PR-AUC 0.341 |
| S0 null control | ROC-AUC 0.501; bands flat (passes) |

## Explanations

**Method:**
- Logistic contributions aggregated to original inputs.
- The three bid-window inputs (status, published flag, window length) are **shown as one "Bid window" factor**, because two of them are exactly collinear.
- Country and low-stability levels (28 of 63 design columns) are pooled and not displayed.

**Most frequent displayed factors:**
- High band: category, method, project age.
- Low band: project age, income group, method.

**Caveats:**
- About 13% of tenders show no factor, because no input raises the score above average.
- Explanations reflect the simulated assumptions, not evidence about real re-bidding.

## Limitations

- **Assumed truth:** labels and drivers are assumptions.
- **Correlated units:** repeat notices of one tender outside the 184 reference projects remain separate units.
- **Bands are relative:** they are fixed from refit scores, and band shares will drift in new periods.
- **Path to real labels:** human-verified re-bid evidence (`ML_LABEL_FEASIBILITY_AUDIT.md`, D-22 / D-23) and plan-version comparison.
