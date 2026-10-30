# Structured Data Agent: SQL Execution Validator, Gateway and Evaluation

**Date:** 2026-10-25 · **Status:** implemented and tested · **Evaluation run:** `evaluation/runs/20261025T211448Z/` (15/15 passed)

## 1. Execution path

No component executes SQL directly. The only path is:

`execute_readonly_sql` → service (request contract + principal) → **gateway** (credential gate) → **validator** (re-run on every call; an earlier validation id is never trusted) → executor runs **the validator's normalised SQL only**, with bound `:named` parameters and a wall-clock timeout → **result validation** → structured result or typed error.

Parsing alone never authorizes execution: a query is approved only when all 12 stages pass. Any unexpected exception rejects the query (fail closed).

## 2. Validator stages (sqlglot 30.24.0, Databricks dialect)

| # | Stage | Rejects |
|---|---|---|
| 1 | Single SELECT statement | Parse errors; more than one statement; any DDL/DML/command node *anywhere in the tree*: INSERT, UPDATE, DELETE, MERGE, CREATE, DROP, ALTER, TRUNCATE, GRANT, REVOKE, SET, USE, COPY, SELECT INTO, CACHE, OPTIMIZE and other `Command`s, transactions, DESCRIBE/SHOW |
| 2 | Allowed constructs and functions | Function allow-list (aggregates, arithmetic, dates, strings, CASE, CAST, ranking); unknown/UDF functions (`reflect`, `java_method`, `read_files`, `ai_query`, …); table-valued functions; UNION/INTERSECT/EXCEPT; time travel; TABLESAMPLE; LATERAL VIEW/EXPLODE; VALUES; PIVOT; `${var}`; `SELECT *`; positional `?` |
| 3 | Parameters | Missing or unused `:name` values; strings over 200 characters |
| 4 | Registered and authorized objects | Names that are not fully qualified; unregistered objects (Bronze, `information_schema`, unknown Gold names); objects the principal's roles may not use, including in nested subqueries and CTEs |
| 5 | Column resolution | Unknown or ambiguous columns (sqlglot `qualify` against the registry schema) |
| 6 | Column authorization and classification | Prohibited columns (any use); restricted columns as output (also inside aggregates); lineage columns for analysts. Use is traced through CTEs and subqueries to base columns |
| 7 | Approved joins and grain | Joins without key equality or on non-key conditions. Base-to-base joins must match a registered relationship, or use conformed keys with one unique side. Many-to-many joins; incompatible grains (no single root source); value aggregates over rows repeated by a join, also when the fan-out happens inside a CTE |
| 8 | Required filters | KPI subtotal views without `grouping_level = …`; country-context joins without a fixed `year` |
| 9 | Measure rules | SUM over non-additive columns (percentiles, rates, scores, dates); arithmetic mixing measure families (contract values, IBRD/IDA financing, project commitments, loan transactions) |
| 10 | Row limit | LIMIT above `min(1000, role limit, request)`; a non-literal LIMIT. A missing LIMIT is *added* (a tightening, recorded as a rewrite) |
| 11 | Complexity budget | More than 4 joins, 4 CTEs, subquery depth 3 or 1,500 AST nodes |
| 12 | Scan budget | Registered row counts of the objects read above 1.5 million |

**How fan-out is detected:**
- Each source's uniqueness comes from its **verified grain**.
- Columns fixed by an equality filter are added to that, so the country context is unique once its year is fixed.
- A CTE or subquery is unique on its GROUP BY keys (or is one row if aggregated without grouping). This is how "pre-aggregate to the shared key, then join" is recognised.
- One source must be a root from which every other source is reached through many-to-one or one-to-one steps.

**Bounded correction** (`generation.generate_with_correction`):
- At most one correction, and only when every error is recoverable (unknown column or table, parse, join, grain, filter, measure, limit, complexity or parameter errors).
- The corrected SQL is validated again.
- Security and authorization errors are never retried.
- The policy is never broadened after a failure.

