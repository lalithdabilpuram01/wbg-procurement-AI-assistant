# Structured Data Agent: Table Registry and Semantic Discovery

**Date:** 2026-10-25 · **Status:** implemented and tested · **Registry build:** `registry_20261025T211626Z`
**Detailed inventory:** [metadata/METADATA_INVENTORY.md](metadata/METADATA_INVENTORY.md)

## 1. What was implemented

| Component | Implementation | Status |
|---|---|---|
| Metadata discovery | `metadata/discover_databricks.py`: read-only `information_schema`, `SHOW TBLPROPERTIES`, `count(*)` | Run against live Databricks; snapshot `databricks_metadata_20261025T203808Z.json` |
| Live verification | `metadata/verify_metadata.py`: grain uniqueness, relationship orphans and cardinality | 30/30 grains unique; 21/21 relationships pass |
| Curated inputs | `metadata/curated/*.json`: objects, column policy, relationships, metrics, synonyms, access policy, SQL templates | Every entry cites its source document |
| Registry store | PostgreSQL 16, database `wbg_structured_registry`, schema `registry` (10 tables) | Created and loaded; runtime role is SELECT-only (tested) |
| Initialisation | `registry/init_registry.py`: explicit, repeatable, one transaction | No scheduler, no automatic synchronisation |
| Embeddings | Local gte-base-en-v1.5 (offline cache), 1,061 documents × 768 dimensions, stored as `real[]` | pgvector not installed: NumPy cosine behind a `VectorIndex` interface |
| Semantic discovery | `search.py` | Live-tested in all 15 evaluation scenarios |

## 2. Environment decisions (approved on 2026-10-25)

- **PostgreSQL:**
  - Created a dedicated database `wbg_structured_registry` with two roles:
    - `wbg_registry_admin` owns the database and loads the registry;
    - `wbg_registry_reader` has CONNECT, USAGE and SELECT only.
  - The existing `postgres`, `compliance` and `compliance_test` databases were not touched.
- **Driver and parser:** installed `psycopg[binary]` 3.3.6 and `sqlglot` 30.24.0 in `/opt/anaconda3`.
- **pgvector:** not available on the server and not installed.
  - Embeddings are stored as `real[]`, together with the model id and dimension; a CHECK constraint enforces that the dimension matches.
  - Vectors with the wrong dimension, NaN values or zero norm are rejected on write and on load.
  - Migration path: `registry/sql/pgvector_migration.sql` (not applied).
  - This applies to the small Table Registry only. The PDF retrieval architecture remains PostgreSQL + pgvector.
- **Limitation:** local PostgreSQL authentication is `trust` for local connections, so role passwords are not enforced by this server. Role privileges are enforced (a write by the reader role fails with `InsufficientPrivilege`). The server configuration was not changed.

## 3. Registered objects

The registry holds **30 objects**:
- 18 Silver tables;
- 5 Gold tables;
- 7 Gold KPI views (`certified_aggregate = true`; preferred for aggregate questions).

Bronze and reference-Bronze are not registered, so any query against them is rejected as unauthorized.

| Layer | Objects | Authorized roles |
|---|---|---|
| Gold tables | `gold_project_portfolio` (2,000), `gold_contract_analytics` (178,921), `gold_procurement_notices` (243,533), `gold_country_context` (5,642), `gold_financing_analytics` (2,685) | `procurement_analyst`, `data_steward` |
| Gold KPI views | `vw_kpi_overview`, `vw_kpi_contract_awards`, `vw_kpi_contract_value_distribution`, `vw_kpi_notice_activity`, `vw_kpi_response_windows`, `vw_kpi_financing_instruments`, `vw_kpi_country_comparison` | `procurement_analyst`, `data_steward` |
| Silver tables | all 18 `wbg_silver` tables | `data_steward` only |

Every object carries:
- its fully qualified name, logical name, layer and type;
- a short description (first sentence of the Databricks table comment; the full comment is kept in provenance);
- its grain and grain columns, with status and source;
- its row count, authorized roles and operations, required filters and limitations;
- provenance: documentation properties, CHECK constraints, masks and the live grain check;
- its columns, each with type, nullability, Databricks comment, classification, unit, measure family and additivity.

## 4. Verified, curated and unverified metadata

