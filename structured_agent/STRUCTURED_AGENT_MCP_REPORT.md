# Structured Data Agent: MCP Interface, Transport, Security and Observability

**Updated:** 2026-10-25 (MCP transport integration) · **Status:** the seven tools work through a real MCP stdio client/server transport. All 41 MCP transport tests passed; the earlier in-process suites still pass (97 tests, 15/15 scenarios).

## 1. What changed in this task

| Change | Why | Files |
|---|---|---|
| Isolated environment `.venv-structured-mcp` (Python 3.12.2), with pinned direct dependencies and a full lock file | `mcp` 1.16.0 needs `anyio` ≥ 4.5; the shared Anaconda environment has 3.7.1 and was **not modified** | `mcp_env/requirements.txt`, `mcp_env/requirements.lock.txt` (114 packages), `mcp_env/setup_mcp_env.sh` |
| Server starts with explicit `transport="stdio"`, no banner; docstring states the single-principal limitation | Clean protocol stream and clear documentation | `mcp_server.py` |
| Middleware `AuditRejectedCalls` | Calls that FastMCP rejects before the service runs (unexpected argument, unknown tool) are now audited. Unknown tool names are logged as `unknown_tool`, never as client text | `mcp_server.py` |
| Audit fields `authorization` (granted / denied / not_evaluated) and `validation_status` (approved / rejected / not_run), plus `audit_protocol_rejection()` | Required audit content for every MCP request | `service.py`, `observability.py` (allow-list) |
| MCP client helpers and a CLI smoke check (official MCP Python SDK) | Real client for testing and manual use | `mcp_transport/client.py`, `mcp_transport/smoke_check.py` |
| MCP transport test suite (separate folder, run in the venv) | End-to-end tests through the protocol | `tests_mcp/` (`conftest.py`, `mcp_session.py`, `hold_connection.py`, `test_mcp_protocol.py`, `test_mcp_security.py`, `results/`) |

**No business logic changed:** registry, validator rules, gateway, policy, contracts and search are untouched.

## 2. Environment

| Item | Value |
|---|---|
| Base interpreter | `/opt/anaconda3/bin/python3.12` (3.12.2). Used only to create the venv; nothing was installed into the shared environment, which still has `anyio` 3.7.1 |
| Venv | `procurement-intelligence/.venv-structured-mcp`; `pip check`: no broken requirements |
| MCP stack | fastmcp 2.12.5, mcp 1.16.0, **anyio 4.11.0**, starlette 1.10.0, uvicorn 0.54.0, sse-starlette 3.6.0, python-multipart 0.0.32, httpx 0.28.1, Authlib 1.9.1 |
| Agent stack (same versions as the shared environment) | pydantic 2.13.3, sqlglot 30.24.0, psycopg 3.3.6, rank-bm25 0.2.2, sentence-transformers 5.4.1, transformers 4.57.3, torch 2.9.1, numpy 1.26.4, databricks-connect 17.3.14, opentelemetry-api/sdk 1.27.0, pytest 7.4.4 |
| Models | gte-base-en-v1.5 from the existing local Hugging Face cache, offline; nothing downloaded |

**Setup** (from `procurement-intelligence/`):

```bash
bash structured_agent/mcp_env/setup_mcp_env.sh             # reproducible install from requirements.lock.txt
```

**Run the server manually** (stdio; stdout carries protocol messages only):

```bash
WBG_MCP_PRINCIPAL=analyst-demo WBG_MCP_WORKSPACE=wbg-procurement-poc \
  .venv-structured-mcp/bin/python -m structured_agent.mcp_server
```

**Smoke check with a real client** (it starts the server itself):

```bash
.venv-structured-mcp/bin/python -m structured_agent.mcp_transport.smoke_check             # execution refused (default deny)
.venv-structured-mcp/bin/python -m structured_agent.mcp_transport.smoke_check --execute   # labelled dev execution
```

**Tests:**

```bash
.venv-structured-mcp/bin/python -m pytest structured_agent/tests_mcp -q -p no:cacheprovider   # through MCP (needs Databricks)
.venv-structured-mcp/bin/python -m pytest structured_agent/tests -q -p no:cacheprovider       # in-process
```

## 3. Request path (unchanged; no bypass)

MCP client → stdio → FastMCP (JSON-schema and argument validation) → service `_invoke`:
1. Pydantic request contract;
2. principal and workspace authorization;
3. handler: registry operations, or the **SQL Execution Validator** (sqlglot AST, 12 stages);
4. **execution gateway**: credential gate, re-validation, only the validated SQL, timeout;
5. result validation;
6. sanitised audit event and OpenTelemetry span.

