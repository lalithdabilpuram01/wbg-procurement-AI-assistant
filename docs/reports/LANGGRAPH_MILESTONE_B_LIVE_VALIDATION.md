# LangGraph Milestone B — Live Claude, Lakebase Checkpointing and Logfire: Validation Report

**Date:** 2026-10-26 · **Basis:** `LANGGRAPH_INTEGRATION_READINESS.md`, `LANGGRAPH_MILESTONE_A_VALIDATION.md`
**Code:** `orchestrator/` (uv project). Live evidence is in `orchestrator/live_results/`.

## Summary

| Area | Result |
|---|---|
| Live Claude (Portkey) | **Live validated.** 4 of 5 attempts used: 3 completions and 1 deliberate error probe. **Estimated spend $0.025624**; conservative committed spend $0.035717 against the $1.00 ceiling. The planner, the response generator, structured output, contract validation and HITL all worked on Claude. |
| Lakebase checkpointing | **Live validated** (follow-up run, same day). After you created the `dev-orchestrator` branch and the `wbg_orchestrator_ckpt` schema, **9 / 9 live tests passed** on Lakebase PostgreSQL 18.6: isolation, setup, persistence, restart, HITL, user isolation, secrets, crash recovery and OAuth refresh. Every other schema on the branch is unchanged, and the read-only inventory of `production` is identical before and after. The same checks also pass **offline** on a throwaway local PostgreSQL 18.6 cluster (12 tests). |
| Dependencies | `langgraph-checkpoint-postgres==3.2.0` and `psycopg-pool==3.3.3` were added to the orchestrator uv lock only. Nothing else changed. |
| Logfire | **Live validated (export).** One bounded, metadata-only export went to the existing Logfire project using the existing token. In-memory verification found 0 leaks across 59 spans and events, and the SDK reported no export errors. Visual confirmation in the Logfire UI is still open. |
| Regressions | Orchestrator **111 / 111** passed. All existing offline suites pass. The persistent audit log is unchanged (SHA-256 `d34868ea…2bb2680`). |

Work stopped after Milestone B. There was no Databricks Apps deployment, no data migration and no GitHub push. The only Lakebase writes are checkpoint test data in `wbg_orchestrator_ckpt` on `dev-orchestrator`.

---

## 1. Actual Portkey / Claude model used

- **Configured model** (existing settings loader, `PORTKEY_CLAUDE_MODEL`): `@wbg-anthropic/claude-sonnet-5-5`. This is the Portkey Model Catalog entry of the approved `@wbg-anthropic` integration. Claude Sonnet 5.5 is the API model `claude-sonnet-5-5`.
- **Route:** Claude-only, through the existing `PortkeyClaudeClient`:
  - `POST https://api.portkey.ai/v1/chat/completions`;
  - no `x-portkey-config` header;
  - the existing `LiveAccess` dual opt-in (`--confirm-live-call` plus `WBG_ALLOW_LIVE_LLM_CALLS=true` in the process environment only, never in `.env`).
- **Provider check:** every reply passed the existing provider check (reply model contains `claude`, provider `anthropic`). No fallback provider answered.
- **Authentication:** the existing `PORTKEY_API_KEY` from `.env`, read by the existing loader. It was never printed or stored, and the result files contain neither it nor any secret-shaped value.
- **Message format** (OpenAI-compatible Portkey chat completions):
  - request: `system` and `user` messages, `max_tokens`, and `response_format: {type: json_schema, json_schema: {name, strict: true, schema}}`, with no sampling parameters;
  - reply: `choices[0].message.content` (JSON text), `finish_reason`, `usage.prompt_tokens` / `usage.completion_tokens`.

## 2. Verified pricing and limits

**Pricing** was verified 2026-10-26 at https://platform.claude.com/docs/en/about-claude/pricing:

| Model | Input | Output | Notes |
|---|---|---|---|
| Claude Sonnet 5.5 | $2 / MTok | $10 / MTok | Full 1M context at standard pricing; no long-context premium. |

- The US-only `inference_geo` option applies a 1.1× multiplier. We do not request it, but the worst-case estimate includes the multiplier anyway.
- These prices are in `live_llm.SONNET_5_5_PRICES`. The runner refuses any model other than `@wbg-anthropic/claude-sonnet-5-5`, because pricing was verified only for that model.

