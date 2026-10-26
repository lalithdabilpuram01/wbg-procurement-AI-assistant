# Silver Layer Jobs (Databricks Connect, Serverless)

Silver transformations run from VS Code against Databricks Free Edition. They use Databricks Connect 17.3.14 and the
`wbg-databricks-free` profile on Serverless compute.

**Rules:**
- Bronze tables are read-only.
- Every job defaults to a **dry run**.
- A write needs `--mode write --confirm-create`, all blocking data-quality checks passing, and a target table that
  doesn't exist yet. Existing tables are never overwritten.

## Layout

```text
silver/
  common/session.py     DatabricksSession (profile + serverless); finds the Databricks CLI bundled with the VS Code extension
  common/dq.py          DQ report: "error" checks block writes, "warn" checks are reported
  wdi/transform.py      WDI Bronze -> Silver (pure DataFrame functions)
  wdi/checks.py         WDI data-quality rules
  wdi/run_wdi_silver.py runner (dry run / write)
  wdi/verify_wdi_silver.py independent post-write verification against Bronze
  income_class/parse.py   OGHIST workbook parser (pure Python; layout detected by labels)
  income_class/transform.py / checks.py / run_income_class_silver.py / verify_income_class_silver.py
  common/write.py         create-only Delta write (used by income_class, wgi)
  wgi/transform.py / checks.py / run_wgi_silver.py / verify_wgi_silver.py   WGI estimates (6 dimensions)
  cpia/transform.py / checks.py / run_cpia_silver.py / verify_cpia_silver.py CPIA ratings (6 series)
  common/sourcefiles.py   manifest vintages + Volume source-file SHA-256 / line verification (cpia, projects; .jsonl and .jsonl.gz)
  projects/transform.py / checks.py / run_projects_silver.py / verify_projects_silver.py   2,000 cohort projects (procurement Silver)
  contracts/transform.py / checks.py / run_contracts_silver.py / verify_contracts_silver.py   contract awards: lines, contracts, legacy
  financing/transform.py / checks.py / run_financing_silver.py / verify_financing_silver.py   IBRD loans + IDA credits (two tables; exact US$, no FX)
  project_details/transform.py / checks.py / run_project_details_silver.py / verify_project_details_silver.py   v3 all-fields: project_details_v3 (184) + project_loan_transactions (16,643); no personal ids, no raw JSON
  audit/run_silver_audit.py   consolidated read-only audit of all 18 Silver tables (inventory, relationships, reference joins, privacy counts, Gold readiness)
  ieg/transform.py / checks.py / run_ieg_silver.py / verify_ieg_silver.py   IEG project ratings (DS00053; latest-only snapshot; ordinal scores)
  notices/transform.py / checks.py / run_notices_silver.py / verify_notices_silver.py   procurement notices, API details (notice_text masked, D-14), notice-contract candidates + evidence-supported links
  notices/d14_gate_tests_b.py        D-14 gate tests T3b–T6b (direct SP grants, identity masks, temp catalog)
  notices/d14_gate_tests.py          D-14 gate tests T1–T7 (temporary SP/group, synthetic data, ledger + cleanup)
  notices/probe_access_controls.py   D-14 probe: tests what column masks enforce on Free Edition (synthetic data, temporary objects)
  tests/                logic tests on synthetic DataFrames over Databricks Connect (no table I/O)
  runs/<run_id>/        local DQ report of every run (dq_report.json)
```

## Run

Run from `procurement-intelligence/`, using the Python environment that has `databricks-connect==17.3.14` (here:
`/opt/anaconda3/bin/python3`).

