# LangGraph Integration Readiness Assessment

**Date:** 2026-10-26.

**Type:** read-only assessment.

- Nothing was modified, installed, created, connected, called or deployed.
- Evidence comes from the code and the installed package metadata. Every item that needs a live test is marked **[LIVE]**; items that rely on library documentation are marked "verify".

**Approved target:**

- Hierarchical Supervisor + Planner, with a Deterministic Execution Controller.
- The existing Structured Data Agent and Document Intelligence Agent.
- Portkey/Claude.
- Lakebase checkpoints and memory.
- Logfire.
- Pydantic contracts and immutable records.
- Guardrails, circuit breakers and HITL.
- Conversation management and context compression.
- Databricks Apps.

---

## Summary

- **Most of the hard parts already exist and are tested offline:**
  - the intent router and conversation service (owner isolation with PostgreSQL row-level security, compression, token budget);
  - the structured agent (7 tools, validator, gateway, MCP, rate limits, breakers, deadline, NeMo and policy guardrails, Logfire allow-lists);
  - document retrieval with versioned corpora and deterministic answer validation.
- **LangGraph should be added as a thin orchestration layer around them, not as a rewrite.**
- **Five gaps need small adapters before an MVP:**
  1. a Claude (Portkey) generator for the document `AnswerPipeline` (today only local Ollama);
  2. per-request principals for the structured agent (today one deployment principal per process);
  3. importable packaging for `rag/answering` and `rag/retrieval` (today `sys.path` imports);
  4. a LangGraph checkpointer on Lakebase with OAuth-refreshing connections (`langgraph-checkpoint-postgres` and `psycopg_pool` are not installed);
  5. one orchestrator environment, because LangGraph, MCP and NeMo live in different environments today.
- **Not yet proven live:** a successful Claude call through Portkey, Logfire export, and anything running inside Databricks Apps.

---

## 1. Existing component inventory

**Status key:** **Impl** = implemented; **Offline** = tested without external services; **Live** = tested against real services.

