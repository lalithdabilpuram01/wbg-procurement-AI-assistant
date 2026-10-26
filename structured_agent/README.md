# Structured Data Agent: Table Registry, semantic discovery and validated SQL

A standalone module that answers structured procurement questions over the Databricks Silver and Gold layers.
1. It finds the relevant registered tables and metrics.
2. It produces SQL from a reviewed template, or later from an LLM.
3. It validates that SQL against the registry and the access policy.
4. It executes it only through a read-only gateway.

It is designed to become one specialist node of the LangGraph orchestrator; no orchestration or UI is built here.

## Layout

```text
structured_agent/
  config.py                 settings from environment variables (DSNs, Databricks profile, limits, dev-mode flag)
  contracts.py              Pydantic request/response contracts for the 7 operations; typed error codes
  policy.py                 principals, roles, object and column authorization
  search.py                 hybrid discovery: exact names, synonyms, BM25, gte-base embeddings, RRF
  generation.py             SQL generation interface: template (no LLM), LLM providers (disabled), one bounded correction
  sql_validation/           SQL Execution Validator (sqlglot AST): structure, lineage, joins/grain, rules, orchestrator
  execution/                Databricks executor interface, gateway (credential gate, timeout), result validation
  observability.py          sanitised audit events (JSONL) and OpenTelemetry spans
  service.py                the 7 operations behind one invocation path (contract -> authorization -> handler -> audit)
  mcp_server.py             FastMCP domain server exposing exactly the 7 operations (stdio)
  mcp_env/                  isolated MCP environment: pinned requirements, lock file, setup script
  mcp_transport/            MCP client helpers and smoke check (official MCP Python SDK)
  tests_mcp/                41 end-to-end tests through the real stdio transport (run in .venv-structured-mcp)
  metadata/                 discovery, live verification, curated inputs, bundles, METADATA_INVENTORY.md
  registry/                 registry models, column policy, builder, PostgreSQL store, vector index, init script, SQL DDL
  tests/                    97 tests (validator, gateway, generation, observability, registry store, MCP tools)
  evaluation/               15 live scenarios with independent baselines; runs/<UTC>/
```

## Run (from `procurement-intelligence/`, Python `/opt/anaconda3/bin/python3`)

```bash
# 1. one-time database bootstrap (PostgreSQL superuser; creates wbg_structured_registry and two roles)
psql -d postgres -v ON_ERROR_STOP=1 -f structured_agent/registry/sql/bootstrap_database.sql
# 2. metadata (read-only Databricks), live verification, registry initialisation (explicit; no scheduler)
python -m structured_agent.metadata.discover_databricks
python -m structured_agent.metadata.verify_metadata
python -m structured_agent.registry.init_registry
python -m structured_agent.metadata.write_inventory_report
# 3. tests (no Databricks needed) and live evaluation (labelled development mode)
python -m pytest structured_agent/tests -q -p no:cacheprovider
WBG_ALLOW_DEV_OWNER_CREDENTIALS=true python -m structured_agent.evaluation.run_evaluation
# 4. MCP server in its own environment (the shared Anaconda env has anyio 3.7.1, too old for mcp 1.16)
bash structured_agent/mcp_env/setup_mcp_env.sh                                   # creates .venv-structured-mcp
WBG_MCP_PRINCIPAL=analyst-demo WBG_MCP_WORKSPACE=wbg-procurement-poc \
  .venv-structured-mcp/bin/python -m structured_agent.mcp_server                 # stdio server
.venv-structured-mcp/bin/python -m structured_agent.mcp_transport.smoke_check [--execute]
.venv-structured-mcp/bin/python -m pytest structured_agent/tests_mcp -q -p no:cacheprovider
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `WBG_REGISTRY_DSN` | `postgresql://wbg_registry_reader@localhost:5432/wbg_structured_registry` | runtime registry access (SELECT-only role) |
| `WBG_REGISTRY_ADMIN_DSN` | `postgresql://wbg_registry_admin@localhost:5432/wbg_structured_registry` | registry initialisation only |
| `WBG_DATABRICKS_PROFILE` | `wbg-databricks-free` | execution credential; point it at a read-only principal's profile to replace the owner credential |
| `WBG_ALLOW_DEV_OWNER_CREDENTIALS` | `false` | allow a write-capable credential for controlled assessment testing (results stamped `dev_owner_credentials`) |
| `WBG_SQL_MAX_ROWS`, `WBG_SQL_TIMEOUT_SECONDS` | 1000, 60 | execution budget |
| `WBG_MCP_PRINCIPAL`, `WBG_MCP_WORKSPACE` | none | identity of an MCP server deployment (never a tool argument) |
| `WBG_AUDIT_LOG` | `structured_agent/runs/audit_events.jsonl` | local audit sink |
| `WBG_ALLOW_EXTERNAL_LLM` + provider key | unset | required before any LLM provider adapter can be constructed |

Passwords are never stored in code; when the server requires one, libpq reads `PGPASSWORD` or `~/.pgpass`.

## Reports

- [STRUCTURED_AGENT_REGISTRY_REPORT.md](STRUCTURED_AGENT_REGISTRY_REPORT.md)
- [STRUCTURED_AGENT_SQL_VALIDATION_REPORT.md](STRUCTURED_AGENT_SQL_VALIDATION_REPORT.md)
- [STRUCTURED_AGENT_MCP_REPORT.md](STRUCTURED_AGENT_MCP_REPORT.md)
- [metadata/METADATA_INVENTORY.md](metadata/METADATA_INVENTORY.md)