**Controls, all application-side** (`wbg_orchestrator/live_llm.py`, `BudgetedClaudeClient` subclassing the existing client):

| Control | Setting |
|---|---|
| Attempts | At most **5**, including failures. Persisted in `live_results/claude_spend_ledger.json`, saved and fsynced **before** each request, so a rerun cannot exceed the limit. |
| Spend | At most **$1.00** committed. Committed spend = settled actual cost × 1.1, plus the full worst case for every failed or unknown attempt. |
| Worst case per request | (input bound × $2 + `max_tokens` × $10) / 1M × 1.1. The input bound is 1 token per UTF-8 byte of system + user + schema, plus 2,000 framing tokens. A request is refused if its worst case would push committed spend over the ceiling. |
| Output and reasoning tokens | Explicit `max_tokens` per schema: plan **8,000**, answer **4,000**, error probe **256**. On Claude, `max_tokens` bounds thinking and answer together, so it is also the reasoning-token cap. The existing client exposes no separate effort or thinking control. |
| Input tokens | The pre-call bound above. The ledger locks if a reply reports more input tokens than the bound. |
| Timeout | 120 s per request: client timeout plus `x-portkey-request-timeout`. |
| Hidden retries | None in the client. No Portkey Config means no gateway retries. Verified per response through `x-portkey-retry-attempt-count`, which was **`0` on all 4 attempts**. Any other value locks the ledger. |

The $1 ceiling is enforced by this application, not by the provider.

## 3. Number of live API attempts

**4 of 5.** One attempt remains in the ledger, and no further live calls were made.

| # | Purpose | max_tokens | Input bound | Outcome | Duration |
|---|---|---|---|---|---|
| 1 | Simple response (`investigation_answer`) | 4,000 | 3,145 | ok | 3.6 s |
| 2 | Investigation plan (`investigation_plan`) | 8,000 | 4,305 | ok | 7.2 s |
| 3 | Investigation answer (`investigation_answer`) | 4,000 | 6,851 | ok | 6.2 s |
| 4 | Error probe: non-existent model of the approved integration | 256 | 2,143 | HTTP 404 `not_found_error` → `provider_bad_request` (not transient) | 0.3 s |

## 4. Actual token usage and estimated total cost

| # | Input tokens | Output tokens | Estimated cost | Reserved worst case |
|---|---|---|---|---|
| 1 | 675 | 154 | $0.002890 | $0.050919 |
| 2 | 1,231 | 811 | $0.010572 | $0.097471 |
| 3 | 2,101 | 796 | $0.012162 | $0.059072 |
| 4 | 0 | 0 | $0 (rejected request) | $0.007531 |
| **Total** | **4,007** | **1,761** | **$0.025624** | $0.214993 |

- **Conservative committed spend:** $0.035717. This is the actual cost × 1.1, plus the probe's full worst case.
- **Pre-call bounds held:** actual input stayed well under every bound (675 / 3,145, 1,231 / 4,305, 2,101 / 6,851). No attempt was truncated.
- **Counter difference:** the graph's own `usage.llm_calls` reads 2 and 3 for the two investigations. Each includes the `llm_calls=1` reported by the **synthetic** structured-data fixture. The authoritative live count is the ledger: 3 completions plus 1 probe.

## 5. Claude planner and response results

**Simple response** (scenario 1; fast path; synthetic structured result, value 1234 from the test fixture):
- The supervisor chose the fast path, so there was no planner call.
- Claude returned **1 claim, which was kept (0 removed)**: "In fiscal year 2024, 1234 bid invitations (tender invitation notices) were published, according to the notice activity KPI view." It cites the approved structured evidence.
- Claude added an accurate limitation: one query result, and tender invitation notices used as the measure.
- Status: `answered`.

**Structured investigation plan** (scenario 2). The question was: "Investigate how many bid invitations were published in fiscal year 2024, and then check what the Ethiopia Expressway Development Support Project documents say about procurement delays."

| Task | Worker.operation | Depends on | Execution | Outcome |
|---|---|---|---|---|
| t1 | structured_data.describe_metric | — | no | succeeded (synthetic registry fixture) |
| t2 | structured_data.answer_data_question | t1 | **yes** | succeeded (synthetic workflow fixture; executed once, after approval) |
| t3 | document_intelligence.retrieve_passages | — | no | succeeded (**real local retrieval**, default `pilot_v2`) |
| t4 | document_intelligence.answer_document_question | t3 | no | partial (real local `AnswerPipeline`, evidence-only generator) |