| Component | Where | Python interface | Status | Notes |
|---|---|---|---|---|
| Intent router | `conversation/routing/` | `IntentRouter(classifier=None).route(RouteRequest) -> RoutingDecision` (sync, deterministic; frozen Pydantic) | Impl · Offline (`test_intent_router`, `test_routing_isolation_nl2sql`, `test_routing_service`) · no live dependency | `ROUTE_POLICIES` is the only source of permissions. A message cannot pick tools or routes. Classifier plug-in point unused. Routes: conversation, conversation_memory, structured_data, document_rag, ml_prediction, procurement_planning, clarification |
| Conversation service, memory, compression | `conversation/` | `ConversationService(store, runners: dict[Route, AgentRunner], guard, …).handle_message(owner, thread_id, text, execute) -> ConversationReply`. `AgentRunner = (question, conversation_context, execute, request_id) -> Any` | Impl · Offline · **local PostgreSQL** tested (RLS, roles; `test_postgres_store`) · not on Lakebase | Redaction, NeMo input guard, follow-up resolution, `ContextBuilder` (12k budget), versioned summaries with a loss check, staleness, decisions (also meant for HITL). **Only `structured_data` has a runner.** `CheckpointStore` and `LongTermMemoryRetriever` are protocols only |
| Structured Data Agent service | `structured_agent/service.py` | `StructuredDataService` with 7 operations: `search_table_registry`, `get_table_schema`, `get_table_relationships`, `get_metric_definition`, `get_verified_sql_examples`, `validate_sql`, `execute_readonly_sql` (sync; Pydantic contracts with typed `ErrorCode`s) | Impl · Offline (97) · **Live Databricks** (15/15 evaluation scenarios, results equal Silver baselines, dev owner credentials) | Registry in **local PostgreSQL** (`wbg_structured_registry`); hybrid search loads the local gte-base model; JSONL audit file |
| MCP server and transport | `structured_agent/mcp_server.py`, `mcp_transport/`, `nl2sql/tools.py` | FastMCP stdio server with the 7 tools. `McpToolCaller` (stdio, background session) or `InProcessToolCaller(service, principal_id, workspace)`, both `.call(tool, args, request_id) -> dict` | Impl · Offline (41 stdio tests in `.venv-structured-mcp`) · live execution through MCP (report §5) | **One principal per server process** (`WBG_MCP_PRINCIPAL`). No principal in tool arguments. stdio only; no HTTP or SSE, no auth, no TLS |
| NL2SQL workflow | `structured_agent/nl2sql/workflow.py` | `NlToSqlWorkflow(tools, claude, telemetry, settings, tracer, guardrails).run(question, execute=False, request_id, conversation_context) -> WorkflowResult` (sync) | Impl · Offline (MockTransport) · **Claude not yet live** | Rate limits, concurrency slots, LLM and Databricks circuit breakers, 90 s deadline, ≤ 2 LLM calls, ≤ 1 execution, typed `FailureClass`, safe user message. `WorkflowResult` contains **rows** |
| Portkey/Claude client | `structured_agent/nl2sql/llm_client.py`, `live_guard.py`, `settings.py` | `PortkeyClaudeClient(settings, transport=MockTransport \| live=LiveAccess).complete(ClaudeRequest) -> ClaudeResponse` (sync `httpx`; JSON-schema output; no retries; typed `LlmError`) | Impl · Offline · **Live: not successful.** One accidental request on 2026-10-25 was rejected (4xx, `temperature`, since removed); `response_format` json_schema support is **unverified** | Claude-only route without a Portkey Config (Grok fallback off). Dual live opt-in: `--confirm-live-call` + `WBG_ALLOW_LIVE_LLM_CALLS` (never in `.env`) |
| Guardrails | `structured_agent/guardrails/` | `Guardrails.check_input / check_model_output / check_final_response / guard_context / check_execution / tool_gate` -> `GuardDecision` (sync; NeMo `LLMRails.generate` inside) | Impl · Offline · NeMo 0.24.1 **with no LLM** (regex and custom-action rails only); fail closed | Installed only in `.venv-structured-mcp`. NL2SQL-specific output and final-response policies |
| Document retrieval | `rag/retrieval/core.py`, `versions.py` | `build_retriever(version="pilot_v2"\|"pilot_v2_1", strategy="C") -> Retriever`; `.search(q, method, k, guarded, rerank) -> dict` (sync) | Impl · Offline (benchmark, citation, privacy; Step 6E) · local models only | Guard layer (scope, privacy, exact reference). Release binding. gte-base and ms-marco cross-encoder from the local HF cache |
| Answer pipeline | `rag/answering/` | `AnswerPipeline(generator, strategy="C", use_nli, typed_router, retriever).answer(question) -> AnswerResponse` (sync; citations from index metadata; `check_claim` validation) | Impl · Offline · LLM only via **local Ollama** (`llama3.2:1b`) | **No Claude generator.** NLI = `roberta-large-mnli` (local, about 1.4 GB). Not an importable package (`sys.path` imports). The generator contract is `generate(question, items) -> (DraftAnswer, meta)` |
| Logfire | `structured_agent/nl2sql/tracing.py`, `observability.py` | `WorkflowTracer` (local Logfire instance, allow-listed `SAFE_SPAN_ATTRIBUTES`); `Telemetry` (JSONL audit, allow-listed keys) | Impl · Offline · **export never exercised** (`LOGFIRE_SEND_TO_LOGFIRE` off) | No auto-instrumentation; exceptions recorded by class only |
| Databricks connectivity | `silver/common/session.py`, `execution/executor.py` | `DatabricksSparkExecutor(profile)`: Databricks Connect, **serverless**, CLI profile (`auth_type=databricks-cli`, OAuth U2M) | Impl · **Live** (dev owner credential gated by `WBG_ALLOW_DEV_OWNER_CREDENTIALS`) | Needs the local Databricks CLI. **No read-only principal yet** (open blocker) |
| Lakebase | `infrastructure/lakebase_poc/` | `load_settings()`; `connect(settings, token, autocommit)` (single `psycopg` connection); token via `databricks postgres generate-database-credential` (CLI, 60 min) | Impl · Offline (41) · **Live** (pgvector POC; `wbg_rag_poc`, `wbg_rag_pdf_poc`; PG 18.6, pgvector 0.8.6) | No pool, no rotation; `uv` environment with psycopg and pgvector only |
| UC Volumes | `infrastructure/uc_volume_poc/` | POC script | Impl · **Live** (one PDF) | — |
| LangGraph | base Anaconda only | `langgraph` 1.1.10, `langgraph-checkpoint` 3.0.1 (`InMemorySaver`, `InMemoryStore`, `EncryptedSerializer`, `interrupt`, `Command`, `RetryPolicy`, durability modes) | Installed, **unused** | `langgraph-checkpoint-postgres`, `langgraph.store.postgres` and `psycopg_pool` are **not installed** anywhere |
| ML prediction | `ml/` | training code and frozen artifacts; **no runtime prediction API** | Not available | The `ml_prediction` route stays `route_unavailable` |
| Databricks Apps | repository root `databricks.yml` | bundle stub (dev target, no resources) | **Not available** | No `app.yaml`, no app code, no resources |

**Environments (read from installed metadata):**

| Environment | Python | Contents | Constraint |
|---|---|---|---|
| `.venv-structured-mcp` | 3.12.2 | mcp 1.16, fastmcp 2.12.5, anyio 4.11, nemoguardrails 0.24.1, logfire 5.3.0, databricks-sdk 0.157, psycopg 3.3.6; no LangGraph | — |
| base Anaconda | 3.12.2 | langgraph 1.1.10, langchain-core 1.3.2, **anyio 3.7.1** | anyio is too old for the installed `mcp` |
| `lakebase_poc/.venv` | 3.12.12 | psycopg, pgvector | — |

**No existing environment can run LangGraph + MCP + NeMo + Logfire together** (§10, M0).

---

## 2. Reusable interfaces and gaps

### 2.1 Structured Data Agent: reuse as-is behind an adapter

- **Call:** `NlToSqlWorkflow.run(question, execute, request_id, conversation_context)`. This is the same entry the conversation service uses (`structured_runner`).
- **Unchanged:** all guardrails, validator, gateway, breakers and deadline stay inside it.

