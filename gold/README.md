# Gold Layer Jobs (Databricks Connect, Serverless)

Gold tables are analytical models built only from the accepted `workspace.wbg_silver` tables. They follow the
Silver conventions:

- reuse of `silver.common` (session, DQ report, create-only write);
- dry run by default; a write needs `--mode write --confirm-create`;
- an independent verifier that recomputes the table in plain Python;
- a baseline that proves Bronze, Silver, masks and the cohort are unchanged.

Design guidance comes from `silver/runs/silver_audit_20261024T043611Z/` (`GOLD_READINESS_ASSESSMENT.md`,
`SILVER_RELATIONSHIP_REPORT.md`).

## Layout

```
gold/
  project_portfolio/transform.py / checks.py / run_project_portfolio_gold.py / verify_project_portfolio_gold.py
                     gold_project_portfolio: current-state accumulating snapshot, one row per cohort project (2,000)
  contract_analytics/transform.py / checks.py / run_contract_analytics_gold.py / verify_contract_analytics_gold.py
                     gold_contract_analytics: contract-grain fact, one row per current DS00005 contract (178,921)
  procurement_notices/transform.py / checks.py / run_procurement_notices_gold.py / verify_procurement_notices_gold.py
                     gold_procurement_notices: notice-grain fact, one row per DS00979 notice (243,533)
  country_context/transform.py / checks.py / run_country_context_gold.py / verify_country_context_gold.py
                     gold_country_context: one row per WDI economy x calendar year 2000-2025 (5,642)
  financing_analytics/transform.py / checks.py / run_financing_analytics_gold.py / verify_financing_analytics_gold.py
                     gold_financing_analytics: one row per IBRD loan / IDA credit instrument (2,685)
  procurement_kpis/kpi_queries.py / run_procurement_kpis_dry_run.py / verify_procurement_kpis.py
                     PROPOSED dashboard KPI views (D-23 pending; nothing created): 7 vw_kpi_* SQL views over the 5 Gold tables
  common/baseline.py shared pre-write baseline + unchanged checks (Bronze/Silver/Gold versions, rows, grants, masks, cohort)
  tests/             synthetic-frame tests (run with the Silver suite)
  runs/<run_id>/     baseline, dq_report.json, verification JSON, design/dry-run reports
```

## Run (from `procurement-intelligence/`)

```bash
python -m gold.project_portfolio.run_project_portfolio_gold                     # dry run
python -m gold.project_portfolio.verify_project_portfolio_gold --dry-run --run-id <dry_run_id>
python -m pytest silver/tests gold/tests -q -p no:cacheprovider
```

## Rules for every Gold table

1. Aggregate each source to the target grain before joining. Never sum contract amounts over supplier lines or notice links.
2. Keep contract values, IBRD/IDA financing, project commitments and loan transactions in separate, labelled columns. No ratios, conversions or reconciliations.
3. Use 0 only for counts from complete cohort-filtered sources. Use NULL plus a `has_*` flag for partial coverage or unavailable data.
4. Regional projects get no country context. Country joins use the validated `wdi_country_code`.
5. No personal or free-text fields: supplier names/IDs, descriptions, notice text, team leaders, raw records (D-14/D-14b).
6. Name snapshot fields `*_snapshot` and record the source as-of dates. Snapshot fields are not historical ML features.

## Run log

