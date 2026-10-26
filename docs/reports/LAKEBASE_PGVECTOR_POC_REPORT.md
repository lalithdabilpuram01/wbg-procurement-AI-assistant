# Lakebase PostgreSQL + standard pgvector POC: 2026-10-26

**Result:** standard pgvector works on Lakebase in our Databricks Free Edition workspace, connected from the local Mac
over TLS with a short-lived OAuth credential. The test covered:
- `VECTOR(768)` insert and retrieval;
- cosine-distance search, whose ranking matches NumPy exactly;
- creating an HNSW index (`vector_cosine_ops`) and confirming it in the catalog;
- planner use of that index (when sequential scans are disabled).

Only synthetic, non-sensitive data was used, in the isolated schema `wbg_rag_poc`. No PDF, embedding or project data
was uploaded or migrated.

Code: `infrastructure/lakebase_poc/` (uv, Python 3.12.12). Raw results, without identities or credentials:
`infrastructure/lakebase_poc/results/pgvector_poc_20261026T073513Z.json`.

## 1. Is Lakebase available in our Free Edition account?

Yes.

**The workspace is Free Edition.** Our workspace matches the published Free Edition limits:
- the only SQL warehouse is the *Serverless Starter Warehouse*: 2X-Small, serverless, max 1 cluster;
- there are no classic clusters;
- the cloud and region are AWS `us-east-2`, which is a supported Lakebase Autoscaling region.

No API reports the edition directly; this is the evidence available.

**The docs include one Lakebase project.**
[Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations) lists "One
Lakebase project per account, with scale-to-zero compute". It describes Free Edition as a no-cost offering under a
fair-usage policy: if usage goes over quota, compute is shut down for the rest of the day, and data and settings are
kept.

**Authentication:** the existing `wbg-databricks-free` profile, an OAuth user login with no secret stored in
`~/.databrickscfg`. It was used through the Databricks CLI v1.22.1 bundled with the VS Code extension, because there is
no `databricks` on `PATH`.

## 2. Was a project created, or did one already exist?

**None existed** (0 Lakebase Autoscaling projects, 0 Provisioned instances), so the one-project allowance was free.

**You created** `wbg-procurement-dev` in the UI. I made no create, update or delete call; my Databricks calls were
read-only listings plus credential generation. As read back from the API:

| Property | Value |
|---|---|
| Project | `projects/wbg-procurement-dev`, created 2026-10-26 07:16 UTC |
| **Postgres version** | **18**, not 16 as requested. The version is fixed at creation; you chose to proceed on 18 |
| Branch | `production` (default, READY) |
| Database | `databricks_postgres` |
| Endpoint | `primary`, read-write, ACTIVE |
| Compute | 1–1 CU (min = max = 1; 1 CU ≈ 2 GB RAM) |
| Scale-to-zero suspend timeout | 86,400 s (24 h) |
| History retention | 604,800 s (7 days) |
| Postgres native (password) login | Disabled: OAuth only |
| Projects in the account afterwards | 1 |

## 3. PostgreSQL and pgvector versions

| Item | Value |
|---|---|
| Server | PostgreSQL 18.6 (`server_version_num` 180006), x86_64 Linux |
| `vector` (standard pgvector) | 0.8.6 available. Installed in `databricks_postgres` only, with `CREATE EXTENSION IF NOT EXISTS vector` |
| `lakebase_vector` | **Not installed.** Lakebase Search was not enabled |
| Client | psycopg 3.3.6 (bundled libpq 18.6), pgvector-python 0.5.1, NumPy 2.5.4 |

## 4. Connectivity from the Mac

It works. The connection uses:
- TLS with `sslmode=require`;
- the user identity (`databricks_postgres` owner role);
- a 60-minute OAuth database credential, generated per connection with
  `databricks postgres generate-database-credential <endpoint>` and passed as the password in memory only.

**How the credential is handled:**
- the token is never printed, logged, stored, or included in exception messages, and a test enforces this;
- the connection settings are host, database, user and endpoint resource name;
- those settings live in the git-ignored `lakebase.local.env` (permissions `600`), and the committed template
  `lakebase.env.example` holds placeholders only;
- a scan found no login, host or token in source, tests, results or `pyproject.toml`.

**Session controls:**
- `connect_timeout` 30 s, which allows for a scale-to-zero cold start;
- `statement_timeout` 30 s, `lock_timeout` 10 s, `idle_in_transaction_session_timeout` 60 s;
- connections always closed in `finally`.

A full POC run, including credential generation, took about 38 s end to end. That is not a performance measurement.