| Item | Count | Status | Basis |
|---|---:|---|---|
| Object descriptions | 30 | verified | Databricks table comments (all 30 objects have one) |
| Column descriptions | 302 of 1,035 | verified | Databricks column comments. The other 733 columns have no description, and none was written |
| Grains | 30 | verified | Silver grain and keys from the Silver audit inventory; Gold from table/column comments and the Gold README; views from `kpi_queries.py` GRAINS. All 30 confirmed unique on live data (null-safe distinct count) |
| Relationships | 21 | verified | Silver join catalogue and Gold documentation, re-checked live (see §5) |
| Column classifications | 1,035 | curated | Rules from Silver audit §9, D-14, D-14b and Gold build rule 5. 810 public, 187 lineage, 20 restricted, 18 prohibited |
| Metrics | 14 | curated | Expressions copied from `kpi_queries.py` or documented aggregation rules. D-23 (dashboard KPIs) is still pending |
| Business synonyms | 110 terms | **unverified** | Curated vocabulary; every target exists; needs procurement SME review |
| SQL templates | 12 | 10 verified, 2 curated | Verified = executed through the gateway and matched an independent baseline in evaluation run `20261025T211448Z` |

**Sensitive data.** The registry classifies:
- **Prohibited** (never selected, filtered, joined or embedded): `notice_text` (masked, D-14); `supplier_name(s)`, `supplier_text*`, `contract_description*`, `bid_description*`, `contact_organization` and `footnote_text` (free text); `bronze_raw_record`.
- **Restricted** (stewards may filter or join on them, nobody may output them): supplier IDs and procurement references.
- **Lineage** (stewards only): hashes, Bronze file names, run IDs.

Gold contains no restricted or prohibited columns, consistent with the Gold build rules.

## 5. Approved relationships (all verified live)

| Group | Relationships | Live result |
|---|---|---|
| Gold facts → portfolio | contracts, notices and financing → `gold_project_portfolio.project_id` (N:1) | 0 orphans; to-side unique |
| Gold → country context | portfolio, contracts, notices, financing `wdi_country_code` → `gold_country_context.country_code`, **year required** | 0 orphans; unique per (country, year). NULL keys are regional rows (139 to 24,971) |
| Silver | 14 relationships from the Silver join catalogue (contracts, lines, legacy, notices, details, candidates, links, IBRD, IDA, details v3, IEG, WDI country) | 0 orphans. Optional `1:0..1` relationships report coverage (e.g. 1,816 projects without details v3) |

**Documented exception, counted explicitly:** `procurement_notice_details` has 9 rows with no DS00979 notice. The Silver report documents these as "9 API-only notices", and the verifier requires exactly 9.

**Country-context join lag.** No join lag is approved for country context, so the registry does not invent one. Joins must fix `year` explicitly; the validator rejects them otherwise.

## 6. Semantic discovery

**Signals:**
1. Exact identifier match: table, column or metric names containing `_`.
2. Curated synonym phrases, with light plural normalisation.
3. BM25 over 1,061 object, column and metric documents.
4. gte-base cosine similarity.

**Authorization filter first.** Documents of unauthorized objects, and of columns the principal can neither output nor filter, are removed before ranking. Prohibited columns are never embedded or indexed.

**Fusion:**
- object-level RRF (k = 60) over the BM25 and embedding ranks;
- fixed bonuses: exact match +0.05, synonym +0.03, certified KPI view for aggregate wording +0.01;
- a metric matched through a synonym also boosts its certified KPI view.

**Each candidate returns:**
- identity, description and grain;
- the reasons it was selected;
- matched columns and authorized columns;
- related metrics, approved joins, limitations and provenance.

Full schemas are fetched separately and only for selected objects (`get_table_schema`).

**Ambiguity.** When two or more metrics of one ambiguity group match only through generic words ("total value", "how much money"), discovery returns `needs_clarification` with the options, unless the rest of the question points to exactly one of them. For example, "contracts … for what value" resolves to contract award value, and "value of IDA credits" resolves to IBRD/IDA principal.

**Live results** (evaluation run `20261025T211448Z`, analyst principal):

| Measure | Result |
|---|---|
| Expected object in the top 3 | 15/15 scenarios |
| Ambiguous question (T10, "What is the total value for Ethiopia?") | `needs_clarification`: contract award value, IBRD/IDA principal, project commitment |
| Synonym-only question (T09, "bid invitations") | `tender_invitation_notice_count` ranked first; `gold_procurement_notices` and `vw_kpi_overview` in the top 3 |
| Search latency | 48–98 ms warm; 3.0 s for the first call (embedding model load) |

**Discovery issues fixed during development** (disclosed):
1. Plural forms missed synonyms ("invitations for bids").
2. The generic word "year" exact-matched a column.
3. Generic "value" in "contracts … what value" was wrongly flagged as ambiguous. This made T05 fail in the first evaluation run `20261025T211135Z`, which is kept as superseded.

## 7. Limitations

- Synonyms and metric definitions need SME review. D-23 (dashboard KPIs) is pending.
- 733 columns have no Databricks comment. Discovery relies on their names for those columns.
- Metric attribution in results is by column lineage, so a filtered count is reported under its base metric. For example, T09's tender-invitation count is attributed to `procurement_notice_count`.
- No freshness monitoring, synchronisation or re-embedding (out of scope). The registry is rebuilt only by explicitly re-running the initialisation script.