- Claude's rationale correctly split the question into a structured count, defined via the metric first, and a document review.
- **Response:** **5 claims, all kept (0 removed)**:
  - the count, citing the metric definition and the approved structured result;
  - four claims on Ethiopia Expressway procurement-delay risks, citing PAD 24440102 pp. 76–78 and p. 45. They cover non-standard Design & Build bidding documents, rated Substantial; mitigation through Output Performance Road Contract (OPRC)-based documents agreed before negotiations; two-envelope bid-evaluation delays, rated Moderate; and ERA quality assurance.
- **Limitations:** Claude added 4 accurate ones; the validator added 5 (missing verified evidence for t2–t4, unverified PDF table values, one partial task).
- **Status:** `partial`. This is correct, conservative behaviour:
  - Claude asked for `validated_query` evidence on t2, but the executed task produces `structured_result`;
  - the document requirements asked for *verified* passages, but most citations had unchecked pages.

  Both requirements were reported as unmet instead of being silently satisfied.
- **No figures came from PDF tables:** 6 of the 9 evidence records were `document_table`, all `NOT_APPROVED` with `pdf_table_value_unverified`. No claim stated a figure from them; the claims that cite them are numberless narrative.

## 6. Structured-output and contract validation

- **Schema sanitising:** Claude's structured outputs reject `minLength`/`maxLength`, numeric bounds and complex array constraints. A new `claude.strict_output_schema` removes those keywords and closes every object (`additionalProperties: false`, all properties required). The full Pydantic models (`DraftPlan`, `DraftResponse`) still validate each reply client-side, so no constraint is lost.
- **Live result:**
  - Portkey and Claude accepted the strict `json_schema` request on both schemas, with no 400 errors.
  - All 3 completions returned parseable JSON that validated against the strict contracts.
  - The plan passed `build_plan` and then the deterministic `ExecutionController.validate_plan` with **0 errors**: DAG, permissions, operation allow-list, evidence producibility and budgets.
  - The HITL policy then required approval (`read_only_sql_execution`, `large_plan`). **No task ran** until the owner approved; afterwards the SQL task ran exactly once.
- **Not verifiable from outside:** whether Portkey enforces the schema server-side through Claude's structured outputs, or Claude merely followed it. In both cases the client-side contract remains the gate.
- **Offline (MockTransport):** the malformed-plan, schema-violation, replan and truncation paths are covered (`test_plans.py`, `test_live_adapter.py`).

## 7. Lakebase development branch availability

- **Read-only inspection** (`live/lakebase_inspect.py`, CLI `list-branches` plus a session with `default_transaction_read_only=on`) found that project `wbg-procurement-dev` has **one branch: `production`** (default, READY, not protected, 38.7 MB). **There is no development branch.**
- **What was not done:** as instructed, no branch was created and `production` was not used for test writes. **Live checkpoint writes did not run.**
- **Facts from the read-only session:**
  - PostgreSQL **18.6** (fd34222), database `databricks_postgres`, TLS on;
  - the role is not a superuser, but can create schemas in the database;
  - the default `search_path` is `"$user", public`.

  That default means an unpinned `PostgresSaver.setup()` would create its tables in `public`. The isolation design (§8) exists to prevent exactly that.
- **Follow-up (2026-10-26, after your approval):** you created the branch **`dev-orchestrator`** (READY, endpoint `primary`, read-write, PG 18.6, TLS) and the empty schema **`wbg_orchestrator_ckpt`**.
  - My role has `USAGE` and `CREATE` on that schema but does not own it.
  - The branch also carries copies of `wbg_rag_pdf_poc` and `wbg_rag_poc`.
  - The live tests resolve the host with `databricks postgres get-endpoint` and use your existing Lakebase login. They need no connection string or stored password.
  - The settings loader refuses `production` and `main`.

## 8. Checkpoint schema and objects

**Created in Lakebase** on `dev-orchestrator`, schema `wbg_orchestrator_ckpt` (live follow-up run). The same result was obtained earlier on the local PostgreSQL 18.6 cluster. `isolated_setup` created exactly these **11 objects, all in `wbg_orchestrator_ckpt`**, and nothing anywhere else:

- **Tables:** `checkpoint_migrations`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`.
- **Primary keys:** `checkpoint_migrations_pkey`, `checkpoints_pkey`, `checkpoint_blobs_pkey`, `checkpoint_writes_pkey`.
- **Indexes:** `checkpoints_thread_id_idx`, `checkpoint_blobs_thread_id_idx`, `checkpoint_writes_thread_id_idx`.

These come from the saver's 10 migrations, inspected in `langgraph/checkpoint/postgres/base.py`: `CREATE TABLE IF NOT EXISTS …`, `CREATE INDEX CONCURRENTLY …` and `ALTER TABLE …`, all unqualified.

**Isolation mechanism** (`wbg_orchestrator/checkpointing.py`):
- **Search-path pinning:** every pooled connection runs `set_config('search_path', '"wbg_orchestrator_ckpt"')` (plus statement, lock and idle-transaction timeouts) before first use.
- **Pre-setup check:** before `setup()`, `verify_isolation` refuses unless:
  - the effective schemas are exactly `[wbg_orchestrator_ckpt]`;
  - the schema exists;
  - the role has `USAGE` and `CREATE`;
  - the schema holds only checkpointer objects.
- **Before/after diff:** `isolated_setup` compares every other schema's objects before and after `setup()`, and fails if anything changed.
- **Explicit creation, no deletion:** the schema is created only by the explicit `create_schema` step. Nothing is ever dropped.

## 9. PostgreSQL checkpointer compatibility

| Item | Result |
|---|---|
| `langgraph-checkpoint-postgres` 3.2.0 | Requires `langgraph-checkpoint>=4.3.0,<5`, `psycopg>=3.2`, `psycopg-pool>=3.2`, `orjson>=3.11.5`. All are satisfied by the existing pins (`langgraph` 1.1.10, `langgraph-checkpoint` 4.3.0, psycopg 3.3.6). `uv add` resolved 183 packages; only these two packages were added, and no existing pin changed. |
| PostgreSQL 18.6 | Setup, put / get / list and interrupt-resume all work on local 18.6, the same server version as Lakebase (18.6). |
| Connection requirements | `autocommit=True`, `row_factory=dict_row`, `prepare_threshold=0` are set on every pooled connection. `CREATE INDEX CONCURRENTLY` needs autocommit; pipeline mode is used where supported. |
| Deserialization | `JsonPlusSerializer(pickle_fallback=False, allowed_json_modules=None, allowed_msgpack_modules=None)`, LangGraph's strict mode. Only the safe built-in types can be revived, including `Interrupt` and `Command` (HITL works). Graph state is plain JSON. |
| Pool bounds | `psycopg_pool.ConnectionPool`: min 1, max 2, 30 s wait, `max_lifetime` 40 min, `max_idle` 5 min, connections health-checked on checkout. Separate from all other application connections (`application_name=wbg-orchestrator-checkpoints`). |

## 10. Checkpoint persistence, interrupt, restart and resume

Offline validated on the local PostgreSQL 18.6 cluster (`tests/test_checkpointing.py`, `tests/pg_ephemeral.py`):
- **The cluster:** a new data directory in the system temp folder, unix socket only (no TCP), deleted afterwards. It never touches the existing local PostgreSQL server or its data.
- **The layout mirrors Lakebase:**
  - database `databricks_postgres`;
  - the existing schemas `wbg_rag_poc` (with data) and `public`;
  - a non-superuser application role.
- **A deliberate trap:** `public` is left creatable by that role, to prove that pinning, not luck, keeps the checkpointer out.

| Test | Result |
|---|---|
| Save, then read back with `get_tuple` | Identical `request`, `plan`, `evidence`, `results`, `task_states`, `response`, `usage` |
| HITL interrupt → "restart" (pool closed; new pool, saver, engine, workers and ticket secret) → resume with the original thread ID by the owner | `answered`; the SQL task executed **exactly once**; a second resume raises `ApprovalNotPending` |
| Rejection persisted | Re-read after a new engine: `rejected`, nothing executed |
| Crash mid-run (a worker raises an uncatchable `BaseException` after approval) | The last checkpoint is intact with the approval recorded. The new `InvestigationEngine.recover()` continues it to `answered` with one execution |
| Failed task | The error codes and task states are preserved and identical after re-reading |
| Other schemas | Object inventory and `wbg_rag_poc` rows unchanged; `public` holds only its original table |

**Live Lakebase** (`tests_live/test_live_checkpointing.py`; opt-in with `WBG_CKPT_LIVE_TESTS=1`; no LLM). **9 passed in 36.5 s** on `dev-orchestrator`:

| # | Check | Result |
|---|---|---|
| 1 | Isolation and setup | An unpinned connection is refused (Lakebase default path `"$user", public`). The pinned path resolves to exactly `[wbg_orchestrator_ckpt]`. Setup created the 11 objects listed in §8, and only those; a second setup created none. TLS was on. |
| 2 | Save and read back | Identical `request`, `plan`, `evidence`, `results`, `task_states`, `response`, `usage`. |
| 3 | HITL across a restart | After the interrupt, all connections were closed and a new pool, saver, engine and workers were built. The owner resumed with the original thread ID: `answered`, with exactly 1 execution. A second resume raised `ApprovalNotPending`. |
| 4 | No secrets or raw rows | 37,341 bytes of the thread's checkpoints, blobs and writes were checked. No synthetic secret and no rows beyond the 25-cell fact limit. SQL is stored only as a hash. |
| 5 | Crash recovery | A crash after approval left a checkpoint that `recover()` completed: `answered`, 1 execution. |
| 6 | Failed task | Status `failed`; errors (`cannot_answer`, then the exhausted fake replanner) and task states are identical after re-reading. |
| 7 | OAuth refresh | Two real CLI credentials (different tokens) were both accepted on newly opened connections, with simulated time past the 45-minute refresh point and the pool drained. |
| 8 | Tracing | `checkpoint_saved` and `checkpoint_resumed` events from `PostgresSaver`, carrying checkpoint IDs. |
| 9 | Other schemas | Objects and exact row counts of every other schema on the branch are unchanged (`wbg_rag_pdf_poc` 311 rows, `wbg_rag_poc` 500). |

- **Stored on the branch:** 6 test threads, holding 89 checkpoints, 171 blobs and 332 writes (0.84 MB). These rows were not deleted.
- **`production`:** the read-only inventory before and after is identical (schemas, table rows, branches), with no checkpoint tables. Evidence: `live_results/lakebase_checkpoint_live_results.json` and `lakebase_inventory_{before,after}.json`. The Claude-session inventories are kept as `*_claude_session.json`.

## 11. User-isolation test results

- **Offline (local PostgreSQL 18.6):**
  - after a restart, another user (BOB) gets `InvestigationNotFound` on `resume`, `outcome` and `recover`;
  - the owner (ALICE) resumes successfully;
  - checkpoint thread IDs are derived from the tenant, the owner hash, the conversation and the investigation.
- **The Milestone A isolation tests still pass:** threads, approvals and memory (`test_conversation.py`, `test_hitl.py`).
- **Live (Lakebase):** after a full restart, BOB was refused on `resume`, `outcome` and `recover`, and ALICE resumed successfully (check 3 in §10).
- **Live (Claude session):** the approval was granted by the requesting synthetic owner. The approval audit records IDs only.

## 12. OAuth refresh and connection behaviour

- **`TokenProvider`:**
  - fetches lazily and is thread-safe;
  - refreshes 15 minutes before the 60-minute expiry, i.e. after 45 minutes;
  - reuses the existing `lakebase_poc.credentials.generate_token` (Databricks CLI, `generate-database-credential`, the profile's OAuth U2M login).
- **Per-connection credentials:** `oauth_connection_class` sets the current token as the password of **each new physical connection**, so tokens never appear in a DSN.
- **Pool rotation:** `max_lifetime` of 40 minutes rotates connections before the token expires.
- **Offline validated:**
  - reuse, then refresh at 45 minutes;
  - a new connection after expiry receives the new token (`conn.info.password` checked);
  - pooled connections are autocommit;
  - `repr()` never shows a token.
- **Live:**
  - the checkpoint pool connected to `dev-orchestrator` over TLS with CLI-generated OAuth credentials;
  - two different real tokens were each accepted on newly opened connections (check 7 in §10);
  - the read-only `production` inspections used their own separate connections.
- **Exclusions:** no tokens are printed, logged or persisted; no passwords or service principals were created.

## 13. Logfire traces and export status

**Spans and events** (`orchestrator.*`, metadata only):

| Span or event | Covers |
|---|---|
| `node.supervise` | Supervisor routing |
| `node.plan` | Planner execution |
| `node.validate_plan` | Controller plan validation |
| `dispatch`, `node.execute`, `task_attempt` | Worker dispatch |
| `llm_call` (new) | Provider calls. Carries model slug, attempt, status, tokens, `cost_usd`, `worst_case_usd`, `committed_usd`, `gateway_retry_count`, HTTP status and provider error type |
| `investigation_finished` | Totals for tokens, LLM calls, cost and replans |
| `checkpoint_saved`, `checkpoint_resumed` (new) | Checkpoint ID and thread, checkpointer class, step |
| `circuit_breaker_transition`, `task_retry` | Breaker and error events |
| `approval_requested`, `approval_decision` | HITL interruptions and decisions |

- **Correlation:** every node span and event now carries `investigation_id`, `conversation_id` (the conversation thread ID), `plan_id` (once a plan exists) and the pseudonymous `owner_hash`. Task attempts carry `task_id` and `plan_id`. Provider-call spans are children of the `plan` or `respond` node span.
- **Scrubbing:** the existing allow-list applies. Only the listed keys are kept, and string values must look like identifiers or codes (no spaces, at most 100 characters). Synthetic Portkey-key, OAuth-token and Logfire-token secrets were injected into questions and plan tasks, and **none appeared** in any span. This was tested offline and in the live session (scenario 0).
- **Live session check:** 59 spans and events across 3 investigations.
  - **0 leaks** found when searching for the question text, the claim texts, the real Portkey key, the real Logfire token and the synthetic secrets.
  - Every node span is correlated, and every investigation `llm_call` has a correlated parent.
- **Export:**
  - bounded and metadata-only, to the **existing** Logfire project with the **existing** `LOGFIRE_TOKEN`, enabled in-process for this session only (service name `wbg-orchestrator-milestone-b`; `.env` still says `LOGFIRE_SEND_TO_LOGFIRE=false`);
  - **0 warnings or errors** from the SDK, OpenTelemetry or the HTTP stack during export and flush;
  - not verified: arrival in the Logfire UI, since there is no read token here.
- **LangSmith:** forced off for every graph run, as in Milestone A, and still covered by its test.
- **No new external infrastructure** was created.

## 14. Circuit-breaker and guardrail test results

- **Offline:**
  - breaker open/close and retry bounds still pass;
  - provider failures through the budgeted client are typed and charged at worst case, with exactly one HTTP request each and no hidden retry: timeout → `provider_timeout`, 429 → `provider_rate_limited`, 529 → `provider_unavailable`, 400 → `provider_bad_request`, `finish_reason=length` → `model_output_truncated`, a non-Claude reply → `unexpected_provider`;
  - refusals at the attempt limit, at the cost limit, and when the ledger is locked by gateway retries or an input-bound breach.
- **Live:**
  - the real NeMo input guardrails (no LLM rails) admitted all 3 live questions;
  - the 404 probe was classified as non-transient, so it does not count towards the LLM breaker;
  - the LLM breaker recorded 3 successes.
- **New hardening:** the worker adapters now apply the existing `redact_secrets` patterns to fact values, summaries and excerpts. Secret-shaped values in worker output are redacted before they reach state, checkpoints or prompts.

## 15. Existing component regression tests

The audit log was routed to a scratch file. `structured_agent/runs/audit_events.jsonl` has the same SHA-256 before and after (`d34868ea4831e463f8090f114efdbc2de38ae2253eff542da36e8a39c2bb2680`, 232,854 bytes).

| Suite | Interpreter | Result |
|---|---|---|
| Orchestrator (82 Milestone A + 29 new) | orchestrator uv env | **111 passed** |
| `structured_agent/tests`, `tests_workflow_mcp`, `conversation/tests` (routing), `app_config`, RAG offline loading (guardrails included) | `.venv-structured-mcp` | 501 passed |
| Same, without the workflow MCP suite | base | 454 passed, 5 skipped |
| `rag/tests` · `rag/ingestion/tests` · `rag/embedding_cache/tests` · `rag/release_v2_1/tests` | base | 72 · 45 · 101 · 30 passed |
| `ml/tests` · `discovery/tests` · `audits/pdf_table_quality/tests` · `infrastructure/uc_volume_poc/tests` | base | 23 · 129 · 12 · 5 passed |
| `infrastructure/lakebase_poc/tests` (offline) | lakebase_poc `.venv` | 41 passed |
| `structured_agent/tests_mcp` | — | **Not run.** It opens live Databricks sessions and appends to the persistent audit log |

**Compatibility checks (§5 of the brief):** confirmed by the passing Milestone A scenario tests:
- the fast path bypasses planning;
- complex requests go through the planner and controller;
- workers refuse calls without a controller ticket;
- retries and breakers are bounded;
- HITL is user-isolated;
- compression keeps constraints and evidence references;
- the RAG default is still `pilot_v2`, and `pilot_v2_1` remains opt-in;
- partial failures are marked `partial`.

**Inventory diff since Milestone A:** 0 files removed. Every addition and change is under `orchestrator/` (636 new venv files for the two packages). The only other new file is the Milestone A report.

**Code changes:**
- **New files:** `checkpointing.py`, `live_llm.py`, `live/`, `live_results/`, 3 test modules, and the opt-in `tests_live/` (live Lakebase checkpoint tests; not in the default test path).
- **Edited files:** `claude.py`, `graph.py`, `controller.py`, `engine.py`, `observability.py`, `workers/structured.py`, `workers/document.py`, `pyproject.toml`, `uv.lock`.

## 16. Failures and unresolved blockers

1. **Resolved in the follow-up:** the dev branch and schema were created, and the live checkpoint tests pass (§10). The test data remains in `wbg_orchestrator_ckpt`; nothing was deleted automatically.
2. **Logfire UI confirmation pending.** The export reported no errors, but I cannot read the project back to confirm arrival.
3. **Planner evidence kinds:** Claude requested `validated_query` evidence for an *executed* task, and *verified* document passages where page checks are mostly unverified. The result was `partial` instead of `answered`. The prompt or the default evidence mapping needs tuning: either derive evidence kinds deterministically from the operation, or tell the planner which kinds each operation yields.
4. **Structured Data worker not validated through Claude.** Live runs used synthetic fixtures. The NL2SQL Claude call and Databricks execution were not run; they still need the read-only Databricks principal (open decision 13).
5. **Document worker partly validated.** Retrieval and the evidence-only answer ran live over the local public `pilot_v2` corpus, with Claude composing the final answer. The RAG pipeline has no Claude generator, and none was used.
6. **No checkpoint encryption at rest yet.** `EncryptedSerializer` is not configured (open decision 6). State is compact and secret-redacted, but evidence summaries are stored in plaintext.
7. **Application-side spend control only.** The ledger is single-process and file-based. A production budget service is needed for multi-instance use.
8. **Only verified price in the code is Sonnet 5.5.** Any other model is refused by the runner.
9. **`structured_agent/tests_mcp` remains unrun** for the reasons above.
10. **Carried over:** the decision on truncating `structured_agent/runs/audit_events.jsonl`. The file was not modified.

## 17. Readiness for the next milestone

**Goal:** conversation integration and an end-to-end procurement investigation.

**Ready:**
- the live Claude planner and response path, with strict contracts, controller validation, HITL, budgets and correlated, scrubbed tracing;
- the official PostgreSQL checkpointer with proven schema isolation, strict deserialization, OAuth-per-connection credentials, restart, recovery and owner-only resume, validated **live on Lakebase `dev-orchestrator`** and offline;
- the conversation front door from Milestone A, unchanged.

**Needed first** (each needs your approval):
1. Done: the Lakebase dev branch and checkpoint schema are live-validated.
2. A read-only Databricks principal and a live NL2SQL budget, so the Structured Data worker runs end to end. This means one Claude call per question plus one Databricks execution.
3. Planner tuning for evidence kinds (§16.3), re-validated offline first, then within a new bounded live budget.
4. Wiring `ConversationFrontDoor` to an engine built with `PostgresSaver`. The conversation store would move to Lakebase only after a separate data-migration approval.
5. A new live budget. This ledger has 1 attempt and $0.964 left under its own ceiling, but the next milestone should start a fresh, separately approved ledger.