| Aspect | Current behaviour | Adapter needed |
|---|---|---|
| Sync / async | Sync; internal 90 s deadline; thread-pool timeout around Spark | Run graph nodes **sync** (see 2.4) |
| Input | Question string plus an optional memory block | `AgentTask.question` (standalone question from the resolver) and `context_ref` → rendered memory block |
| Output | `WorkflowResult` with status, failure class, validated / executed SQL, `validation_id`, source objects, metric ids, **rows**, limitations, guardrail decisions | Map to `AgentResult` + `EvidenceRecord(kind="structured_result")`. **Rows stay out of graph state** (request-scoped result cache, §4.4) |
| Tool permissions | Stage allow-lists (`ToolGate`) plus MCP allow-list plus principal authorization | None. The adapter never passes tool names; the plan only names the agent |
| Identity | One principal per workflow or MCP process | **Gap:** build the tool caller per request from the trusted principal (`InProcessToolCaller(service, principal_id, workspace)`), and map the authenticated user to a registry principal (role) |
| Execution | Only when `execute=True` | The controller sets `execute` from the plan **and** policy (HITL gate, §8.3) |
| Errors | Typed `FailureClass` plus `retry_after_seconds`; no exceptions escape `run` | Map to `ExecutionError` (`retryable` only for `rate_limited` / `circuit_open` / `deadline_exceeded`, never for validation or authorization) |
| Timeouts | Per call 45 s (LLM), Databricks timeout, 90 s total | Graph-level deadline in `InvestigationBudget`; pass the remaining time down (gap: `run` has no deadline parameter; small change, §10) |

**Recommendation:** inside the orchestrator, use `InProcessToolCaller` (the same service methods and authorization) with a per-request principal. Keep the MCP stdio server for external MCP clients. A stdio subprocess per user inside an App adds process management without adding isolation.

### 2.2 Document Intelligence Agent: reuse `build_retriever` + `AnswerPipeline`

| Aspect | Current | Gap / adapter |
|---|---|---|
| Call | `AnswerPipeline(generator, retriever=build_retriever("pilot_v2_1")).answer(question) -> AnswerResponse` (sync) | Wrap as `document_agent(task) -> AgentResult` |
| LLM | Only `OllamaGenerator` (localhost), `EvidenceOnlyGenerator`, `TypedRecordAnswerer` | **New `PortkeyClaudeGenerator`** implementing `generate(question, items) -> (DraftAnswer, meta)` with the existing `PortkeyClaudeClient` (JSON schema `DraftAnswer`, live guard, breaker). About 60 lines; no pipeline change |
| Packaging | `sys.path` inserts and `from core import …` | Make `rag/retrieval` and `rag/answering` importable packages, or load them through one adapter module. Test both import styles (Step 6E already handles `versions.py`) |
| Validation | `check_claim` (numbers, dates, refs, quotes, privacy, lexical / NLI), page checks, typed-record route | Reuse as-is. NLI (`roberta-large-mnli`) is a deployment decision (§9) |
| Evidence | `Citation` (unit, document version, pages, page check, excerpt, source URL) | Map to `EvidenceRecord(kind="document_passage")` with corpus release id (`pilot_v2_1`, `release.json` hash) |
| Guards | Retrieval guard: scope, privacy (withheld), exact reference | Reuse; add the NeMo input rail at graph intake (as the conversation service does) |
| Known limits | Table windows lack the caption unit ("US$ million"); evidence-only Q27 artefact | Track (Step 6E §6) |

### 2.3 Conversation management: keep it as the front door

`ConversationService.handle_message` already does the following, all tested:

- trusted ownership;
- redaction and the input guard;
- follow-up resolution and routing;
- the context budget;
- storing turns and metadata;
- compression.

**Recommendation (smallest change):**

- Register the LangGraph application as the runner for the task routes: `structured_data`, `document_rag`, and later a multi-agent `investigation` route.
- Do not rebuild intake inside the graph.

**Gaps:**

1. `_run_task` stores structured-specific metadata; it needs to accept `InvestigationResult`.
2. A *resume* path for HITL (an approval message → `Command(resume=…)`) using the existing `record_decision`.
3. The `AgentRunner` signature needs the trusted `Owner` and thread id. Today it gets only the question, context, execute flag and request id.

### 2.4 Sync vs async

Every existing component is synchronous: `httpx.Client`, psycopg sync, Spark Connect, sentence-transformers. The NeMo wrapper calls the sync `LLMRails.generate`, which manages its own event loop. Inside a running asyncio loop that is a known hazard (verify).

**Recommendation:** build a **synchronous graph** (`graph.invoke`) and execute it per request in a worker thread of the web server. Fan-out uses LangGraph's parallel branches, bounded by the existing concurrency limiters. Async nodes are not needed for the MVP.

### 2.5 Telemetry leak risk to close

- `langsmith` is installed in base Anaconda. LangChain/LangGraph tracing exports **full inputs and outputs** to LangSmith if `LANGSMITH_TRACING` / `LANGCHAIN_TRACING_V2` are ever set.
- The orchestrator must force these off at start-up and fail if they are on, and `check_env` should report them.

---

## 3. Proposed LangGraph node graph

```text
            (ConversationService: redaction → NeMo input rail → follow-up resolution → IntentRouter → context build)
                                                   │ task route
                                                   ▼
START → intake ─► plan ─► review_plan ─┬─(needs approval)─► approval_gate ⟲ interrupt / resume
                                       │                         │ approved / rejected
                                       ▼                         ▼
                                    dispatch ◄──────────── (rejected → finalize "not run")
                                       │  ready tasks (dependency order, ≤ max_parallel)
                        ┌──────────────┼───────────────┐
                        ▼              ▼               ▼
              structured_agent   document_agent   (ml_agent: unavailable → ExecutionError)
                        └──────────────┼───────────────┘
                                       ▼
                                    collect ─► controller ─┬─► dispatch      (more ready tasks)
                                                           ├─► replan        (≤ 1, only on typed recoverable gaps)
                                                           ├─► approval_gate (task requests execution or budget overrun)
                                                           └─► synthesize
                                                                   ▼
                                                           validate_answer ─► finalize ─► END
                     any node ── unrecoverable ExecutionError ──► fail_safe ─► finalize
```