| Run | Result | Report |
|---|---|---|
| `gold_project_portfolio_20261024T050137Z` | **Dry run only.** 2,000 rows × 61 cols; 44/44 checks; independent verifier 46/46 (60 columns recomputed, 0 mismatches; contract total US$85,248,987,799.45 from supplier lines); 13 CHECK constraints and 28 NOT NULL hold on the data; `wbg_gold` not created; awaiting approval | [`runs/gold_project_portfolio_20261024T050137Z/GOLD_DESIGN_AND_DRY_RUN_REPORT.md`](runs/gold_project_portfolio_20261024T050137Z/GOLD_DESIGN_AND_DRY_RUN_REPORT.md) |
| `gold_project_portfolio_20261024T062250Z` | **Created `workspace.wbg_gold` (schema) and `gold_project_portfolio` (Gold table #1): 2,000 rows × 61 cols; Delta v69.** Pre-write 44/44; post-write 58/58 (60 columns recomputed, 0 mismatches; 178,921 contracts = US$85,248,987,799.45; 243,533 notices; 612 IBRD; 2,073 IDA; 750 IEG; 184 details; 1,861 matched / 139 regional NULL; 0 personal values); 17 Bronze + 18 Silver tables, grants, mask and cohort unchanged | [`runs/gold_project_portfolio_20261024T062250Z/FINAL_DQ_REPORT.md`](runs/gold_project_portfolio_20261024T062250Z/FINAL_DQ_REPORT.md) |
| `gold_contract_analytics_20261024T064531Z`, `…065059Z` | Dry runs (superseded): a Spark analysis error in a reported check; then 1 blocking privacy-name check on two flag columns, which were renamed. Data unchanged | `runs/gold_contract_analytics_20261024T065059Z/dq_report.json` |
| `gold_contract_analytics_20261024T065844Z` | **Dry run only.** 178,921 rows × 54 cols; 41/41 checks; independent verifier 18/18 (53 columns recomputed, 0 mismatches; US$85,248,987,799.45 from supplier lines; per-project equal to Gold #1); 16 CHECK and 32 NOT NULL hold on the data; awaiting approval | [`runs/gold_contract_analytics_20261024T065844Z/GOLD_DESIGN_AND_DRY_RUN_REPORT.md`](runs/gold_contract_analytics_20261024T065844Z/GOLD_DESIGN_AND_DRY_RUN_REPORT.md) |
| `gold_contract_analytics_20261024T143908Z` | **Created `gold_contract_analytics` (Gold table #2): 178,921 rows × 54 cols; Delta v73.** Pre-write 41/41; post-write 24/24 (53 columns recomputed, 0 mismatches; US$85,248,987,799.45 rebuilt from supplier lines; per-project equal to Gold #1; baseline equal to the approved dry run); all 36 existing tables, grants, mask and cohort unchanged. Only deviation: approved documentation comments | [`runs/gold_contract_analytics_20261024T143908Z/FINAL_DQ_REPORT.md`](runs/gold_contract_analytics_20261024T143908Z/FINAL_DQ_REPORT.md) |
| `gold_procurement_notices_20261024T153851Z` | Dry run (superseded): 1 blocking privacy-name check on two reference-sharing *count* columns, which were renamed `*_bid_ref_count`; a deadline status renamed `equals_publication_date_no_window`. Data unchanged | `runs/gold_procurement_notices_20261024T153851Z/dq_report.json` |
| `gold_procurement_notices_20261024T154709Z` | **Dry run only.** 243,533 rows × 52 cols; 38/38 checks; independent verifier 18/18 (51 columns recomputed, 0 mismatches; per-project per-type equal to Gold #1); 17 CHECK and 27 NOT NULL hold on the data; awaiting approval | [`runs/gold_procurement_notices_20261024T154709Z/GOLD_DESIGN_AND_DRY_RUN_REPORT.md`](runs/gold_procurement_notices_20261024T154709Z/GOLD_DESIGN_AND_DRY_RUN_REPORT.md) |
| `gold_procurement_notices_20261024T161522Z` | **Created `gold_procurement_notices` (Gold table #3): 243,533 rows × 52 cols; Delta v68.** Pre-write 38/38; post-write 23/23 (51 columns recomputed, 0 mismatches; per-project per-type equal to Gold #1; baseline equal to the approved dry run); all 37 existing tables, grants, mask and cohort unchanged. Only deviation: approved documentation comments | [`runs/gold_procurement_notices_20261024T161522Z/FINAL_DQ_REPORT.md`](runs/gold_procurement_notices_20261024T161522Z/FINAL_DQ_REPORT.md) |
| `gold_country_context_20261024T165818Z` | **Dry run only.** 5,642 rows (217 economies × 2000–2025) × 41 cols; 36/36 checks; independent verifier 14/14 (40 columns, exact values, 0 mismatches; first attempt failed on a verifier unpacking bug, report kept); 13 CHECK and 20 NOT NULL hold on the data; awaiting approval | [`runs/gold_country_context_20261024T165818Z/GOLD_DESIGN_AND_DRY_RUN_REPORT.md`](runs/gold_country_context_20261024T165818Z/GOLD_DESIGN_AND_DRY_RUN_REPORT.md) |
| `gold_country_context_20261024T174956Z` | **Created `gold_country_context` (Gold table #4): 5,642 rows × 41 cols; Delta v52.** Pre-write 36/36; post-write 19/19 (40 columns recomputed, exact values, 0 mismatches; 0 duplicates, 0 aggregates; baseline equal to the approved dry run); all 38 existing tables, grants, mask and cohort unchanged. Only deviation: approved table-comment wording (keyed by calendar year; not ML features) | [`runs/gold_country_context_20261024T174956Z/FINAL_DQ_REPORT.md`](runs/gold_country_context_20261024T174956Z/FINAL_DQ_REPORT.md) |
| `gold_financing_analytics_20261024T195959Z` | Dry run (superseded: 31/31 passed, but the report summary kept one value per lender in its distribution tables) | `runs/gold_financing_analytics_20261024T195959Z/dq_report.json` |
| `gold_financing_analytics_20261024T200921Z` | **Dry run only.** 2,685 instruments (612 IBRD + 2,073 IDA) × 65 cols; 31/31 checks; independent verifier 19/19 (64 columns, exact Decimal, 0 mismatches; per-project counts equal to Gold #1); 14 CHECK and 36 NOT NULL hold on the data; awaiting approval | [`runs/gold_financing_analytics_20261024T200921Z/GOLD_DESIGN_AND_DRY_RUN_REPORT.md`](runs/gold_financing_analytics_20261024T200921Z/GOLD_DESIGN_AND_DRY_RUN_REPORT.md) |
| `gold_financing_analytics_20261024T202548Z` | **Created `gold_financing_analytics` (Gold table #5): 2,685 rows × 65 cols; Delta v80.** Pre-write 31/31; post-write 24/24 (64 columns recomputed, exact Decimal, 0 mismatches; per-project counts equal to Gold #1; baseline equal to the approved dry run); all 39 existing tables, grants, mask and cohort unchanged. Only deviation: approved documentation comments | [`runs/gold_financing_analytics_20261024T202548Z/FINAL_DQ_REPORT.md`](runs/gold_financing_analytics_20261024T202548Z/FINAL_DQ_REPORT.md) |
| `gold_procurement_kpis_dryrun_20261024T211422Z`, `…211846Z` | KPI dry runs (superseded: 3 false-positive column-name checks; then window percentiles typed DOUBLE, now INT) | — |
| `gold_procurement_kpis_dryrun_20261024T212925Z` | **Read-only KPI design and dry run (proposed D-23 baseline; nothing created).** 7 SQL views proposed instead of a physical table; 42/42 checks; independent verifier 29/29 (every cell of 16,310 view rows recomputed, 0 mismatches); all 40 tables unchanged; awaiting approval | [`runs/gold_procurement_kpis_dryrun_20261024T212925Z/KPI_DESIGN_AND_DRY_RUN_REPORT.md`](runs/gold_procurement_kpis_dryrun_20261024T212925Z/KPI_DESIGN_AND_DRY_RUN_REPORT.md) |
| `gold_procurement_kpis_create_20261024T214700Z`, `…215011Z`, `…215213Z` | KPI view pre-flights (nothing created): two stopped on the single-statement guard (`;` in comments); the third passed 51/51 (pre-flight only) | — |
| `gold_procurement_kpis_create_20261024T215532Z` | **Created the 7 approved `vw_kpi_*` views (CREATE VIEW only; D-23 still pending).** Pre-flight 51/51; post-creation verification 53/53 (schemas, row counts, every cell recomputed, headline totals exact, no inflation; 40 tables, grants, mask and cohort unchanged) | [`runs/gold_procurement_kpis_create_20261024T215532Z/POST_CREATION_VERIFICATION_REPORT.md`](runs/gold_procurement_kpis_create_20261024T215532Z/POST_CREATION_VERIFICATION_REPORT.md) |
