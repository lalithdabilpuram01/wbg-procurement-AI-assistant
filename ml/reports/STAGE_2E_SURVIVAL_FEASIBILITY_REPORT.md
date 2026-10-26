# Model 1, Stage 2E: Survival-Label Feasibility Audit

**Date:** 2026-10-25 · **Result: FAIL for a deployment-realistic survival model. The branch stopped; no survival model was fitted.**

**Run and inputs:**
- **Run:** `ml/eda/stage2e_survival_feasibility_20261025T080147Z`:
  - `survival_feasibility.json`;
  - `training_window_packages_classified.csv`;
  - `unlinked_share_by_*_training_window.csv`.
- **Code:** `ml/stage2c/survival_feasibility.py`.
- **Inputs:** the Phase 1 extract and linked-label table. Nothing was re-linked.
- **Unit:** tender package (project + normalised reference) in the 184 projects with notice references.

## Outcome classes

| | At the 2022-06-30 training cutoff (packages first published 2019-07-01 → 2022-06-30) | At the 2026-10-20 snapshot (all packages from 2019-07-01) |
|---|---:|---:|
| Packages | **1,172** | **3,283** |
| Signing observed (valid link) by the cutoff | **451** | 1,636 |
| Signed *after* the cutoff (known only in hindsight) | **145** | – |
| Award notice exists, but no DS00005 contract linked | **179** | 448 |
| Re-bid / replacement marker on the tender notice | 5 | 14 |
| Notice status "Cancelled" | **0** | 0 |
| Linked only through back-filled (invalid) notices | 23 | 32 |
| **No signal at all** (open, silently cancelled, re-referenced, or a linkage failure) | **369** | 1,153 (645 older than 730 days) |

The classes are ordered (first match wins): valid link → cancelled → re-bid marker → award notice without a contract → revised only → no signal.

## Checklist

1. **Observed signing events:** yes. 451 by the cutoff (1,636 by the snapshot).
2. **Genuinely known to be unfinished at the cutoff:** **no subset is identifiable at the cutoff itself.**
   - 721 packages were unsigned at 2022-06-30.
   - Only 145 of them are later seen to sign, which can be known only in hindsight.
   - At the cutoff they look exactly like the 369 with no signal and the 179 awarded without a contract record.
3. **Cancellations, replacements, re-bids, linkage failures:** separable only *after* the fact, and only partially.
   - Explicit cancellations: 0.
   - Re-bid markers: 5.
   - Awarded without a DS00005 contract: 179. These are linkage failures or contracts outside DS00005, and the event probably did occur.
   - Competing events are therefore heavily under-recorded.
4. **Valid right-censoring indicators:** **not available.** An "unsigned at cutoff" flag would mix true censoring, competing events and linkage failures.
5. **Informative censoring / linkage:** **yes.** The training-window unlinked share depends strongly on group:

   | Group | Unlinked share |
   |---|---|
   | Works | 39% |
   | Goods | 56% |
   | ECA / ESA / MENA | 35–36% |
   | West & Central Africa | 55% |
   | East Asia & Pacific | 65% |
   | LAC | 68% |
   | QCBS | 41% |
   | CQS | 66% |

   This violates the non-informative censoring assumption of Cox and AFT models.
6. **Counts:** see the table above. Of the unsigned at the cutoff, 145 are genuine (hindsight) censorings and 576 have an unresolved or competing outcome.
7. **Defensible survival dataset:** **no**, for a model that could be trained and used at a historical cutoff.

## Variant considered and not fitted

- **Design:** restrict to packages that are *eventually* signed, and treat the 145 later-signed ones as censored at the cutoff.
- **Why it isn't deployable:** choosing that population uses post-cutoff knowledge.
- **What it estimates:** the same conditional quantity as the regression models (duration given eventual signing).
- **Verdict:** a retrospective analysis, not a deployable survival model, and it cannot be compared fairly with the as-of regression results. It was not fitted, in line with the instruction not to force survival modelling.

## What would make survival analysis feasible later

- **Notice status history:** snapshots of procnotices status (Cancelled / Revised) collected from now on (D-20), so competing events are recorded when they happen.
- **Plan histories:** procurement-plan Process Status histories across versions (D-11), which date cancellations and replacements.
- **Linkage:** fixing the award-notice-without-contract gap (179 packages), for example by matching on the reference quoted in the award text against all DS00005 records.