| Node | Kind | Responsibility |
|---|---|---|
| `intake` | deterministic | Validate `InvestigationRequest`; owner, principal and authorization scope **from the trusted context only**; initialise budget, deadline and ids; LangSmith-off check |
| `plan` | deterministic, with an optional LLM | Single-route requests: one `AgentTask` from the router decision (no LLM). Multi-agent investigations: Claude proposes an `InvestigationPlan` (structured output), then it is validated |
| `review_plan` | deterministic | Plan ⊆ route policies and the principal's scope; task count and depth limits; budget forecast; decide whether approval is required |
| `approval_gate` | HITL | `interrupt(ApprovalRequest)`; resume only with an `ApprovalDecision` from the same owner before expiry |
| `dispatch` | deterministic | Choose ready tasks (dependencies met, budget left, breaker closed); fan out |
| `structured_agent`, `document_agent` | adapters | Call the existing agents; return `AgentResult` + `EvidenceRecord`s; never raise into the graph |
| `collect` | deterministic | Append results and evidence (reducers); charge the budget with actual usage |
| `controller` | deterministic | State machine, §5 |
| `synthesize` | LLM | Claude composes the answer **only** from `EvidenceRecord` summaries; structured claims → evidence ids |
| `validate_answer` | deterministic | `check_claim`-style checks per claim (numbers, dates, refs, citations exist and belong to this investigation); NeMo final-response rail; remove unsupported claims → `partial` |
| `finalize` | deterministic | `InvestigationResult`; conversation turn metadata; audit and trace |
| `fail_safe` | deterministic | Safe message from the typed error; no partial claims without evidence |

---

## 4. Data contracts and state management (design)

### 4.1 Conventions

- **Pydantic v2 records:** `ConfigDict(frozen=True, extra="forbid")`, `schema_version: Literal["1"]`, ULID or UUIDv7 ids, UTC timestamps.
- **Content hash:** `record_sha256` of the canonical JSON excluding itself. A record is never edited; a change is a new record that `supersedes` the old id.
- **Graph state is JSON-only:** contracts are validated at node boundaries (`Model.model_validate(dict)` in, `model_dump(mode="json")` out). Checkpoints therefore need no custom deserialisation, and `JsonPlusSerializer` keeps `pickle_fallback=False`. The alternative is the serializer's `allowed_json_modules` for one contracts module.
- **What state never holds:** credentials, raw result rows, document excerpts beyond the cited excerpt, prompts, or unredacted user text.

### 4.2 Models (sketch)

```python
class AuthorizationScope(Record):            # from the trusted identity, never from text or the LLM
    tenant_id: str; user_id_hash: str
    principal_id: str                         # registry principal (roles) for the structured agent
    routes: frozenset[Route]                  # ⊆ ROUTE_POLICIES of the routing decision
    rag_release: Literal["pilot_v2", "pilot_v2_1"]
    may_execute_sql: bool                     # policy; execution still needs the HITL gate when required

class InvestigationRequest(Record):
    request_id: str; thread_id: str; turn_seq: int
    question: str                             # standalone, already redacted (conversation service)
    context_ref: str | None                   # id of the rendered memory block (not the text)
    routing: RoutingDecision                  # existing frozen model
    scope: AuthorizationScope
    budget: InvestigationBudget               # llm_calls, input/output tokens, cost_usd, executions, deadline_s

class AgentTask(Record):
    task_id: str; investigation_id: str
    agent: Literal["structured", "document"]
    question: str
    depends_on: tuple[str, ...] = ()
    execute: bool = False                     # structured only; decided by policy and HITL, never by the LLM
    max_attempts: int = 1                     # infrastructure retries only
    idempotency_key: str                      # sha256(investigation_id, agent, question, execute)

class InvestigationPlan(Record):
    plan_id: str; version: int; supersedes: str | None
    tasks: tuple[AgentTask, ...]              # ≤ 6, depth ≤ 2
    rationale: str                            # model text: untrusted, never shown as fact
    produced_by: Literal["router_template", "claude_planner"]
    requires_approval: bool; approval_reasons: tuple[str, ...]

class EvidenceRecord(Record):
    evidence_id: str; task_id: str
    kind: Literal["structured_result", "document_passage", "typed_plan_record"]
    # provenance (one block per kind):
    validation_id: str | None; sql_sha256: str | None; source_objects: tuple[str, ...] = ()
    data_version: str | None                  # e.g. Delta table version / registry build id
    row_count: int | None; result_cache_key: str | None   # rows live outside state (§4.4)
    unit_id: str | None; document_id: str | None; document_version_id: str | None
    pages: tuple[int, int] | None; page_check: str | None
    corpus_release: str | None; release_sha256: str | None
    summary: str                              # bounded, verified facts only (≤ 1,500 chars)
    verified: bool                            # by validator / gateway / page check, not by an LLM

class AgentResult(Record):
    task_id: str; attempt: int
    status: Literal["succeeded", "partial", "insufficient_evidence", "withheld", "failed", "blocked"]
    evidence_ids: tuple[str, ...]; error: ExecutionError | None
    usage: Usage                              # llm_calls, tokens, executions, duration_ms

class ExecutionError(Record):
    error_id: str; task_id: str | None; node: str
    category: Literal["validation", "authorization", "guardrail", "budget", "deadline", "rate_limited",
                      "circuit_open", "provider", "execution", "internal"]
    code: str                                 # existing FailureClass / ErrorCode values
    retryable: bool; retry_after_s: float | None
    safe_message: str                         # never exception text

class ApprovalRequest(Record):
    approval_id: str; investigation_id: str; kind: Literal["execute_sql", "plan", "budget_overrun", "restricted_scope"]
    summary: str                              # what will run: tables, metric ids, row limit (no SQL parameters)
    payload_sha256: str                       # hash of exactly what is being approved
    requested_for: str                        # user_id_hash; only this owner may answer
    expires_at: datetime

class ApprovalDecision(Record):
    approval_id: str; payload_sha256: str; approved: bool; decided_by: str; reason: str | None
```

