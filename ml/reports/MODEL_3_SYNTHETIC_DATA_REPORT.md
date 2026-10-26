# Model 3 (Re-bidding Risk): Synthetic Outcome Data Report

> **SYNTHETIC OUTCOMES — NOT VALIDATED WORLD BANK RISK ACCURACY.** The features are real, sanitised World Bank tender-notice rows. Only `sim_rebid_12m` is simulated. Phase 1 found 0 verified re-bids. Cancellations, revisions and addenda are not re-bids.

## 1. Unit, prediction point and population

| Item | Value |
|---|---|
| Unit | A tender at its **first publication**: DS00979 IFB / REOI / prequalification notices (55,223 in the cohort), de-duplicated |
| De-duplication | Dropped **460** rows identical to another notice except for the ID (kept the earliest ID). Dropped **12** later notices of the same package where a reference exists (184 API projects). **Units: 54,751** |
| Residual dependence | Outside the 184 projects DS00979 has no reference number, so repeat or revised notices of one tender cannot be collapsed. This is handled by project-grouped CV and reported as a limitation |
| Prediction point / outcome | First publication date; simulated re-bid needed within 12 months (`sim_rebid_12m`) |
| Population | Published FY2016–FY2025 (12-month window complete; pre-FY2016 deadlines systematically equal the publication date). Out of window: 10,871 |
| Splits (by publication FY) | Train FY2016–21: **17,253** · Validation FY2022: **5,673** · Test FY2023–25: **20,954 (sealed)** |
| Projects | Train 1,118 · validation 793 (645 also in training; 4,913 rows) · test 1,192. Validation rows in unseen projects: 760 (148 projects). Packages crossing splits: 0 |

**Features:** `ml/features/m3_tender_features_v1.parquet`, all known at publication.
- **Included:** notice type, method, category, region, grouped country, sector, deadline status, published response window (days; missing when there is no positive window, 15.7%), has-positive-window flag, project age at publication, income group at publication FY (ordinal; 11.6% missing).
- **Excluded:** re-bid wording or markers (label evidence), notice status and versions, bidder counts, identical-group size, link counts, governance and macro values.

## 2. Generator (`ml/synthetic/generator.py`, config `sim_v1.json`; fixed before any fit; assumptions, not findings)

- b0 is solved for a population prevalence of **8%**. This is the central assumption; the only public floor is 0.4% of tender notices with explicit re-bid wording, which is not a rate.

| Scenario | Content | σ project / σ country | Seed |
|---|---|---|---:|
| **S0** | Null; no random effects | 0 / 0 | 20263025 |
| **S1** | Linear-additive main effects (below) | 0.4 / 0 | 20263026 |
| **S2** | S1 + Works × (short or no window) (+0.6) + U-shaped log-window (+0.25·(z² − 1)) | 0.4 / 0.3 | 20263027 |

**S1 main effects (log-odds):**

| Driver | Assumed effect |
|---|---|
| Window < 14 days | +0.5 |
| No positive window | +0.4 |
| log1p(window), standardised | −0.2 per SD |
| Notice type | REOI −0.3, prequalification +0.2 |
| Method | RFB +0.3, QCBS +0.2, RFQ −0.2 |
| Category | Works +0.4, Goods +0.2 |
| Project age < 365 days | +0.3 |
| Income group | L +0.3, LM +0.15, H −0.1 |

**Realised prevalence:**

| Scenario | Train | Validation | Test | Log-odds SD |
|---|---:|---:|---:|---:|
| S0 | 8.3% | 7.8% | 8.0% | 0 |
| S1 | 8.9% | 8.2% | 7.3% | 0.67 |
| S2 | 7.5% | 8.1% | 8.2% | 0.98 |

## 3. Separation, provenance and tests

**Artefacts:**
- Labels: `ml/synthetic/m3_sim_v1/labels/labels.parquet`.
- Internals: `ml/synthetic/m3_sim_v1/generator/` (audit only).
- Provenance: `ml/synthetic/m3_sim_v1/PROVENANCE.json`.

**Pre-training tests (all pass):**
- **Reproducibility:** labels regenerate identically.
- **Integrity:** feature file unchanged; no latent columns; units match.
- **S0 null:** 1 of 9 label-vs-feature tests has p < 0.05, and overdispersion is **0.93**.
- **S1 / S2:** 7 and 9 of 9 significant, with overdispersion 1.0 and 1.5.
- **Correlation guard:** passes.
- **Label shuffles (20, after training):** validation ROC-AUC mean 0.498 (S0), 0.500 (S1, SD 0.067) and 0.504 (S2, SD 0.043). The wide spread in S1 / S2 comes from project and country clustering of the labels. **A single shuffle can land at about 0.4–0.58 by chance, so never read one shuffle on its own.**

## 4. Limitations

- **Assumed truth:** the simulated drivers are assumptions. In particular, a "short window" effect is a hypothesis, not evidence.
- **Correlated units:** repeat notices of one tender outside the 184 reference projects remain as separate units, which makes units correlated within projects.
- **Ceiling:** project effects are unobservable. The validation ceiling with true probabilities is ROC-AUC 0.70 (S1) and 0.78 (S2).
