# PoC Cohort: 2,000-Project Selection Methodology

Decision **D-26** (approved 2026-10-21; see [docs/decisions/D-26_POC_COHORT_2000_PROJECTS.md](../docs/decisions/D-26_POC_COHORT_2000_PROJECTS.md)):
the interactive PoC uses **exactly 2,000 World Bank projects**, replacing the earlier 3,000-project target.

| Item | Value |
|---|---|
| Selection script | [`discovery/scripts/c01_select_poc_cohort.py`](../discovery/scripts/c01_select_poc_cohort.py) (logic in [`discovery/wbdisc/cohort.py`](../discovery/wbdisc/cohort.py), tests in `discovery/tests/test_cohort.py`) |
| Cohort manifest | [`poc_cohort_2000_manifest.csv`](poc_cohort_2000_manifest.csv): 2,000 rows, 54 columns |
| Eligible pool with selection flag | [`poc_eligible_pool_3120.csv`](poc_eligible_pool_3120.csv): 3,120 rows |
| Checks, representation, coverage | [`poc_cohort_2000_summary.json`](poc_cohort_2000_summary.json) |
| Seed | **20261021** (a constant in the script; changing it changes the cohort) |
| Selected on | 2026-10-21, from local snapshots dated 2026-10-20/21 |
| Network use | None. Only local files are read. |

Reproduce with `cd discovery && python3 scripts/c01_select_poc_cohort.py`. Before selecting, the script checks the SHA-256 of
every input against `data_cache/MANIFEST.jsonl` / `samples/MANIFEST.jsonl`, and stops if any input has changed. Two
consecutive runs produced byte-identical CSVs.

> **Reading the CSVs:** use `keep_default_na=False` (pandas) or equivalent. Namibia's country code is `NA`, which default
> parsers turn into a null.

## 1. Eligible pool (3,120 projects)

A project is eligible when all of the following hold:

1. it is in the v3 projects population (`data_cache/projects_v3_population.jsonl.gz`, 28,193 projects);
2. its v3 `status` is **Active** or **Closed** (Dropped and Pipeline are excluded);
3. it has ≥ 1 row in **DS00005** contract awards (FY2020+);
4. it has ≥ 1 notice in **DS00979** procurement notices.

This reproduces the 2026-10-21 audit count (3,142 projects with contracts and notices, less 20 Dropped and 2 Pipeline).
The script stops if the pool is not exactly 3,120.

## 2. Stratification variables

| Variable | Definition | Source |
|---|---|---|
| Region | v3 `regionname` as published (8 values, including "Other" for 16 projects) | v3 projects |
| Status | Active / Closed | v3 `status` |
| Approval-year band | Approval fiscal year binned: FY≤2014, FY2015–17, FY2018–20, FY2021–23, FY2024+, Unknown | v3 `fiscalyear`; if missing, the WB fiscal year derived from `boardapprovaldate` |
| Contract-volume quartile | Equal-frequency quartiles of the number of distinct `WB Contract Number`s per project, over the pool. Ties are broken by project-ID order. Bounds: Q1 1–14, Q2 14–37, Q3 37–88, Q4 89–15,322 | DS00005 |
| Country | v3 `countrycode` as published. 139 cohort projects carry a regional or multi-country code (e.g. `3W`) | v3 projects |
| Major sector | Dominant major sector: the largest summed `sector_percent` in v3 `major_sectors`, with the "FY17 - " prefix removed. Legacy names (e.g. "Energy and Mineral Resources") are kept as published, not merged | v3 projects |

Data notes:
- **Unknown approval year:** two pool projects, P120417 and P124073, have no fiscal year and no board-approval date. They
  form their own "Unknown" strata and were both selected.
- **Inconsistent approval year:** P125198 has v3 `fiscalyear` 2001, but its approval date implies FY2010. The published
  value is kept and the conflict logged in the summary JSON. Both values fall in the same band.

## 3. Procedure

1. **Explicit strata:** region × status × approval-year band × contract-volume quartile gives 237 non-empty strata.
2. **Allocation:** proportional (sampling fraction 2,000 / 3,120 = 0.641), rounded with the largest-remainder (Hamilton)
   method. Allocations sum to exactly 2,000, and every stratum receives at least one project.
3. **Random keys:** the pool is sorted by project ID, and each project gets `random_key` from
   `numpy.random.default_rng([20261021, 1])`.
4. **Within-stratum systematic sampling:**
   - Units in each stratum are ordered by (country code, major sector, random key).
   - The sampling interval is k = N_h / n_h.
   - The start is u·k, where u comes from `default_rng([20261021, 2])`; strata are processed in sorted order.

   The ordering spreads the sample across countries and sectors within each stratum. This is implicit stratification.
5. **Country-coverage repair:** every country with ≥ 2 eligible projects must be represented. Where one is missing, a
   project from that country is swapped in for a project in the **same stratum** from the most over-represented country.
   Because swaps stay inside a stratum, stratum counts and inclusion probabilities don't change. **No swaps were needed**
   in this run; all 2,000 projects were selected in step 4.