## 5. `VECTOR(768)` inserts and retrieval

**Table:** `wbg_rag_poc.synthetic_embeddings`, with columns `item_id integer PRIMARY KEY`, `label text`,
`embedding vector(768)` and `created_at`.

**Data:** 500 deterministic synthetic unit vectors, from a NumPy Gaussian with seed 20261026. Each label is
`synthetic-NNNN`, and nothing is derived from real documents.

**Idempotency:** inserts use `ON CONFLICT (item_id) DO NOTHING`, and every DDL statement uses `IF NOT EXISTS`.
- The first live attempt created the extension, schema and table, and inserted all 500 rows. It then stopped on a
  client-side type conversion while reading a vector back: pgvector-python 0.5 returns a `Vector` object, which was then
  fixed in the code.
- Every later run, and the live tests, inserted **0** rows on both the first pass and the retry.
- The table holds exactly **500** rows.

**Round trip:** a stored vector read back equals the original (`allclose`, atol 1e-7).

## 6. Cosine search and HNSW index

Each of the 5 queries is a noisy copy of a known stored item (items 3, 77, 150, 299, 444). Top-10 by `<=>`, cosine
distance, compared with NumPy's exact float64 computation:

| Search | Top-1 = source item | Same top-10 order as NumPy | Recall@10 vs NumPy | Max distance difference |
|---|---|---|---|---|
| Exact scan (before the index) | 5 of 5 | **Yes** | 1.0 for all 5 | 3.1 × 10⁻⁸ |
| HNSW, default `hnsw.ef_search` = 40 | 5 of 5 | No | 0.9, 1.0, 0.9, 1.0, 1.0 | 6.4 × 10⁻³ (different 10th items) |
| HNSW, `hnsw.ef_search` = 200 (set per transaction) | 5 of 5 | **Yes** | 1.0 for all 5 | — |

**Index creation and catalog.** The statement was
`CREATE INDEX IF NOT EXISTS synthetic_embeddings_embedding_hnsw_cosine ON wbg_rag_poc.synthetic_embeddings USING hnsw
(embedding vector_cosine_ops)`, with pgvector defaults `m = 16` and `ef_construction = 64`. The catalog (`pg_index`,
`pg_class`, `pg_am`, `pg_opclass`) shows:
- access method `hnsw`;
- operator class `vector_cosine_ops`;
- `indisvalid = true`, `indisready = true`.

**Index creation versus planner use.**
- With default settings, `EXPLAIN` chooses **Limit → Sort → Seq Scan**. For 500 rows a scan is cheaper, so this is
  expected, and it does not mean the index failed.
- With `enable_seqscan = off` (`SET LOCAL`, this transaction only), the plan is **Limit → Index Scan using
  `synthetic_embeddings_embedding_hnsw_cosine`**. So the index is usable for `ORDER BY embedding <=> …`.

HNSW is approximate. On this deliberately hard data (random 768-d vectors, where nearest neighbours are barely
separated), the default `ef_search` missed one of the true top 10 in 2 of 5 queries. Raising `ef_search` restored exact
results. The production value must be set from the real-embedding benchmark, not from this synthetic test.

## 7. Measured storage (from the database)

| Object | Bytes |
|---|---|
| `databricks_postgres` database (includes system catalogs and extension objects) | 12,247,040 (11.7 MiB) |
| `wbg_rag_poc.synthetic_embeddings`, total incl. TOAST and indexes | 4,268,032 (4.07 MiB) |
| of which heap | 40,960 |
| of which TOAST (the 768-d vectors are stored out of line) | ≈ 2,138,112 (total − heap − indexes) |
| of which all indexes | 2,088,960 |
| of which the HNSW index | 2,056,192 (≈ 4.1 KB per vector) |
| of which the primary key | ≈ 32,768 |

**Calibrating the earlier projection.** With 500 vectors, table plus TOAST is about 4.4 KB per row, and HNSW about
4.1 KB per vector (≈ 1.34 × the 3,072 B of raw vector data). That is about **8.5 KB per 768-d embedding**, including the
index. For the projected 28.7 k–54.2 k retrieval units of one strategy
([PROJECT_STORAGE_ASSESSMENT.md](PROJECT_STORAGE_ASSESSMENT.md) §D), this gives about **0.24–0.46 GB**. That agrees
with the earlier 0.2–0.5 GB projection, which remains a projection until real embeddings are loaded.

`logical_size_bytes` for the branch was still reported as 0 by the Lakebase API right after the run. The database-side
figures above are authoritative.