### 4.3 State and reducers

```python
class InvestigationState(TypedDict):
    request: dict                                                 # InvestigationRequest (set once)
    plan: dict | None                                             # latest InvestigationPlan
    plans: Annotated[list[dict], append_unique("plan_id")]        # history (immutable)
    task_status: Annotated[dict[str, str], merge_monotonic]       # pending → running → done/failed (never backwards)
    results: Annotated[list[dict], append_unique("task_id","attempt")]
    evidence: Annotated[list[dict], append_unique("evidence_id")]
    errors: Annotated[list[dict], append_unique("error_id")]
    approvals: Annotated[list[dict], append_unique("approval_id")]
    budget_used: Annotated[dict, add_counters]
    answer: dict | None                                           # validated InvestigationResult
```

- **Append-only reducers:** they reject duplicates with different hashes, so parallel branches cannot overwrite each other.
- **`merge_monotonic`:** enforces the task-status order.
- **No reducer deletes:** corrections are new records.

### 4.4 Large and sensitive data

Structured result rows are needed by `synthesize`, but must not be checkpointed.

**MVP:** keep them in a **request-scoped, in-process result cache**, keyed by `result_cache_key`, with a TTL equal to the deadline. On resume after a restart:

- the evidence is still referenced;
- rows are re-obtained by re-running the **same validated SQL**, with the same validation id and an approval check;
- or the investigation degrades to the evidence summary.

The alternative is persisting bounded previews (≤ 50 rows, allow-listed columns); that is an architecture decision (§11).

---

## 5. Planner and execution-controller responsibilities

**Planner** (the only LLM decision about the work):

- **Single-intent requests (the default):** no LLM. One task from the router decision. Most turns stay deterministic and cheap.
- **Multi-agent investigations** (for example "compare the planned vs actual award dates for Ethiopia roads and explain delays from the ISRs"):
  - Claude returns an `InvestigationPlan` (JSON schema): agents, questions, dependencies.
  - The planner cannot set `execute`, principals, tools, budgets or routes beyond the router decision.
  - At most one *replan*, triggered only by typed gaps (`insufficient_evidence`, `clarification_needed`), never by free text.

**Execution controller** (deterministic; the only node that changes control flow):

1. **Validates** every plan against `AuthorizationScope` and `ROUTE_POLICIES`, the task limit (6), depth (2) and the budget forecast.
2. **Orders** tasks by `depends_on`; dispatches ready tasks up to `max_parallel` (2), using the existing concurrency limiters.
3. **Enforces budgets** before every LLM call or execution (`InvestigationBudget`) and charges actual usage afterwards.
4. **Checks circuit breakers** before dispatch. When open, the task fails with `circuit_open` and `retry_after_s`; it is not queued.
5. **Retries:** only `retryable` errors, at most `max_attempts`, with the same idempotency key. **No LLM retries** (the existing policy), and Databricks execution at most once per approval.
6. **HITL triggers** (§8.3) → `approval_gate`.
7. **Terminal conditions:** all tasks finished → synthesize; deadline, budget or unrecoverable error → `fail_safe` with partial evidence reported as such.
8. **Records** every transition as an audit and trace event (ids, statuses, counts only).

---

## 6. Lakebase checkpoint compatibility

