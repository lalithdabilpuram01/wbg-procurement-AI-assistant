# Module 2: Exploratory Data Analysis Report (Phase 1)

**Date:** 2026-10-25. All figures are **real observed data**, measured in this phase. No amendment or re-bidding outcome charts are shown, because no verified real labels exist.

**Runs:**
- Statistics: `ml/eda/eda_20261025T070501Z/eda_summary.json`
- Charts: `ml/eda/eda_20261025T070501Z/charts/`
- Labels: `ml/eda/tender_labels_20261025T070133Z/`

**Populations:**

| | Population | Size |
|---|---|---:|
| **L** | Valid tender-duration labels (cohort, 184 projects with notice references) | 2,114 |
| **C** | Cohort contracts (Gold `gold_contract_analytics`; Model 2 unit) | 178,921 |
| **T** | Cohort tender notices (DS00979 IFB / REOI / prequalification; Model 3 unit) | 55,223 |

**Country indicators** are joined with the approved two-year lag (D-30), but they are current-vintage values.

## A. Dataset overview

| Table | Rows | Key unique | Projects | Notes |
|---|---:|---|---:|---|
| `gold_contract_analytics` | 178,921 | yes | 2,000 | FY2020–27. Supplier lines are ×1.269 per contract, so never join lines before aggregating |
| `gold_procurement_notices` | 243,533 | yes | 2,000 | 62,193 rows in identical-metadata groups (kept, not de-duplicated) |
| Tender notices (T) | 55,223 | yes | 1,860 | REOI 31,187; IFB 23,780; prequalification 256. 3,999 have API detail |
| Label set (L) | 2,114 | yes | 145 | 1,691 packages. The country-context join adds no rows |

- **Grain:** contract → project is N:1. 27.3% of labelled contracts share a package (lots).
- **Contract amount (C):** median US$29,923, skew 64.3. On log10 the skew is 0.02. 4 contracts have an amount of 0.
- **Published response window (T):** median 18 days; no positive window for 18.3% of notices.

![funnel](../eda/eda_20261025T070501Z/charts/12_label_funnel.png)
![populations](../eda/eda_20261025T070501Z/charts/16_contract_and_tender_populations_by_fy.png)
![amount and window](../eda/eda_20261025T070501Z/charts/17_contract_amount_and_bid_window.png)

## B. Numerical analysis (population L)

| Feature | Median | p25–p75 | Skew | IQR outliers | Missing |
|---|---:|---|---:|---:|---:|
| Duration (days) | 144 | 92–251 | 2.11 | – | 0% |
| ln(duration) | 4.97 | – | −0.62 | – | 0% |
| Published bid window (days) | 19 | 14–29 | 13.6 (max 549) | 49 | 0% |
| Project age at tender (days) | 906 | 483–1,432 | 1.05 | 31 | 0% (17 tenders before approval) |
| Prior tender notices in project | 19 | 7–36 | 1.75 | 101 | 0% |
| GDP growth, t−2 | see JSON | | | | 5.3% |
| Inflation, t−2 | see JSON | | | | 5.5% |
| WGI (6 indicators), t−2 | see JSON | | | | 4.9% |
| CPIA IRAI, t−2 | see JSON | | | | **26.4%** |

**Implications:**
- **Use ln(duration) as the regression target.**
- Bid window, prior notices and commitment need log1p / log transforms.
- Outliers are kept; robustness is tested in Stage 3.

![distribution](../eda/eda_20261025T070501Z/charts/01_duration_distribution.png)
![numeric](../eda/eda_20261025T070501Z/charts/02_numeric_feature_histograms.png)

## C. Categorical analysis (population L)

- **Methods with at least 20 labels:** ICS 665, RFB 756, QCBS 290, CQS 255, RFQ 102, LCS 20. QBS (9), E-auction (2), RFP (10) and FBS (2) are rare and should be grouped as "Other".
- **Categories:** Consulting 1,244, Goods 460, Works 314, Non-consulting 96.
- **First notice type:** REOI 1,244, IFB 863, prequalification 7. REOI equals consulting by construction.
- **Review type:** Post 1,743, Prior 367, missing 4.
- **Cardinality:** 79 countries; 12 primary sectors. Sector names carry "FY17 -" taxonomy prefixes, which must be normalised before grouping.

## D. Relationships with duration

All group differences are significant on a Kruskal–Wallis test. This is descriptive only: associations, not causes.

