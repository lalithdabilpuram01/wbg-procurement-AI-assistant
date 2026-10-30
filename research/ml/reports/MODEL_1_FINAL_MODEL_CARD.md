# Model Card: Model 1, Tender Duration (Ridge, log-duration)

| | |
|---|---|
| **Model** | Ridge regression on ln(days), α = 31.6. Artefact `ml/final/frozen/models/ridge_final.joblib` (scikit-learn 1.5.1 Pipeline: preprocessing + model). SHA-256 in `ml/final/frozen/FROZEN.json` |
| **Benchmark shipped with it** | B1, the method × category historical median (`b1_final.joblib`) |
| **Version and date** | `model1_ridge_final`, refit 2026-10-25, historical cutoff 2023-06-30 |
| **Status** | PoC decision support. Not for automated decisions; not a production model |

## Intended use

- **Question:** at the moment a tender is first published, how many days until the contract is likely to be signed, with what uncertainty, and how likely is it to exceed one year?
- **Users:** World Bank procurement officers and task teams, for planning and early follow-up. Not for evaluating staff, borrowers or suppliers.
- **Outputs:**
  - median estimate exp(ŷ);
  - P10–P90 range;
  - P(duration > 365 days), to be used as a ranked risk score;
  - the B1 historical median as context.

## Inputs (all known at tender publication)

| Group | Features |
|---|---|
| Procurement | Method; category; review type (prior or post); published bid window (days, log1p) |
| Project | Region; country (countries with fewer than 20 training rows grouped; regional projects = "REGIONAL"); primary sector; project age at tender (days since board approval); approval FY; prior tender notices in the project (as-of, log1p) |
| Context | Income group at the tender's fiscal year (OGHIST, point-in-time; ordinal) |
| **Excluded** | Contract amount, supplier data, award information, notice revisions, ISR / IEG ratings, snapshot aggregates, current-vintage WDI / WGI / CPIA (`ML_FEATURE_INVENTORY.csv`) |

**Strongest effects** (multiplicative on days, other inputs held fixed):

| Feature | Effect |
|---|---|
| QCBS | ×1.48 |
| RFQ | ×0.74 |
| Prior review vs post review | ×1.25 vs ×0.80 |
| Consultant services | ×1.27 |
| Each SD of log bid window | ×1.24 |
| Each SD of project age | ×0.82 |
| LAC region | ×1.28 |

These are associations, not causal effects.

## Training data

- **Rows:** 806 contracts (as-of 2023-06-30: tenders first published 2019-07-01 → 2023-06-30 and signed by the cutoff), from 101 projects.
- **Source:** the 2,000-project PoC cohort (D-26), linked through procnotices references (184 projects linkable).
- **Labels:** valid under the documented back-fill rule (Phase 1 / Stage 2A).

## Performance

**Held-out test** (616 contracts, tenders 2023-07 → 2024-10; evaluated once):

| Metric | Ridge | B1 benchmark |
|---|---|---|
| MAE | 76.7 days (CI 62.7–89.3) | 74.3 (difference not significant) |
| Median absolute error | 62.4 days | 55.0 |
| P10–P90 interval coverage | 0.84 | – |
| Long-tender (> 365 d) PR-AUC | 0.54 | 0.37 |
| Long tenders at the frozen threshold: recall / precision / F1 | 0.39 / 0.56 / 0.46 | 0.59 / 0.52 / 0.56 |
| At a 20% review capacity: recall / precision | 0.83 / 0.43 | – |

**Where it is weakest:**
- long tenders, under-predicted by about 207 days;
- QCBS (MAE 139);
- one large project (MAE 72 vs B1 36).

## Limitations and risks

- **Population:** linked, signed contracts only. Tenders that are cancelled or never linked (about 39% of packages) are absent, so estimates are conditional on the tender being signed.
- **Coverage:** advertised methods only, with no direct contracting. Thin coverage of LAC and EAP. Test results dominated by 5 projects (53% of rows).
- **Drift:** durations are shortening over time. Training-derived alert thresholds under-alert on newer tenders. Re-derive thresholds and monitor before alerting.
- **Long tail:** median predictions rarely exceed 365 days. Use the probability and interval for long-tender risk.
- **No survival model:** censoring cannot be established (Stage 2E).
- **Not blind:** a test-window median was exposed in Phase 1 diagnostics. See the final test report for disclosures.

## Maintenance

- **Retraining:** refit with the same as-of rule at a new cutoff (`ml/final/final_refit.py` pattern). Re-derive the alert threshold from training out-of-fold scores. Compare against B1 on a new held-out period.
- **Monitoring:** MAE vs B1, interval coverage and alert rate by quarter, method and region.
- **Data improvements that would help most:**
  - procnotices references for all 2,000 projects (only 184 are linkable now);
  - procurement-plan estimated amounts and planned dates (D-11);
  - snapshots of notice status (D-20).