```bash
python -m silver.wdi.run_wdi_silver                              # dry run: build + validate, no writes
python -m silver.wdi.run_wdi_silver --mode write --confirm-create   # only after approval
python -m silver.wdi.verify_wdi_silver --run-id <run_id>        # independent post-write verification (read-only)
python -m silver.income_class.run_income_class_silver           # dry run (add --mode write --confirm-create after approval)
python -m silver.income_class.verify_income_class_silver --run-id <run_id>
python -m silver.wgi.run_wgi_silver                             # dry run (add --mode write --confirm-create after approval)
python -m silver.wgi.verify_wgi_silver --run-id <run_id>
python -m silver.cpia.run_cpia_silver                           # dry run (add --mode write --confirm-create after approval)
python -m silver.cpia.verify_cpia_silver --run-id <run_id>
python -m silver.projects.run_projects_silver                   # dry run (add --mode write --confirm-create after approval)
python -m silver.projects.verify_projects_silver --run-id <run_id>
python -m silver.contracts.run_contracts_silver                 # dry run (add --mode write --confirm-create after approval)
python -m silver.contracts.verify_contracts_silver --run-id <run_id>
python -m silver.financing.run_financing_silver                 # dry run (add --mode write --confirm-create after approval)
python -m silver.financing.verify_financing_silver --run-id <run_id> --approved-dry-run <dry_run_id>
python -m silver.project_details.run_project_details_silver         # dry run (add --mode write --confirm-create after approval)
python -m silver.project_details.verify_project_details_silver --dry-run --run-id <dry_run_id>   # or --run-id <run_id> --approved-dry-run <dry_run_id>
python -m silver.ieg.run_ieg_silver                             # dry run (add --mode write --confirm-create after approval)
python -m silver.ieg.verify_ieg_silver --run-id <run_id>
python -m silver.notices.run_notices_silver                     # dry run (add --mode write --confirm-create after approval)
python -m silver.notices.verify_notices_silver --run-id <run_id>
python -m silver.notices.probe_access_controls                  # D-14 access-control probe (synthetic; drops its objects)
python -m pytest silver/tests -q -p no:cacheprovider
```

**Authentication:**
- The profile uses `auth_type = databricks-cli`.
- If `databricks` is not on PATH, `session.py` adds the CLI bundled with the Databricks VS Code extension
  (`~/.vscode/extensions/databricks.databricks-*/bin`).
- To use another profile, set `WBG_DATABRICKS_PROFILE`.

The Phase 1 discovery tests (`discovery/`, plain pandas) are separate. Databricks Connect replaces `pyspark`, so the two
should not share one environment.

## WDI economic indicators (`wdi/`)

| Target | Grain | Rows (2026-10-08 vintage) |
|---|---|---|
| `workspace.wbg_silver.wdi_indicator_observations` | country_code × year × indicator_code | 20,748 (217 economies + 49 aggregates × 26 years × 3 indicators) |
| `workspace.wbg_silver.wdi_country` | WDI entity | 296 (217 economies + 79 aggregates; 266 have observations) |

**Key decisions**
- **Key:** `country_code` is the World Bank API `country.id` (2-character).
  - ISO3 is blank for 5 income-group aggregates (`XD`, `XM`, `XN`, `XT`, `XY`); keying on ISO3 would wrongly merge 390
    rows into 78 "duplicates".
  - Blank ISO3 becomes NULL.
  - `country_wb_id` (3-character, e.g. `HIC`) is filled from `/v2/country` for every entity.
- **Economies vs aggregates:** `entity_type` comes from `/v2/country` (`region.id = 'NA'` marks an aggregate). Both are
  kept, unfiltered by the cohort (D-28).
- **Missing values:** 1,526 missing values stay NULL, with `is_value_missing = true`. Nothing is imputed or dropped.
- **Current vs historical attributes:** region, income level and lending type are today's values. They live only on
  `wdi_country`, with a `_current` suffix. Historical income groups come from OGHIST (`income_classification_history`).
- **Lineage:** Bronze table and source file, Bronze ingestion time, SHA-256 of the raw record, source vintage
  (`source_lastupdated` from the API page header), run ID and transform version.
- **Publication lag:** the two-year lag (D-30) is applied in Gold feature engineering, not here; Silver keeps every
  year.

## Run log