**Credentials:**
- The only Databricks credential owns all 30 objects (write-capable), so the gateway refuses it by default (`CREDENTIAL_NOT_READ_ONLY`).
- For this assessment, `WBG_ALLOW_DEV_OWNER_CREDENTIALS=true` enabled a labelled development mode. Every result and audit record says `credential_mode = dev_owner_credentials` and `production_read_only = false`, and carries a limitation stating that read-only access was enforced by the validator and gateway, **not by database permissions**.
- To replace the credential with a read-only principal, point `WBG_DATABRICKS_PROFILE` at its profile; no agent code changes.

**Result validation:**
- Returned columns must equal the validated output columns.
- The row limit must hold.
- Outputs must be disclosable (public or lineage).
- Counts must be non-negative.
- No e-mail or phone pattern may appear in any value. A failure withholds all rows.

**Results returned:** status; validation and query ids; source objects; columns with type, unit and source columns; rows (decimals as exact strings); row count; truncation flag; metric definitions; applied filters; limitations; validation and result checks; provenance (credential mode, registry build, timestamp).

## 3. Unit tests (97 passed; no Databricks needed)

| File | Tests | Covers |
|---|---:|---|
| `tests/test_sql_validator.py` | 72 | The 12 reviewed templates approved. Rejected: 14 destructive and stacked statements (including INSERT behind a CTE, mixed case, SELECT INTO); 11 unsafe functions and constructs (including a function hidden in a nested subquery, nested UNION in a CTE, `${x}`, `?`, delta paths). Literal `'; DROP TABLE x; --'` treated as data; trailing comment not executed. Unknown table/column; unauthorized Silver, Bronze, `information_schema`, Silver in a nested subquery; principal without roles; prohibited columns for stewards (output, filter, CTE); restricted output inside `count(DISTINCT …)`; lineage for analysts; fact-to-fact fan-out; incompatible grains; SUM of the one-side after 1:N; fan-out hidden in a CTE; cross, non-key and non-equi joins; missing country-context year; missing `grouping_level`; SUM of percentiles; mixed measure families; LIMIT, complexity and scan budgets; parameter mismatch |
| `tests/test_gateway_and_generation.py` | 14 | Write-capable credential refused by default; dev-mode labelling; a rejected query never reaches the executor; only validated SQL executes (with the added LIMIT); typed timeout error; rows with contact details withheld; one recoverable correction; correction bounded to one attempt; destructive SQL not retried; template labelled "no LLM"; LLM providers fail closed; invalid vectors; authorization before vector ranking |
| `tests/test_registry_and_mcp.py` | 10 | Registry row counts; reader role cannot write; single embedding model and dimension; MCP tools (see the MCP report) |
| `tests/test_observability.py` | 1 | Spans and audit events keep only allow-listed attributes |

## 4. Live evaluation (15 scenarios, analyst principal unless stated)

SQL came from reviewed templates (labelled "template (deterministic; no LLM)") or hand-written adversarial test SQL. **No LLM generated SQL**: no provider is approved or configured. Baselines are separate trusted SELECTs, mostly over **Silver**, so they do not share the agent's path.

