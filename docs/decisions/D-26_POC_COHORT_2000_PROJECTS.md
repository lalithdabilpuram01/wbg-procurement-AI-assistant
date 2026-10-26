# D-26: PoC Scope Is Exactly 2,000 World Bank Projects

| | |
|---|---|
| Status | **Approved** (2026-10-21, project architecture decision) |
| Supersedes | The earlier working target of 3,000 projects. That target was agreed in the brief and never recorded as a decision here |
| Related | D-11 (procurement-plan extraction on a bounded project set), D-12, D-20, D-23 in [PENDING_DECISIONS.md](PENDING_DECISIONS.md). None of them is changed by this record |

## Decision

The interactive PoC uses **exactly 2,000 World Bank projects**, plus their associated contracts, procurement notices,
procurement plans, evaluation records and relevant documents.

The cohort is a reproducible, stratified, seeded sample of the 3,120 eligible projects:
- **Eligible:** v3 status Active or Closed, at least one DS00005 contract (FY2020+), and at least one DS00979 notice.
- **Seed:** 20261021.
- **Method:** [cohort/COHORT_SELECTION_METHODOLOGY.md](../../cohort/COHORT_SELECTION_METHODOLOGY.md).
- **Manifest:** [cohort/poc_cohort_2000_manifest.csv](../../cohort/poc_cohort_2000_manifest.csv).

## Rationale

- **3,000 was effectively a census.** The 2026-10-21 audit found only 3,120 eligible projects, so a 3,000 cohort would
  have been 96% of the pool, with little room for stratified selection. At 2,000 the sampling fraction is 0.641, and
  representation can be stratified and verified. Every gap between pool share and cohort share is 0.73 percentage
  points or less.
- **Smaller ingestion load.** About 223k notices instead of about 355k would need full text; about 150k instead of
  about 220k document-metadata records.

## Consequences

- **Label volume falls.** IEG outcome labels in scope drop from about 1,185 to **750** (all ICR Reviews, 126 rated on
  the unsatisfactory side). ML feasibility conclusions that depend on label counts should be re-checked against the
  cohort.
- **Estimates need weights.** Analyses that generalise to the eligible pool must use the manifest's `design_weight`.
- **This is the bounded set for any later ingestion.** Medallion Bronze ingestion, document downloads and
  procurement-plan extraction would be scoped to these 2,000 project IDs. Each of those still needs its own approval:
  D-11, D-15 and the download plan in the methodology §6.
- **Changes need a new decision.** Re-selection with a different seed, eligibility rule or size requires a new decision
  record; the manifest is versioned by seed and selection date.
- **The pool is anchored to a snapshot.** The eligible pool reflects the 2026-10-20/21 local snapshots. The selection
  script refuses to run if the input checksums change. A cohort refresh policy is not part of this decision.