## 8. Observed limits and unresolved Free Edition quotas

**Observed:**
- one project per account (the only project now);
- compute fixed at 1 CU;
- scale-to-zero with a 24 h default timeout;
- 7-day history retention;
- OAuth-only login;
- standard pgvector 0.8.6 available without enabling anything else.

**Unresolved, because the docs give no Free Edition numbers:**
- the Lakebase storage quota ("adjustable on request" for paid tiers) and the maximum CU;
- whether the 1 CU limit is a Free Edition cap or only this project's default;
- how much daily usage triggers the fair-usage shutdown;
- whether Lakebase usage counts against the same quota as other serverless compute.

**This report does not claim that the full corpus fits in Free Edition.**

## 9. Manual actions

**None required to keep the POC.**

**Optional:**
1. In project **Settings → Compute**, shorten the scale-to-zero timeout from 24 h (for example to 5 minutes) to use
   less of the fair-usage allowance.
2. To remove the test data later, run `DROP SCHEMA wbg_rag_poc CASCADE;` in `databricks_postgres`. That can't be
   undone, so it needs approval. The `vector` extension can stay.
3. The project is on Postgres 18 rather than 16. If PG 16 is ever required, the project must be recreated, and it is
   the only one allowed.

**To re-run:** from `infrastructure/lakebase_poc/`, on explicit request only.

```bash
LAKEBASE_LIVE_TESTS=1 ~/.local/bin/uv run --locked python -m lakebase_poc.run_poc     # results/…json
LAKEBASE_LIVE_TESTS=1 ~/.local/bin/uv run --locked pytest tests_live -q -p no:cacheprovider
~/.local/bin/uv run --locked pytest -q -p no:cacheprovider                            # offline only (default)
```

## 10. Protected components unchanged

**Local files:** an inventory comparison (SHA-256 per file) against the post-cleanup baseline found no addition, change
or removal outside `infrastructure/lakebase_poc/` and the earlier reports. Specifically:
- `pyproject.toml` / `uv.lock`: `uv.lock` is unchanged, since no dependency was added;
- `.venv-structured-mcp`, Anaconda and the requirements files;
- Bronze/Silver/Gold code, notebooks and runs; `discovery/`; the 2,000-project cohort;
- the approved 35-project manifests, PDFs and evidence; the RAG vectors and evaluation results;
- the Structured Data Agent, MCP tools and guardrails; the frozen ML models; the cleanup archives.

**Databricks:**
- No Unity Catalog object, Volume, table, KPI view, job, App or notebook was read for data, created or changed.
- The only Databricks calls were the listings (current user, Lakebase projects, branches, endpoints, database
  instances, SQL warehouses, clusters, metastore summary) and database-credential generation.
- In Lakebase, the changes are limited to the `vector` extension and the `wbg_rag_poc` schema in
  `wbg-procurement-dev` / `databricks_postgres`.

**Local PostgreSQL:** not accessed.

## Files (`infrastructure/lakebase_poc/`)

| File | Purpose |
|---|---|
| `lakebase_poc/settings.py` | Settings from the environment or `lakebase.local.env`; enforces TLS modes; redacted `repr` |
| `lakebase_poc/credentials.py` | OAuth credential through the Databricks CLI; the token never appears in output or errors |
| `lakebase_poc/connection.py` | TLS connection, session timeouts, guaranteed close |
| `lakebase_poc/synthetic.py` | Deterministic synthetic vectors and the NumPy reference ranking |
| `lakebase_poc/pgvector_store.py` | Parameterised, idempotent SQL: extension, schema and table, inserts, kNN, HNSW, catalog, plans, sizes |
| `lakebase_poc/run_poc.py` | One opt-in live run → `results/pgvector_poc_<UTC>.json` |
| `tests/` | 21 offline tests: the 8 environment tests + 13 POC tests (settings, credential handling, data, SQL contract), all passing. The default `pytest` run |
| `tests_live/test_live_pgvector.py` | 4 live tests. They skip unless `LAKEBASE_LIVE_TESTS=1`; all 4 passed |
| `lakebase.env.example` / `lakebase.local.env` | Settings template / local values (git-ignored, mode 600; no password) |

Sources:
- [Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)
- [Lakebase extensions](https://docs.databricks.com/aws/en/oltp/projects/extensions)
- [Manage projects](https://docs.databricks.com/aws/en/oltp/projects/manage-projects)
- [Connect an external app to Lakebase](https://docs.databricks.com/aws/en/oltp/projects/external-apps-connect)