6. **Weights:** each project carries `inclusion_probability` = n_h / N_h and `design_weight` = N_h / n_h. Use these to
   produce pool-level (3,120) estimates from the cohort.

## 4. Verification

| Check | Result |
|---|---|
| Rows / unique project IDs | 2,000 / 2,000 |
| All IDs valid P-numbers; all selected projects eligible | Yes / Yes |
| Selected count per stratum equals its allocation | Yes (237 strata) |
| Byte-identical output on re-run | Yes |

Representation (largest absolute gap between pool share and cohort share):

| Dimension | Largest gap (percentage points) | Notes |
|---|---|---|
| Region | 0.15 | All 8 regions represented |
| Status | 0.05 | Closed 57.45%, Active 42.55% |
| Approval-year band | 0.09 | |
| Contract-volume quartile | 0.20 | 503 / 496 / 499 / 502 projects |
| Major sector | 0.73 | Public Administration is 14.42% of the pool and 15.15% of the cohort. All 15 sector values represented |
| Financing type | 0.43 | Not stratified; reported for information |
| Country | 0.25 | 155 of 159 countries represented. All 147 countries with ≥ 2 eligible projects are represented; 8 of the 12 countries with a single eligible project were selected. The largest count gap is Ethiopia: 27 selected vs. 32.1 under exact proportionality |

Country is implicit, not explicit, stratification, so individual country counts can drift by a few projects from
exact proportionality.

## 5. Data coverage of the cohort (local data, 2026-10-21)

| Area | Coverage |
|---|---|
| Contracts (DS00005, FY2020+) | 178,921 distinct contracts; 186,907 contract × supplier lines; US$85.2 bn. Median 37 per project (10th–90th percentile: 4–176) |
| Partial contract history | 996 projects were approved before FY2020, so DS00005 does not hold their earlier contracts. 363 of them have 6,448 legacy prior-review contracts (DS01004, FY2001–16). FY2017–19 post-review contracts are not public |
| Notices (DS00979 metadata only) | 243,533 in total: Contract Award 187,070; Request for Expression of Interest 31,187; Invitation for Bids 23,780; General Procurement Notice 1,239; Prequalification 256; other 1. Median 61 per project |
| IEG ratings (DS00053) | 750 projects (739 Closed, 11 Active), all ICR Reviews (ICRR). Outcomes: Highly Satisfactory 45, Satisfactory 280, Moderately Satisfactory 299, Moderately Unsatisfactory 83, Unsatisfactory 27, Highly Unsatisfactory 16. 410 Closed projects have no IEG rating yet |
| Loan statements (IBRD/IDA latest) | 1,597 projects |
| Notice full text and reference numbers | 184 projects (20,305 notices) from the Phase 1 sample |
| Full v3 project fields (ISR/ICR ratings, implementing agency, indicators) | 184 projects |
| Document metadata | Complete document list for 184 projects (Phase 1 sample). Partial lists for 174 more, which appear only as co-tagged projects; 14,268 records in total |
| Local PDFs / text files | 42 PDFs across 29 projects: 29 procurement plans and 3 each of PAD, ISR, ICR and ICRR. 27 text files across 4 projects |
| Parsed procurement plans | 25 projects |
| ISR series | 1 project (P163714) |

## 6. Additional data needed for this cohort (not downloaded)

These figures are counts of work, not authorisation to download. Each pull needs approval.

| Need | Projects | Volume | Basis |
|---|---|---|---|
| Notice full text and `bid_reference_no` (procnotices API), required to link notices to individual contracts | 1,816 | **223,237 notices** | Exact, from DS00979 |
| Full v3 project fields (`fl=*`) | 1,816 | 1,816 records | Exact |
| Document metadata (v3 wds, per project) | 1,816 | about 150,000 records | Estimate: Phase 1 sample rates of 75.2 documents per Active and 88.4 per Closed project |
| Main documents (text preferred over PDF; see below) | ≈ 2,000 | about 7,200 documents | Estimate |
| Procurement plans, latest version only | 1,975 without a parsed plan | about 1,975 PDFs | Exact project count |
| Procurement plans, all versions | 2,000 | about 82,600 | Estimate. Not recommended (see D-11) |
| Full ISR history | 2,000 | about 21,900 | Estimate |

How the main-documents figure breaks down:
- **Documents expected across the cohort:** these combine the 184 complete local lists with the estimate for the
  other 1,816 projects.
  - PAD ≈ 1,745
  - Project Paper ≈ 4,470
  - ISR ≈ 21,930
  - ICR ≈ 930
  - ICR Review ≈ 731
- **The 7,200 figure is a recommended minimum set per project:**
  - latest PAD or Project Paper
  - latest ISR
  - ICR and ICR Review where they exist
  - latest procurement plan
- **Already local:** of that minimum set, 12 PDFs and the 25 parsed plans are on disk.

No IEG, contract, notice-metadata, loan-statement or governance-indicator download is needed: those sources are complete
locally for every cohort project.