| Topic | Assessment |
|---|---|
| Library | `langgraph-checkpoint-postgres` (`PostgresSaver`) and `langgraph.store.postgres` (`PostgresStore`) need **`psycopg` 3** (3.3.6 installed everywhere) **and `psycopg_pool`** (not installed). The version must match `langgraph-checkpoint` 3.0.x and `langgraph` 1.1.x (resolve in M0) |
| PostgreSQL 18 | The library uses standard features (JSONB, BYTEA, upserts, `CREATE INDEX CONCURRENTLY` in migrations). No PG-18 blocker is known, but none is proven. **[LIVE]** Lakebase PG 18.6 is confirmed |
| Connection requirements (library docs; verify) | `autocommit=True`, `row_factory=dict_row`, and `prepare_threshold=0` with a pool. `setup()` runs migrations outside a transaction |
| Schema isolation | The tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`; store tables) are created **unqualified**. Put them in a dedicated schema with the connection option `options="-c search_path=wbg_orchestrator"` for both setup and runtime. Keep this apart from `wbg_rag_poc`, `wbg_rag_pdf_poc`, `conversation` and `registry` |
| Roles | **Migration role** (CREATE on `wbg_orchestrator`; runs `setup()` once per version, like `init_store`). **Runtime role** (SELECT / INSERT / UPDATE / DELETE on those tables only). The runtime role never runs `setup()` |
| OAuth refresh | A Lakebase credential lasts 60 minutes and is checked **only at login**; open connections survive expiry. Design: pool with `max_lifetime` 45 min; each **new** physical connection gets a fresh token. Locally the token comes from the CLI (existing `credentials.py`). In Apps it comes from the app service principal via `WorkspaceClient().postgres.generate_database_credential(endpoint, ttl=…)` (SDK 0.157 confirmed). Implement through a connection subclass or a dynamic-kwargs hook of the pinned `psycopg_pool` version (verify which) |
| Credential rotation | No static password anywhere. Rotation means token expiry plus pool recycling. App service principal secrets are managed by Databricks Apps |
| Transactions | The checkpointer uses **autocommit** connections. The conversation store needs **transactional** connections (RLS `set_config` per transaction). → **Two separate pools.** Step 4 lesson: never let an implicit read transaction turn a `transaction()` block into a savepoint |
| Isolation of checkpoint rows | Checkpoint tables have no owner columns, so RLS cannot apply. Opaque thread ids (`sha256(tenant, user, conversation_thread, investigation)`); the app checks the owner against the conversation store **before** loading or resuming; only the backend's runtime role can reach the schema |
| At-rest protection | Optionally `EncryptedSerializer` (available in the installed `langgraph-checkpoint`), with an AES key from a Databricks secret. State is already free of rows and credentials (§4.1) |
| Retention | Checkpoints are execution state, not memory. Delete per thread after completion plus N days (proposed 30, aligned with summary expiry) using the saver's `delete_thread` in a scheduled job |
| `PostgresStore` (long-term memory) | Not needed for the MVP. The conversation store already holds owner-isolated turns, decisions and summaries with RLS. Implement the existing `LongTermMemoryRetriever` protocol over it later; adopt `PostgresStore` (pgvector available) only if semantic cross-thread memory is approved |

### Proposed isolated live checkpoint and resume test (for later approval)

**Setup:**

- **Target:** a new **Lakebase branch** (for example `lg-checkpoint-test`) of `wbg-procurement-dev`, or, if branches are not approved, a new schema `wbg_lg_checkpoint_test` on the production branch.
- **Never touched:** existing schemas.
- **Roles:** a migration role and a runtime role (no superuser).

**Steps:**

1. `setup()` with the migration role through `search_path`. Verify the tables exist only in the test schema.
2. Run a toy graph (3 nodes + `interrupt`) with the runtime role. Then stop the process.
3. Start a new process with a **freshly generated token**. Resume the thread with `Command(resume=…)` and verify state, task status and the single execution of a side-effect node (idempotency).
4. **Token-expiry test:** a credential with a short TTL plus `max_lifetime` recycling. Verify reconnection without errors.
5. **Concurrency:** 2 threads × 20 checkpoints, with no lost writes.
6. **Isolation:** the runtime role cannot read other schemas, and the wrong owner cannot resume (app check).
7. `delete_thread` → rows removed. Drop the schema or delete the branch. Run the cleanup check.

**Acceptance:** every step passes. No credentials appear in logs, and nothing is written outside the test schema.

---

## 7. Memory and context-compression design

| Layer | Store | Content | Lifetime |
|---|---|---|---|
| Conversation memory (existing) | `conversation` schema (move from local PostgreSQL to Lakebase) | Redacted turns, metadata, decisions, versioned summaries | Durable; summaries expire after 30 days |
| Execution state (new) | `wbg_orchestrator` checkpoints | Investigation state (§4.3) | Until completion + 30 days |
| Request-scoped data (new) | Process memory | Result rows, rendered prompts | Request deadline |

**Context per LLM call:**

- **Planner:** the standalone question plus the router decision plus a short memory block (the existing `ContextBuilder`, 3k tokens).
- **Synthesizer:** **only** `EvidenceRecord.summary` fields, plus bounded result previews and cited excerpts, in a fixed order. Budget: 6k tokens for tool results (the existing `ContextBudget` split).
- **Overflow:** evidence is ranked by task order and `verified`, then truncated **by whole record**, and the omission is reported. It is never truncated silently.

**Compression:**

- Reuse the `Compressor`: deterministic, with a loss check for project ids, evidence ids and citations.
- After an investigation, the assistant turn stores the `InvestigationResult` metadata (evidence ids, validation ids, corpus release). Later summaries can then reference evidence without copying content.

**Isolation:** owner from the trusted identity; RLS on the conversation store; owner check before resume. Memory blocks are screened for instruction-like text, both in the builder and in the workflow, as today.

---

## 8. Logfire, guardrails, HITL and circuit breakers

### 8.1 Tracing (Logfire / OpenTelemetry)

- **Spans:** one root span per investigation (`investigation.run`) and one child span per node (`node.<name>`).
  - The existing `nl2sql.*` spans nest under `structured_agent`.
  - New `rag.retrieve`, `rag.generate` and `rag.validate` spans nest under `document_agent`.
- **Allow-listed attributes only**, extending `SAFE_SPAN_ATTRIBUTES`:
  - ids: investigation, task, evidence, approval, validation;
  - node, agent, status, failure class and error code;
  - counts: tasks, evidence, rows, claims kept or removed;
  - tokens and estimated cost, durations, breaker state;
  - corpus release id, route, user id hash, tenant id.
- **Never recorded:** question text, prompts, model output, SQL text and parameters, rows, excerpts, e-mails, tokens or keys, exception messages (class name only).
- **No auto-instrumentation** (no `instrument_httpx`, `instrument_psycopg` or LangChain callbacks). LangSmith is forced off (§2.5).
- **Export:** stays off until approved (`LOGFIRE_SEND_TO_LOGFIRE`). In Apps the token comes from a secret resource. The JSONL audit file needs a durable sink in Apps (§9).
- **Tests:** a span-exporter test that asserts no attribute outside the allow-list, and no known secret, question text or SQL, across a scripted run.

### 8.2 Guardrails and deterministic evidence validation

| Point | Control |
|---|---|
| Intake | Redaction, NeMo input rail, router policies (existing) |
| Structured agent | All six existing stages, unchanged |
| Document agent | Retrieval guard (scope, privacy, exact reference); page checks; quarantine of instruction-like evidence |
| Planner output | JSON schema; agents and questions only; no `execute`, tools, principals or routes; question text screened for instruction-like content |
| Synthesizer output | NeMo output regex rail (secrets) plus claim validation: every claim cites ≥ 1 evidence id of **this** investigation; numbers, dates and references must appear in the cited evidence (the existing `check_claim` logic); privacy patterns; unsupported claims removed → `partial`; no claim may state that SQL ran unless an `EvidenceRecord(kind=structured_result)` with an executed validation id exists |
| Final response | Existing final-response policy, generalised; the answer status must be consistent with the evidence statuses |

### 8.3 HITL (interrupt and resume)

**Triggers** (from policy, not the LLM):

- a structured task with `execute=True` when the principal or credential mode requires approval (dev owner credentials always require it);
- an LLM-planned investigation;
- a budget forecast over the soft limit;
- a request touching a restricted scope.

**Flow:**

1. `approval_gate` emits `ApprovalRequest`, with a `payload_sha256` of exactly what will run.
2. The checkpoint is saved (`durability="sync"` at this node).
3. The conversation service returns `awaiting_approval`, and the request is also recorded as a `Decision`.
4. Resume only through `Command(resume=ApprovalDecision)`, and only when all of these hold: same owner; hash matches; not expired; not already used.
5. **Rejection:** finalize with "not run". **Expiry:** an `ExecutionError(category="budget")`.

### 8.4 Circuit breakers, retries and budgets

- **Breakers:** reuse `resilience.CircuitBreaker`, `TokenBucketRateLimiter`, `ConcurrencyLimiter` and `Deadline`. One breaker each for Portkey (planner, generator, synthesizer), Databricks and Lakebase. They are in-memory per process, which is valid for one app instance (§11).
- **Retries:** LangGraph `RetryPolicy` only on adapter nodes, and only for typed transient infrastructure errors (connection reset, Lakebase reconnect). Never on LLM responses, validation or authorization; `max_attempts` 2.
- **Budgets:** an `InvestigationBudget` per request, with these defaults to tune:

  | Budget | Default |
  |---|---|
  | LLM calls | ≤ 6 |
  | Input tokens | ≤ 60k |
  | Output tokens | ≤ 8k |
  | Estimated cost | ≤ US$0.50 |
  | Databricks executions | ≤ 3 |
  | Deadline | 120 s |

  Usage comes from Portkey responses; prices from a versioned config. Exhaustion is a controlled stop and is never retried.

---

## 9. Databricks Apps deployment considerations

| Concern | Today | In Databricks Apps | Live test needed |
|---|---|---|---|
| Runtime | Python 3.12 locally | Apps run Python from `app.yaml` plus `requirements.txt`; confirm the runtime Python version and a FastAPI/uvicorn server | **[LIVE]** |
| Identity | None per user; deployment principal | App service principal (OAuth M2M, injected). User identity from the forwarded-identity headers → `Owner`. Optional on-behalf-of-user token for UC-enforced access (decision) | **[LIVE]** |
| Lakebase | CLI token, single connection | `postgres` app resource (SDK `AppResourcePostgres`); token via `w.postgres.generate_database_credential`; pools as in §6 | **[LIVE]** |
| Databricks SQL | Databricks Connect serverless with the CLI profile (needs the CLI) | Either Databricks Connect with SDK default (M2M) auth, or a SQL warehouse (`sql_warehouse` resource; Statement Execution API or `databricks-sql-connector`). Both need a `SqlExecutor` implementation; the gateway and validator are unchanged | **[LIVE]** |
| Read-only principal | Not created (blocker) | Grant the app service principal SELECT on `wbg_silver` / `wbg_gold` only. Then `check_credential` reports `read_only` and no dev flag is needed | **[LIVE]** |
| UC Volumes | CLI / POC | `uc_securable` resource (READ VOLUME) plus the SDK Files API | **[LIVE]** |
| Registry and conversation DBs | **Local PostgreSQL** | Move the `registry` and `conversation` schemas to Lakebase. Roles and RLS carry over; PG 18 RLS is to be verified | **[LIVE]** |
| Models | Local HF cache: gte-base (about 0.5 GB), cross-encoder, roberta-large-mnli (about 1.4 GB); torch | App image size, memory and start-up time are the risks. Options: load from a UC Volume at start-up (hash-checked), Model Serving endpoints, or drop NLI | **[LIVE]** |
| RAG artefacts | Local files (`pilot_v2_1` 20 MB, release store 18 MB) | Ship through a UC Volume and verify against `release.json` hashes at start-up (the existing release check) | **[LIVE]** |
| LLM egress | Portkey (`api.portkey.ai`) | Portkey key from a `secret` resource; outbound access from Apps must be allowed by workspace network policy | **[LIVE]** |
| MCP | stdio subprocess | In-process tool caller (§2.1); expose MCP over HTTP only if external clients need it (auth required) | — |
| Audit trail | Local JSONL | Ephemeral in Apps: write to a Lakebase `audit` table or a UC Volume or Delta | **[LIVE]** |
| Scaling | In-memory limiters and breakers | Fine for a single instance. Multiple instances need shared limits (Lakebase or Redis-like) | — |
| Ollama | Local LLM for RAG | Not available. Replaced by the Claude generator (§2.2) | — |

---

## 10. Recommended milestones and acceptance tests (smallest maintainable path)

| # | Milestone | Scope | Acceptance tests |
|---|---|---|---|
| **M0** | Orchestrator environment and contracts (offline) | New `orchestrator/` package with its own `uv` project (Python 3.12) pinning langgraph 1.1.x, langgraph-checkpoint-postgres, psycopg[pool], mcp 1.16, fastmcp, nemoguardrails 0.24.1, logfire, sentence-transformers. Contracts (§4) | Lock resolves (**needs PyPI access: approval**); contracts round-trip JSON and reject mutation, extra fields and duplicates; LangSmith-off check; `check_env` reports tracing variables |
| **M1** | Offline graph MVP | Graph (§3) with `InMemorySaver`; structured adapter (`InProcessToolCaller`, Claude via `MockTransport`, no execution); document adapter (`build_retriever("pilot_v2_1")`, EvidenceOnly and mock Claude generator); deterministic single-route planner; controller; validate; finalize | Routing parity with `IntentRouter`; permissions cannot be widened by plan or text; budget, deadline and breaker stops; interrupt/resume with `InMemorySaver` (approve, reject, wrong owner, hash mismatch, expired); idempotent re-dispatch; claim validation removes unsupported claims; telemetry allow-list test; **all existing suites still pass** |
| **M2** | Claude generator and planner via Portkey | `PortkeyClaudeGenerator` for `AnswerPipeline`; Claude planner and synthesizer with JSON schema | Offline mock tests. Then **first approved live smoke** (dual opt-in; ≤ 10 calls): JSON-schema support confirmed, usage and cost recorded, no content in traces |
| **M3** | Lakebase checkpointer | Pools with token refresh (CLI locally); `PostgresSaver` in `wbg_orchestrator` | The **isolated live test of §6** (approval required) |
| **M4** | Conversation integration and HITL | Register the graph as the task-route runner; resume path via `record_decision`; move the `conversation` schema to Lakebase | Five-turn scenario through the graph; HITL round-trip across a process restart; RLS isolation on Lakebase **[LIVE]** |
| **M5** | Controls hardening | Cost table, retention job, durable audit sink, Logfire export (approved destination) | Exported spans contain only allow-listed attributes **[LIVE]**; retention deletes expired threads |
| **M6** | Databricks Apps dev deployment (approval) | `app.yaml`, resources (postgres, sql_warehouse, uc_securable, secrets), read-only service principal grants, `SqlExecutor` for Apps, artefact and model loading from a UC Volume | Smoke: one structured question (validated and executed read-only), one document question with citations, one HITL resume, telemetry check, start-up integrity check of the RAG release |

---

## 11. Unresolved architecture decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | Front door | Keep `ConversationService` as the entry point; the graph is the task-route runner (§2.3). The alternative is moving intake into the graph |
| 2 | Planner scope | Deterministic for single-intent requests; Claude only for multi-agent investigations |
| 3 | Structured tool transport inside the app | In-process tool caller (MCP stdio only for external clients) |
| 4 | Result rows across interrupts | Request-scoped cache plus re-run on resume, vs persisted bounded previews |
| 5 | LangGraph `PostgresStore` | Defer; use the conversation store for memory |
| 6 | Checkpoint encryption and retention | `EncryptedSerializer` with a secret-held key; 30 days |
| 7 | User identity and data authorization | App service principal plus registry roles, vs on-behalf-of-user tokens (UC-enforced); the principal mapping |
| 8 | Databricks execution path in Apps | Databricks Connect serverless (M2M) vs SQL warehouse |
| 9 | Lakebase layout | One project; schemas per component (`registry`, `conversation`, `wbg_orchestrator`, `wbg_rag_*`), and branches per environment (dev, test) |
| 10 | Models in Apps | UC Volume loading vs Model Serving; keep or drop NLI (`roberta-large-mnli`) |
| 11 | Telemetry export | Logfire SaaS vs OTLP to an approved collector; data-residency review |
| 12 | LLM fallback | Stay Claude-only until Grok is verified (existing decision) |
| 13 | Read-only Databricks principal | Still open: required before any non-development execution |
| 14 | Durable audit sink | Lakebase table vs Delta / UC Volume |
| 15 | Multi-instance scaling | Single instance for the MVP; shared limiter store later |
| 16 | ML prediction route | No runtime API: keep `route_unavailable` |
| 17 | HITL policy | Which actions always require approval (execution with dev credentials, LLM plans, budget overruns, restricted scope) |

**Pending side item from Step 6E:** decide whether to restore `structured_agent/runs/audit_events.jsonl` to its prior 226,591 bytes.