→ MCP response (structured JSON matching the Pydantic response models).

**Identity:**
- The principal and workspace come only from the server's deployment configuration (`WBG_MCP_PRINCIPAL`, `WBG_MCP_WORKSPACE`).
- No tool accepts a principal or workspace argument. Sending one (`principal_id`, `workspace`) is rejected as an unexpected argument; tested through MCP and audited.
- **Per-user authorization is not implemented.** Every client of one server process acts as that server's single configured principal. This setup is for development only.

**Databricks credentials:**
- Default deny is preserved: without `WBG_ALLOW_DEV_OWNER_CREDENTIALS=true`, execution returns `CREDENTIAL_NOT_READ_ONLY` (tested through MCP).
- With the flag, every executed result is stamped `credential_mode=dev_owner_credentials` and `production_read_only=false`.
- Read-only access is enforced by the validator and gateway, **not by database permissions**.

## 4. Tools invoked successfully through MCP

All seven, over stdio with the official MCP Python SDK client:

| Tool | Verified through MCP |
|---|---|
| `search_table_registry` | Gold-only candidates for an analyst; request id kept and invocation id assigned; response validates against `SearchTableRegistryResponse` |
| `get_table_schema` | 46 authorized columns and 8 hidden (lineage) for `gold_contract_analytics`; grain `wb_contract_number` |
| `get_table_relationships` | `gold_contracts_to_portfolio` and `gold_contracts_to_country_context`, both verified |
| `get_metric_definition` | `sum(contract_amount_usd)`; an ambiguous wording returns `needs_clarification` / `AMBIGUOUS_METRIC` |
| `get_verified_sql_examples` | Templates with verification status |
| `validate_sql` | Full 12-check report; rejections typed |
| `execute_readonly_sql` | Live Databricks execution through the gateway; results match Silver baselines |

The server initialises in about 0.8 s; the embedding model loads on the first search.

## 5. MCP transport test results (41/41 passed, 95.6 s)

Run with `.venv-structured-mcp`. Every test talks to a separate server process over stdio. Saved results:
- `tests_mcp/results/mcp_transport_results.txt` (one line per test with its outcome)
- `tests_mcp/results/mcp_transport_junit.xml`

**Protocol** (`test_mcp_protocol.py`, 18 tests):
- **Handshake and discovery:** initialize handshake (server name, protocol version, tools capability); exactly the seven tools; input schemas (required and optional arguments; no principal or workspace argument).
- **Structured responses:** search, schema, relationships, metrics and examples validated against the Pydantic models.
- **Invalid arguments:** missing argument, wrong type, `principal_id`, `workspace`, unknown tool → MCP error results. A contract violation (`top_k=50`) → typed `INVALID_REQUEST`. The server keeps working after errors.
- **Failures:**
  - clean shutdown stops the server process;
  - a misconfigured server (no principal) fails at startup and the client gets an error, not a hang;
  - a server killed mid-session makes the pending call fail quickly;
  - a client killed abruptly → the orphaned server exits.

**Security and execution** (`test_mcp_security.py`, 23 tests):
- **Valid queries:**
  - a SELECT executes and matches the Silver baseline (contract 1803814: amount and method), with dev-credential provenance and all checks passing;
  - a reviewed template (award value by method, 18 methods) matches Silver;
  - `validate_sql` returns all 12 checks.
- **Rejected through `execute_readonly_sql`** (13 cases):
  - analyst → Silver, analyst → Bronze, Silver nested in a subquery → `UNAUTHORIZED_OBJECT`;
  - unknown table → `UNKNOWN_TABLE`; unknown column → `UNKNOWN_COLUMN`;
  - DROP, DELETE, UPDATE, INSERT inside a CTE → `FORBIDDEN_OPERATION`; stacked statements → `MULTIPLE_STATEMENTS`;
  - `reflect()` nested in a subquery → `FORBIDDEN_FUNCTION`;
  - LIMIT 50000 → `LIMIT_EXCEEDED`;
  - a fact-to-fact fan-out join → `FAN_OUT_RISK`.

  Each returned `query_id = null`, 0 rows and no provenance. The gateway creates a query id only immediately before calling Databricks, so **none of the blocked SQL reached Databricks**.