| Run | Result | Report |
|---|---|---|
| `wdi_20261023T142626Z` | Created `wdi_indicator_observations` (20,748) and `wdi_country` (296). Pre-write 35/35 and post-write 33/33 checks passed; Bronze unchanged | [`runs/wdi_20261023T142626Z/FINAL_DQ_REPORT.md`](runs/wdi_20261023T142626Z/FINAL_DQ_REPORT.md) |
| `income_class_20261023T143917Z` | **Dry run only.** 8,736 rows planned; 27/27 checks passed; awaiting approval to write | `runs/income_class_20261023T143917Z/dq_report.json` |
| `income_class_20261023T144321Z` | Created `income_classification_history` (8,736 rows; 224 entities × FY1989–2027). Pre-write 27/27 and post-write 19/19 checks passed; Bronze and WDI Silver unchanged | [`runs/income_class_20261023T144321Z/FINAL_DQ_REPORT.md`](runs/income_class_20261023T144321Z/FINAL_DQ_REPORT.md) |
| `wgi_20261023T145005Z` | **Dry run only.** 34,992 rows planned; 31/31 checks passed; awaiting approval to write | `runs/wgi_20261023T145005Z/dq_report.json` |
| `wgi_20261023T145536Z` | Created `wgi_estimates` (34,992 rows; 216 entities incl. 9 unmatched name-keyed; 27 years; 1,310 NULLs). Pre-write 31/31 and post-write 22/22 checks passed; Bronze and all existing Silver tables unchanged | [`runs/wgi_20261023T145536Z/FINAL_DQ_REPORT.md`](runs/wgi_20261023T145536Z/FINAL_DQ_REPORT.md) |
| `cpia_20261023T150944Z` | Dry run (superseded by the re-run below after a typing-only change) | `runs/cpia_20261023T150944Z/dq_report.json` |
| `cpia_20261023T151305Z` | **Dry run only (final code).** 105,336 rows planned; 32/32 checks passed; awaiting approval to write | `runs/cpia_20261023T151305Z/dq_report.json` |
| `cpia_20261023T151609Z` | Created `cpia_ratings` (105,336 rows; 14,862 scores, 90,474 NULLs; 217 economies + 49 aggregates). Pre-write 32/32 and post-write 24/24 checks passed; Bronze and all existing Silver tables unchanged | [`runs/cpia_20261023T151609Z/FINAL_DQ_REPORT.md`](runs/cpia_20261023T151609Z/FINAL_DQ_REPORT.md) |
| `projects_20261023T152527Z` | Dry run (superseded by the re-run below after typing-only changes) | `runs/projects_20261023T152527Z/dq_report.json` |
| `projects_20261023T153056Z` | **Dry run only (final code).** 2,000 rows planned; 68/68 checks passed; awaiting approval to write | `runs/projects_20261023T153056Z/dq_report.json` |
| `projects_20261023T153545Z` | Created `projects` (2,000 rows = cohort; 1,861 single-country + 139 regional; 1,809 exact + 52 curated-alias WDI matches). Pre-write 68/68 and post-write 24/24 checks passed; Bronze, existing Silver and cohort manifest unchanged | [`runs/projects_20261023T153545Z/FINAL_DQ_REPORT.md`](runs/projects_20261023T153545Z/FINAL_DQ_REPORT.md) |
| `contracts_20261023T155353Z` | Dry run (superseded by the re-run below after a typing-only change) | `runs/contracts_20261023T155353Z/dq_report.json` |
| `contracts_20261023T155924Z` | **Dry run only (final code).** lines 186,907 / contracts 178,921 / legacy 6,448 planned; 62/62 checks passed; awaiting approval | `runs/contracts_20261023T155924Z/dq_report.json` |
| `contracts_20261023T160439Z` | Created `contract_award_lines` (186,907), `contract_awards` (178,921), `contract_awards_legacy` (6,448). Pre-write 62/62 and post-write 30/30 checks passed; exact decimal reconciliation per contract and project; all existing tables unchanged | [`runs/contracts_20261023T160439Z/FINAL_DQ_REPORT.md`](runs/contracts_20261023T160439Z/FINAL_DQ_REPORT.md) |
| `notices_20261023T163241Z` | Dry run **failed** 3 blocking parity checks (NFKC references, empty-text template flag, JSONL line split); fixed. Run folder deleted by mistake; console log kept in the session scratchpad | — |
| `notices_20261023T164849Z` | Dry run (superseded: single-country blank-code metric used the wrong geography value) | `runs/notices_20261023T164849Z/dq_report.json` |
| `notices_20261023T170342Z` | **Dry run only (final code).** notices 243,533 / details 20,305 / link candidates 57,247 / verified links 12,039 planned; 78/78 checks passed; awaiting approval | [`runs/notices_20261023T170342Z/DRY_RUN_REPORT.md`](runs/notices_20261023T170342Z/DRY_RUN_REPORT.md) |
| `d14_access_probe_20261023T173456Z` | D-14 access-control probe (synthetic data): column mask enforced for SQL, DataFrame, toPandas, sha2, filters, CTAS; time travel refused; storage location not exposed. Limitations: owner can drop masks; mask changes not in Delta history. Probe objects dropped | `runs/d14_access_probe_20261023T173456Z/access_control_probe.json` |
| `notices_20261023T173737Z` | Dry run (D-14 + link-status design): 85/85 checks passed | `runs/notices_20261023T173737Z/dq_report.json` |
| `notices_20261023T175322Z` | Created `procurement_notice_details` (20,305; `notice_text` masked), `procurement_notices` (243,533), `notice_contract_link_candidates` (57,247), `notice_contract_links` (12,039; 8,177 one-to-one unambiguous, 3,862 ambiguous). Completed by two resumes of the same run (CHECK constraints not allowed on a masked table; network loss); nothing dropped or overwritten. Pre-write 90/90, post-write 51/51 checks passed; all existing tables unchanged | [`runs/notices_20261023T175322Z/FINAL_DQ_REPORT.md`](runs/notices_20261023T175322Z/FINAL_DQ_REPORT.md) |
| `d14_gate_tests_20261023T211406Z` | D-14 gate tests (synthetic; temporary identities): T1 SP created (no admin), T2 Databricks Connect Serverless as SP via OAuth M2M + denied on production tables, T3 FAIL (workspace-created groups are not UC principals), T4–T6 not run, T7 cleanup complete and state identical to baseline. Two earlier runs (`…210613Z`, `…211026Z`) stopped on a harness bug with full cleanup | `runs/d14_gate_tests_20261023T211406Z/gate_results.json` |
| `d14_gate_tests_b_20261023T213951Z` | D-14 gate tests T3b–T6b (synthetic, temporary catalog, 2 temporary SPs): direct SP grants and identity-keyed masks pass on all read/copy/export paths; Bronze-style JSON redaction mask passes (no Delta version change); volume grants restrict other identities; masks block time travel and CHECK constraints. Cleanup incident: the temp catalog was orphaned after ownership transfer, then recovered and dropped; production state identical to baseline | `runs/d14_gate_tests_b_20261023T213951Z/gate_results.json` |
| `ieg_discovery_20261023` | **Read-only discovery** of IEG ratings (DS00053, 750 rows): unique `project_id`, all in cohort, no blanks; latest-snapshot ratings (post-completion; leakage notes); proposed `wbg_silver.ieg_project_ratings` (750 rows). Awaiting approval | [`runs/ieg_discovery_20261023/IEG_DISCOVERY_REPORT.md`](runs/ieg_discovery_20261023/IEG_DISCOVERY_REPORT.md) |
| `ieg_20261023T224049Z` | Dry run (superseded: constraint pre-checks added) | `runs/ieg_20261023T224049Z/dq_report.json` |
| `ieg_20261023T224233Z` | **Dry run only.** `ieg_project_ratings` 750 rows planned; 42/42 checks (incl. Delta CHECK constraints evaluated on the data); `rating_available_from_date` NULL by design; anomalies listed separately; awaiting approval | [`runs/ieg_20261023T224233Z/DRY_RUN_REPORT.md`](runs/ieg_20261023T224233Z/DRY_RUN_REPORT.md) |
| `ieg_20261023T224948Z` | Dry run re-confirming the write path (45/45; baseline extended to all 17 Bronze tables + masks) | `runs/ieg_20261023T224948Z/dq_report.json` |
| `ieg_20261023T225147Z` | **Created `ieg_project_ratings` (750 rows; Silver table #14).** Pre-write 45/45, post-write 55/55 (all 43 data columns recomputed; `rating_available_from_date` NULL ×750; 7 CHECK constraints); 17 Bronze + 13 Silver tables and masks unchanged; Delta v32 (v0 CTAS + metadata). First verification attempt failed on a verifier timezone bug (data correct; report kept) | [`runs/ieg_20261023T225147Z/FINAL_DQ_REPORT.md`](runs/ieg_20261023T225147Z/FINAL_DQ_REPORT.md) |
| `financing_discovery_20261023` | **Read-only discovery** of IBRD loans (DS00047, 612) and IDA credits (DS00001, 2,073): one row per financing number (monthly snapshot 2026-09-30), all in cohort; US$ fields mix FX bases for non-USD commitments; IBRD currency unpublished; recommends two harmonised Silver tables. Awaiting review | [`runs/financing_discovery_20261023/FINANCING_DISCOVERY_REPORT.md`](runs/financing_discovery_20261023/FINANCING_DISCOVERY_REPORT.md) |
| `financing_20261023T232042Z` | **Dry run only.** `ibrd_loans` 612 + `ida_credits` 2,073 planned; 83/83 checks (exact amounts, placeholders, full-population vocabularies, CHECK constraints evaluated on data); anomalies listed separately; awaiting approval | [`runs/financing_20261023T232042Z/DRY_RUN_REPORT.md`](runs/financing_20261023T232042Z/DRY_RUN_REPORT.md) |
| `financing_20261023T232926Z` | **Created `ibrd_loans` (612 rows, 52 cols; Silver #15) and `ida_credits` (2,073 rows, 50 cols; Silver #16).** Pre-write 83/83, post-write 63/63 first attempt (all data columns recomputed from Volume files, exact Decimal amounts); 17 Bronze + 14 Silver tables, masks and cohort unchanged; Delta v50 / v52 (v0 CTAS + metadata). Only deviation from dry run: approved column comments (interest rate 0.0, late disbursement) | [`runs/financing_20261023T232926Z/FINAL_DQ_REPORT.md`](runs/financing_20261023T232926Z/FINAL_DQ_REPORT.md) |
| `projects_allfields_discovery_20261023` | **Read-only discovery** of v3 all-fields records (184 = Phase 1 cluster ∩ cohort; 76 keys: 39 identical to `projects`, 21 duplicate variants, staff name/ID to exclude, new: E&S risk, product line, themes L2–3, indicators, geo, 16,643 dated loan transactions). Recommends `project_details_v3` (184) + `project_loan_transactions` (16,643); awaiting review | [`runs/projects_allfields_discovery_20261023/PROJECTS_ALLFIELDS_DISCOVERY_REPORT.md`](runs/projects_allfields_discovery_20261023/PROJECTS_ALLFIELDS_DISCOVERY_REPORT.md) |
| `project_details_20261024T000331Z` | Dry run **failed** 2 checks, both check bugs (theme fidelity expected `[]` for an absent `theme3`; a 0–100 percent rule the source violates); transform unchanged | `runs/project_details_20261024T000331Z/dq_report.json` |
| `project_details_20261024T000753Z`, `…T001122Z` | Dry runs (superseded: anomaly list completed; identical-group and indicator-repeat anomaly keys made exact) | `runs/project_details_20261024T001122Z/dq_report.json` |
| `project_details_20261024T002016Z` | **Dry run only (final code).** `project_details_v3` 184 rows × 29 cols + `project_loan_transactions` 16,643 × 26 planned; 69/69 checks + independent verifier 62/62 (every column incl. nested arrays recomputed in Python, 0 mismatches; 0 personal identifiers); CHECK constraints evaluated on data; anomalies listed separately; awaiting approval | [`runs/project_details_20261024T002016Z/DRY_RUN_REPORT.md`](runs/project_details_20261024T002016Z/DRY_RUN_REPORT.md) |
| `project_details_20261024T041006Z` | **Created `project_details_v3` (184 rows, 29 cols; Silver #17; Delta v32) and `project_loan_transactions` (16,643 rows, 26 cols; Silver #18; v31).** Pre-write 69/69, post-write 79/79 (every column incl. nested arrays recomputed from the Volume file, 0 mismatches; 0 personal identifiers; anomalies identical to the approved dry run); 17 Bronze + 16 Silver tables, masks and cohort unchanged. Only deviation: approved documentation comments (zero count + NULL array = unavailable; theme percent not a share) | [`runs/project_details_20261024T041006Z/FINAL_DQ_REPORT.md`](runs/project_details_20261024T041006Z/FINAL_DQ_REPORT.md) |
| `silver_audit_20261024T043611Z` | **Consolidated Silver audit (read-only)** of all 18 tables (#17 `project_details_v3`, #18 `project_loan_transactions` accepted 2026-10-24): 156/156 checks; 0 orphans; exact contract reconciliation; double-count factors documented (×1.269 lines, ×1.106 links); privacy scan (counts only); Gold readiness and recommended architecture. Earlier run `…043017Z` superseded (case-sensitive phone detector) | [`CONSOLIDATED_SILVER_AUDIT.md`](runs/silver_audit_20261024T043611Z/CONSOLIDATED_SILVER_AUDIT.md), [`SILVER_RELATIONSHIP_REPORT.md`](runs/silver_audit_20261024T043611Z/SILVER_RELATIONSHIP_REPORT.md), [`GOLD_READINESS_ASSESSMENT.md`](runs/silver_audit_20261024T043611Z/GOLD_READINESS_ASSESSMENT.md) |
