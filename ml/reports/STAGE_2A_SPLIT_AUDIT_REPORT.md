# Model 1, Stage 2A: Split Audit Report

**Date:** 2026-10-25 · **Result:** **PASS** on all 10 checks. No serious leakage was found, so the split is safe to use.

**Run and inputs:**
- Script: `ml/stage2a/split_audit.py`. Split definition: `ml/stage2a/split.py` (single source of truth).
- Output: `ml/eda/stage2a_split_audit_20261025T072430Z/` (`split_audit.json`, `counts_by_*.csv`, `split_assignment.csv`, chart).
- Inputs: the Phase 1 label and feature files. SHA-256 values are recorded in the JSON.
- No Databricks access was needed.

**Test targets were not read.** `split.load()` strips the target and every outcome-date column from test rows. Target-dependent checks use train and validation only.

| # | Check | Result |
|---|---|---|
| 1 | Date boundaries (inclusive, on the first tender publication date) | Train 2019-07-01 → 2022-06-30 (observed 2019-07-12 → 2022-06-30). Validation 2022-07-01 → 2023-06-30 (observed 2022-07-07 → 2023-06-30). Test 2023-07-01 → 2024-10-20 (observed 2023-07-06 → 2024-10-17). Contiguous; no gap and no overlap. **PASS** |
| 2 | Population total | 646 + 353 + 616 = **1,615 unique contracts. PASS** |
| 3 | The other 499 valid labels | 56 left-truncated (first tender before 2019-07-01: only contracts signed on or after that date are in DS00005, so long durations are over-represented). 443 with insufficient follow-up (first tender after 2024-10-20: fewer than 730 days before the 2026-10-20 snapshot, so only quickly-signed tenders appear). 56 + 443 = 499. **PASS** |
| 4 | Duplicate contract IDs | 0. **PASS** |
| 5 | Package overlap across splits | 0 of 1,246 packages (596 / 276 / 374 per split). The first tender date is a package-level value, so all lots of a package fall in one split. **PASS** |
| 6 | Project overlap across splits | Projects 88 / 60 / 59. Shared: train ∩ validation 40, train ∩ test 27, validation ∩ test 39, all three 23. 253 of 353 validation rows are in training projects (100 are in new projects). 425 of 616 test rows are in train or validation projects (191 in unseen projects). **Allowed for a temporal design and reported**; a project-held-out view is computed (see the decision report) |
| 7 | Counts by FY, method, category, country, project | Train FY2020–22 (178 / 211 / 257). Validation FY2023 (353). Test FY2024–25 Q1 (513 / 103). No method appears later that is absent from train. **Concentration:** the top 5 projects hold 25.2% of train, 36.3% of validation and **52.6% of test** rows; the largest single project has 54 / 37 / 85 rows. Countries 55 / 43 / 44. Sparse methods (LCS 15, QBS 7, RFP 6, e-auction 2, FBS 2 in total) are present. Files: `counts_by_*.csv` |
| 8 | Feature availability at tender publication | Method agrees with the first tender notice's DS00979 method in **99.94%** of rows (1 RFP/RFB mismatch). Category agrees **100%**. The prediction date equals that notice's publication date in **100%** of rows. The prior-tender count, recomputed independently with strictly earlier dates, has 0 mismatches. Income group: OGHIST, point-in-time. **Residual risks:** the bid window comes from the current API notice (an in-place deadline extension cannot be ruled out; 0 revised notices among the labels); review type is a current DS00005 value (normally fixed in the plan). **PASS** |
| 9 | Prediction date and the two-year cutoff | Prediction date = the earliest publication among the package's tender notices. Cutoff 2024-10-20; minimum follow-up 733 days. Train / validation durations over 730 days: 25. Signing after the snapshot: 0. **Label look-ahead:** 184 training outcomes were signed after the training period ended (31 after the validation period ended). Using them keeps training durations complete; a strictly as-of variant is reported as a sensitivity. **PASS** |
| 10 | Lots and shared contracts | Multi-contract packages: train 13 (63 contracts), validation 38 (115), test 77 (319; largest 37). Lots cannot cross splits (check 5). Within a split they are correlated (24 of 51 train / validation multi-contract packages have identical durations), so the CIs use project- and package-cluster bootstrap, and a package-weighted MAE and a package-deduplicated baseline are reported. **PASS** |

## Disclosure on test-period exposure

While sizing the split in Phase 1, before this stage:
- a diagnostic command printed the median of the test window (141.5 days);
- the Phase 1 EDA reports per-FY medians that include FY2024 and FY2025, which overlap the test window.

Neither figure was used for any Stage 2A decision. B0 and B1 have one choice, the minimum group size k, and it was made by cross-validation inside the training period. The test set is unscored.

## Representativeness notes carried forward

- The test period is more concentrated by project than training (top 5 projects hold 52.6% vs 25.2%). Test results will need per-project reporting.
- The method mix shifts: RFB rises from 30% of train to 42% of test, and individual consultants fall from 34% to 24%.