| Grouping | Median days (n) | KW p |
|---|---|---|
| Method | RFQ 72 (102) · CQS 110 (255) · ICS 125 (665) · RFB 137.5 (756) · LCS 215.5 (20) · **QCBS 375 (290)** | 1e-116 |
| Review type | Post 123 (1,743) · **Prior 324 (367)** | 5e-93 |
| Category | Non-consulting 81 · Goods 130 · Works 134 · Consulting 160.5 | 6e-12 |
| Region | MENA 104 · ECA 106 · EAP 122 · ESA 156 · SAR 160 · WCA 161.5 · **LAC 235** | 5e-29 |
| Country (top 15 by count) | Pakistan 71 … Bangladesh 198.5 · Mozambique 227 | 8e-65 |
| Sector | Transport (FY17) 231.5; Social Protection 103 | 2e-41 |
| Tender FY | 2020: 176.5 · 2021: 166 · 2022: 190 · 2023: 129 · 2024: 143 · 2025: 124 · 2026: 103 (truncated) | – |

**Spearman correlations with ln(duration):**

| Feature | rho |
|---|---:|
| Bid window | 0.31 |
| ln(commitment), snapshot | 0.24 |
| WGI control of corruption | 0.19 |
| WGI government effectiveness | 0.13 |
| GDP growth | −0.10 |
| Project age | −0.07 |
| Prior tenders, inflation, GDP per capita | about 0 |

**Multicollinearity:**
- The four core WGI indicators correlate with each other at rho 0.68–0.79; their VIF is up to 5.1 (rule of law).
- Every other feature has a VIF below 1.9.
- So use one WGI composite or a principal component fitted inside the training folds, or L2 regularisation.

**Interactions worth testing later:**
- method × review type (prior-review QCBS);
- category × region;
- bid window × method.

![method](../eda/eda_20261025T070501Z/charts/03_duration_by_method.png)
![category](../eda/eda_20261025T070501Z/charts/04_duration_by_category.png)
![region](../eda/eda_20261025T070501Z/charts/05_duration_by_region.png)
![notice type](../eda/eda_20261025T070501Z/charts/06_duration_by_notice_type.png)
![country](../eda/eda_20261025T070501Z/charts/07_duration_by_country_top15.png)
![review](../eda/eda_20261025T070501Z/charts/08_duration_by_review_type.png)
![sector](../eda/eda_20261025T070501Z/charts/09_duration_by_sector.png)
![fiscal year](../eda/eda_20261025T070501Z/charts/10_duration_by_fiscal_year.png)
![correlations](../eda/eda_20261025T070501Z/charts/14_spearman_correlations.png)

## E. Data quality and bias

| Issue | Evidence | Consequence |
|---|---|---|
| Selection bias (method) | Label coverage: QCBS 68.6% · RFB 30.3% · RFQ 2.9% · Direct 0% | The model is valid only for advertised methods; report this scope |
| Selection bias (geography) | Coverage: LAC 3.9% (Bolivia 1 of 2,474) vs ESA 26.1%. Only 184 of 2,000 projects are linkable | Missing not at random. Report results per segment; do not generalise to the cohort |
| Concentration | Top 10 projects hold 36.3% of labels; median 7 labels per project | Use project-cluster bootstrap CIs; check a project-held-out sensitivity |
| Back-filled notices | 265 tenders published on or after signing | Excluded under the documented rule |
| Left truncation | 56 labels from tenders before 2019-07-01 (median 352 days) | Exclude them |
| Right truncation / censoring | FY2026 median 103 days. 39% of packages never link within 730 days | Use the 730-day full-follow-up window; unlinked packages are not clean censoring |
| Lots | 578 contracts in 155 multi-contract packages | Group CV folds by package |
| Missing values (MNAR) | CPIA missing 26.4% (0% for low-income, 75% for upper-middle-income). Regional projects have no country data (4.9%) | Add missing flags plus a "REGIONAL" level; never impute with zero |
| Snapshot leakage | Commitment and DS00005 amounts are current values; the WGI history is re-estimated | Mark as "investigate" in the inventory; excluded from primary experiments |

![missingness](../eda/eda_20261025T070501Z/charts/15_feature_missingness.png)
![coverage by method](../eda/eda_20261025T070501Z/charts/13_label_coverage_by_method.png)
![KM](../eda/eda_20261025T070501Z/charts/11_km_package_to_signing.png)

## Key discoveries

1. **The cohort has 2,114 valid real labels, not 3,782.** Only 1,615 have full two-year follow-up, and the earliest-years training slice is small (646).
2. **Procurement method and review type dominate.** A segmented median baseline will be hard to beat; linear models must show added value.
3. **The published bid window is the strongest numeric signal,** which supports tender publication as the prediction point.
4. **Governance and macro variables show weak associations** (|rho| ≤ 0.19). They are current-vintage, so they belong in a sensitivity run, not the primary model.
5. **The label set is non-random,** by method, geography and project. Every metric must be reported by segment.