| # | Scenario | Discovery top 3 | Validator | Execution | Result vs baseline | Latency* |
|---|---|---|---|---|---|---|
| T01 | Known project P148850 | portfolio first | approved | 1 row | matches Silver `projects` + counts of Silver contracts (43) and notices (73) | 8.4 s (cold start) |
| T02 | Contract 1803814 | contract analytics first | approved | 1 row | US$67,629,166.65 = Silver contract = sum of Silver supplier lines | 2.5 s |
| T03 | Contracts per Ethiopia project | KPI cube, contracts | approved | 27 rows | 27/27 projects identical (Silver contracts ⋈ projects) | 2.9 s |
| T04 | Award value by procurement method | contracts, KPI cube | approved (certified view) | 18 rows | 18/18 methods identical to Silver | 2.5 s |
| T05 | Ethiopia, FY2023 | KPI cube first | approved | 1 row | 700 contracts / US$335,482,251.96 identical to Silver | 3.2 s |
| T06 | Tender notices by category | overview, notices | approved | 4 rows | 4/4 identical to Silver notices (Invitation for Bids) | 2.5 s |
| T07 | Approved join contracts → portfolio | KPI cube, contracts, portfolio | approved; join `c → p (N:1, registered)` | 7 rows | 7/7 ratings identical to the pre-aggregated portfolio | 2.7 s |
| T08 | One-to-many double counting | contracts, financing | naive join **rejected `FAN_OUT_RISK`**; pre-aggregated CTE approved (`c → f N:1, conformed key`) | 1 row | 1,129 projects / 111,323 contracts / US$53,823,138,419.17, identical to portfolio. The rejected naive query would have reported 285,295 contracts and US$127,318,058,979.75 (**×2.56 and ×2.37**; measured by the harness, never returned by the agent) | 4.6 s |
| T09 | "bid invitations" in FY2024 (synonym) | overview, notices | approved | 1 row | identical to Silver notices | 2.1 s |
| T10 | "What is the total value for Ethiopia?" | `needs_clarification` (3 measures) | not generated | — | no guess made | 0.05 s |
| T11 | Unknown table / column | contracts first | `UNKNOWN_TABLE`, `UNKNOWN_COLUMN` (both flagged recoverable) | not executed | — | 0.1 s |
| T12 | DROP, DELETE, UPDATE, stacked, INSERT-in-CTE, CTAS | portfolio first | all 6 rejected (`FORBIDDEN_OPERATION` / `MULTIPLE_STATEMENTS`) via the gateway | none ran | `gold_project_portfolio` Delta version 71 before and after each attempt | ≤ 2 ms each |
| T13 | Unauthorized access | contracts first | Silver, Bronze, nested Silver → `UNAUTHORIZED_OBJECT`; steward supplier names and notice text → `PROHIBITED_COLUMN` | none ran | — | ≤ 4 ms each |
| T14 | Execution limits | notices first | LIMIT 50000 → `LIMIT_EXCEEDED`; valid query under a 1 s gateway limit → approved | `EXECUTION_TIMEOUT` after 3.1 s (1 s limit + server interrupt) | no rows returned | 3.5 s |
| T15 | Country profile (2 CTEs + join + ordering) | KPI cube, contracts, overview | approved; join `c → t (1:1, conformed key)` | 10 rows | top 10 countries identical to certified `vw_kpi_country_comparison` (awards, value, tender invitations) | 5.6 s |

\* Latency is per case, end to end through the service: discovery, template retrieval, validation, execution and audit. Validation took 1–16 ms; warm Databricks execution took 1.2–3.7 s; warm search took 48–98 ms.

**Result-contract checks passed on every executed result:**
- columns match the validated query;
- the row limit holds;
- outputs are disclosable;
- counts are non-negative;
- values contain no contact details.

**Audit:** the 169 audit events written during development and evaluation contain no SQL text, identifiers, parameter values, rows or principal ids (verified by search). The principal is hashed.

**Superseded run:** `20261025T211135Z` failed only on T05, because discovery wrongly asked for clarification. The ambiguity rule was fixed and the whole evaluation was re-run.

## 5. What this does not show

- **No LLM-generated SQL was evaluated.** Correctness above is for reviewed templates. The validator, correction loop and provider interface are ready for an approved LLM, but semantic accuracy of generated SQL is unmeasured.
- **Not database-enforced read-only.** Development mode used the owner credential; a dedicated read-only principal is still needed for production.
- **Semantic business correctness is checked by rules, not proven.** The grain, fan-out, required-filter and measure-family rules cover known failure modes. Other semantic errors are possible, for example a wrong but valid filter value, or a metric chosen by mistake when the wording is clear but wrong. Verified examples and baselines are the control.
- **Timeouts:** the client stops waiting at the limit, then calls `interruptAll`; the server-side stop took about 2 s more in T14.