- **Restricted Silver access:** a steward can count Silver projects (2,000); a steward asking for `supplier_name` → `PROHIBITED_COLUMN`.
- **Impersonation:** passing `principal_id` → MCP error, audited as `INVALID_TOOL_ARGUMENTS`.
- **Default deny:** without the dev flag → `CREDENTIAL_NOT_READ_ONLY`.
- **Timeout:** a 1-second limit → `EXECUTION_TIMEOUT`, no rows, and the next call on the same server succeeds.
- **Concurrency:** 3 executions, 1 search and 1 DROP validation sent at once over one session. All answered with matching request ids and correct rows; the DROP was rejected.
- **Audit:** one event per request id, with invocation id, tool, status, duration, error codes, authorization result, validation status for SQL tools, and credential mode for executed SQL. The log contains no SQL, contract numbers, amounts, method names, principal ids or `@`.
- **Databricks unchanged:** the Delta version of all 23 Silver and Gold tables, and the list of all 30 objects, are identical before and after the whole MCP run.

## 6. Regression results (all actually run on 2026-10-25)

| Suite | Environment | Through MCP? | Result |
|---|---|---|---|
| MCP transport tests (`tests_mcp/`) | `.venv-structured-mcp` | **Yes** (stdio, separate server processes) | **41/41 passed** |
| Existing tests (`tests/`) | shared Anaconda | No (in-process) | **97/97 passed** |
| Existing tests (`tests/`) | `.venv-structured-mcp` | No (in-process) | **97/97 passed** |
| 15 evaluation scenarios | `.venv-structured-mcp` | No (in-process service) | **15/15 passed**; 10/10 executed results match baselines; `gold_project_portfolio` Delta version 71 before and after (run `20261025T215235Z`) |
| 15 evaluation scenarios | shared Anaconda | No (in-process service) | **15/15 passed**; 10/10 baselines match; version 71 unchanged (run `20261025T215335Z`) |
| Smoke check, default deny and `--execute` | `.venv-structured-mcp` | **Yes** | All 7 tools answered; execution refused, then executed with dev labelling |

**Failure during development, disclosed:** the first MCP run had 40/41 passing. The audit-content check found a contract number in the audit log. It came from my test's own request id (`concurrent_1803814`), because request ids are client-supplied and logged verbatim. The test was changed to opaque ids, and the full suite was re-run (41/41).

## 7. Observability

- The audit sink is JSONL (`WBG_AUDIT_LOG`; each test server writes its own file). Attributes are allow-listed; the principal is hashed.
- OpenTelemetry spans are preserved (no-op without a configured provider; the SDK export path is covered by `tests/test_observability.py`).
- Logfire was not installed or configured; no hosted telemetry was sent.

## 8. Remaining blockers and limitations

1. **Per-user authorization is not implemented:** one principal per server process. Multi-user use needs authenticated MCP sessions (e.g. OAuth/JWT at an HTTP transport) mapped to registry roles.
2. **No database-enforced read-only access:** a dedicated read-only Databricks principal is still needed. Today the owner credential runs in labelled dev mode only.
3. **One request at a time per server process:** FastMCP 2.12.5 calls the synchronous tool functions directly on the server's event loop, and the gateway uses one Databricks worker. Concurrent requests are answered correctly but queued; a long query (up to the 60 s timeout) delays other calls on the same server. Options for later: async tools that offload to worker threads, or several server processes.
4. **Calls rejected by the MCP SDK's JSON-schema check are not audited:** missing or wrongly typed arguments are refused by the SDK's low-level handler before any server code (or middleware) runs. They are refused but leave no audit event. Unexpected arguments and unknown tools are audited.
5. **Request ids are client-supplied and logged verbatim:** callers (the future orchestrator) must use opaque ids such as UUIDs, never business values.
6. **Only stdio is tested.** HTTP/SSE transports, authentication and TLS are not set up.
7. **Unchanged from before:** no LLM SQL generation (no provider approved); Logfire not installed; synonyms and metrics await SME review (D-23 pending); embeddings use `real[]` instead of pgvector for the registry.

## 9. Readiness for LangGraph MCP integration

**Ready for development integration** over stdio:
- one MCP server per principal, launched from `.venv-structured-mcp`;
- all seven tools work end to end, with the security path, typed errors, audit and Databricks safety verified through the protocol.

**Not ready for production or multi-user use** until items 1–3 are resolved:
1. per-user identity;
2. a read-only Databricks principal;
3. a decision on concurrency.

Waiting for architecture review before the next stage.
