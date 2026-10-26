# LangGraph Milestone A — Offline Supervisor–Planner–Executor Engine: Validation Report

**Date:** 2026-10-26 · **Scope:** offline implementation and tests only · **Basis:** `docs/reports/LANGGRAPH_INTEGRATION_READINESS.md`

## Summary

- A LangGraph engine (`orchestrator/wbg_orchestrator`, 24 source files) now runs the existing Structured Data Agent and
  Document Intelligence Agent behind a supervisor, a planning agent, and a deterministic execution controller, with an
  append-only evidence ledger, evidence verification, validated response generation, HITL approval, and conversation
  and memory interfaces.
- The engine has its own uv project (`orchestrator/pyproject.toml` + `uv.lock`, Python 3.12.12, 181 locked packages).
  No existing environment was changed.
- **Orchestrator suite: 82 passed, 0 failed** (12.7 s). All 17 acceptance items are covered. Item 17 (existing
  regressions) passes on every offline suite. `structured_agent/tests_mcp` was **not run**, because it opens live
  Databricks sessions (see §10).
- No Portkey or Claude call, no Databricks or Lakebase connection, no external trace export, and no write to the
  persistent audit log. The audit log's SHA-256 is identical before and after: `d34868ea…2bb2680`.
- Inventory diff against the pre-milestone snapshot: **0 files removed, 0 changed.** All 31,226 added files are under
  `orchestrator/`: 31,189 in `.venv`, plus 37 source, test and lock files.

---

## 1. Implemented graph architecture

```
conversation front door (existing ConversationService, unchanged)
        │  owner, thread, redaction, input guard, routing
        ▼
ConversationRunner / ConversationFrontDoor  ──►  InvestigationEngine (LangGraph StateGraph + checkpointer)
                                                      │
   intake ─► supervise ─┬─(fast)──────────────► validate_plan ─► request_approval ─┬─► await_approval (interrupt)
                        └─(plan)─► plan ───────►                                   │        │ approved / rejected
                                                                                   ▼        ▼
                                          dispatch ◄──── execute ◄──── dispatch ◄──┘     reject
                                             │ (no ready task)
                                             ▼
                                           verify ─► respond / plan (bounded replan) ─► finalize ─► END
```

- **The graph decides; the controller acts.** Every worker call goes through `ExecutionController.execute`. The
  controller issues a per-call HMAC ticket, and workers refuse any call without a valid ticket.
- **State holds only compact, JSON-safe data:** IDs, record dicts, task states and counters. Result rows stay in a
  request-scoped `ResultCache` and are never checkpointed. Memory text is supplied per request (`EngineDeps.memory`) and
  never enters state.
- **Checkpointing** uses `InMemorySaver` with `durability="sync"`. The checkpoint thread ID is
  `stable_id("lgt", tenant, user_hash, conversation_thread, investigation)`, so every lookup is owner-scoped.

## 2. Actual node and edge definitions (`graph.py`, `build_graph`)

**Nodes (13):** `intake`, `supervise`, `plan`, `validate_plan`, `request_approval`, `await_approval`, `dispatch`,
`execute`, `verify`, `respond`, `fail`, `reject`, `finalize`.

| From | Edge | To |
|---|---|---|
| START | fixed | intake |
| intake | phase `done` (guard blocked or budget refused) / else | finalize / supervise |
| supervise | phase `planned` (fast path) / `needs_plan` / `done` (clarify or refuse) | validate_plan / plan / finalize |
| plan | `after_plan`: `planned` / `plan_failed` with a replan left / else | validate_plan / plan / fail |
| validate_plan | `after_validation`: valid / invalid LLM plan with a replan left / else | request_approval / plan / fail |
| request_approval | phase `awaiting_approval` / else | await_approval / dispatch |
| await_approval | `interrupt()` then phase `approved` / else | dispatch / reject |
| dispatch | phase `executing` (a ready batch exists) / else | execute / verify |
| execute | fixed | dispatch |
| verify | `after_verify`: a required task failed with `NO_EVIDENCE` and a replan is left / else | plan / respond |
| respond, fail, reject | fixed | finalize |
| finalize | fixed | END |

**State (`state.py`, `InvestigationState` TypedDict) and its reducers:**

- `results`, `evidence`, `errors`, `plans`, `plan_errors`, `approvals` and `decisions` use the append-only
  `append_records`. Resending an identical record is a no-op; changing a stored record raises `ImmutableRecordError`.
- `task_states` uses `merge_task_states`, which enforces forward-only transitions and requires every task to start
  pending. Anything else raises `IllegalTransitionError`.
- `usage` uses `add_counters`. Cost is summed as a Decimal string.
- All other keys use `last_value`.

## 3. Reused existing components (imported, not modified)

| Component | Used for |
|---|---|
| `conversation.routing.rules` (`normalize`, `is_tool_directive`, `task_signals`), `ROUTE_POLICIES` | Supervisor routing; route-based capabilities |
| `ConversationService`, `InMemoryConversationStore`, `ContextBudget`, `ApproxTokenCounter`, `redact_secrets`, `MemoryHit` | Front door, thread ownership, memory view, budgets |
| `NlToSqlWorkflow.run(...) -> WorkflowResult`, `InProcessToolCaller.call(...)` | Structured worker: `answer_data_question`, `describe_metric`, `find_tables` |
| `PortkeyClaudeClient`, `ClaudeRequest`, `LlmError` | Claude planner and responder adapters (`claude.py`) |
| `CircuitBreaker`, `BreakerSettings`, `CircuitOpenError` (`structured_agent.nl2sql.resilience`) | Per-worker and LLM circuit breakers |
| `build_guardrails()` (NeMo, input rails only), `evaluate_user_input`, `contains_instruction_like_text` | Input guard; sanitising upstream evidence passed to workers |
| `AnswerPipeline`, `EvidenceOnlyGenerator`, `schemas.AnswerResponse` / `Citation`, `validate.numbers` / `validate.dates` | Document worker; claim validation |
| `rag.retrieval.versions.build_retriever` / `VERSIONS` | Document retrieval. The default stays `pilot_v2`; `pilot_v2_1` is an explicit option |
| `structured_agent.tests.nl2sql_fakes`, `registry_available()` | Offline Claude and Databricks fakes in tests |

## 4. New adapters and contracts

**Contracts (`contracts.py`).** All records are Pydantic models with `frozen=True` and `extra="forbid"`, use
`schema_version: "1"`, and get stable or deterministic IDs. Helpers are `to_state()`, `from_state()` and
`record_sha256()`.

- **Requests and plans:**
  - `InvestigationRequest`: the question is limited to 2,000 characters; the owner is an `OwnerRef` hash, not a user
    ID; it carries `AppAuthorizationContext` and `InvestigationBudget`.
  - `InvestigationPlan`: tasks, a `CompletionCondition`, and `produced_by` (`fast_path` / `fake_planner` /
    `claude_planner`).
  - `AgentTask`: the worker, an operation from the allow-list, `depends_on`, `EvidenceRequirement`s,
    `requires_execution`, `optional`, a deadline, `max_attempts` and an `idempotency_key`.
- **Results and evidence:**
  - `AgentResult`: terminal states only.
  - `EvidenceRecord`: ID, source system, project, document and table IDs, source objects, source version, citation,
    provenance pairs, `validation_status`, `numeric_use`, `quality_warnings` and the producing task.
  - `ExecutionError`: category, code and a safe message.
- **Approvals and responses:** `ApprovalRequest`, `ApprovalDecision`, `ResponseClaim` and `InvestigationResponse`.

**Authorization (`authorization.py`).**

- `AppAuthorizationContext` covers:
  - one shared, restricted application identity (`wbg-procurement-app`);
  - `PublicDataScope`: the Gold and Silver schemas, approved RAG releases and `writes_allowed: False`;
  - route-derived capabilities;
  - `user_scoped_data_access: False`.
- `OPERATIONS` is the allow-list:
  - structured: `answer_data_question`, `describe_metric`, `find_tables`;
  - document: `answer_document_question`, `retrieve_passages`.
- Execution additionally requires `databricks_execution`.

**Adapters.**

- **`workers/structured.py`** maps `WorkflowResult` to evidence:
  - `ANSWERED` → `STRUCTURED_RESULT`, with `APPROVED` numeric use. Facts come from at most 25 cells. Provenance
    records the validation ID, SQL and parameter hashes, the result reference, the row count and gateway flags.
  - `VALIDATED_NOT_EXECUTED` → `VALIDATED_QUERY`, with `NOT_APPROVED` numeric use.
  - Clarification, `CANNOT_ANSWER` and guardrail blocks map to their own categories.
- **`workers/document.py`** maps citations to evidence:
  - `DOCUMENT_TABLE` and `TYPED_PLAN_RECORD` records are `NOT_APPROVED`, warned `pdf_table_value_unverified` or
    `pdf_extracted_typed_record`, and carry extraction flags from `shared/table_rows.jsonl`.
  - Narrative text with a verified page check becomes `DOCUMENT_STATEMENT`. Unverified pages are warned
    `page_not_checked`.
  - Excerpts are capped at 600 characters, and `source_version` is `rag:<release>`.
- **`claude.py`:** `PortkeyJsonClient`, a JSON-schema response format over the existing `PortkeyClaudeClient`.
- **`planner.py`:** `FakePlanner`, `ClaudePlanner`, and `fast_path_plan()`.
- **`response.py`:** `DeterministicResponseGenerator`, `ClaudeResponseGenerator`, and `validate_response`.

## 5. Environment and dependencies

- **Location:** `orchestrator/` (uv project with `package = false`). Python 3.12.12, 181 locked packages, `.venv`
  926 MB.
- **Run with:** `env -u VIRTUAL_ENV uv run --locked python -B -m pytest tests/`
- **Pins:**
  - `langgraph==1.1.10`, with `langgraph-checkpoint` 4.3.0 as a transitive dependency.
  - The rest match `.venv-structured-mcp`, so the existing agents import unchanged: `mcp==1.16.0`,
    `fastmcp==2.12.5`, `anyio==4.11.0`, `nemoguardrails==0.24.1`, `logfire==5.3.0`, `opentelemetry-sdk==1.45.1`,
    `pydantic==2.13.3`, `httpx==0.28.1`, `torch==2.9.1`, `transformers==4.57.3`, `sentence-transformers==5.4.1`,
    `numpy==1.26.4`, `pandas==2.3.3`, `pymupdf==1.26.7`, `psycopg[binary]==3.3.6`, `sqlglot==30.24.0`,
    `rank-bm25==0.2.2`, `python-dotenv==1.2.4`.
  - Dev dependency: `pytest>=8.3,<9` (8.4.2 locked).
- **How the earlier incompatibilities were resolved:** LangGraph 1.1.10 accepts the MCP environment's
  `anyio` / `pydantic` / `httpx` pins. NeMo 0.24.1 and Logfire 5.3.0 resolve together in one lock. The MCP, RAG and
  Lakebase POC environments are untouched.
- **Deliberately absent:** `langgraph-checkpoint-postgres`, `psycopg-pool` and `databricks-connect`.
- **`langsmith`:** 0.14.12 arrives transitively and is forced off (see §9).
- **Remaining warnings** are third-party only: an authlib deprecation and SWIG `__module__` warnings during the
  FAISS/RAG import. They are not suppressed in `pyproject.toml`.

## 6. Supervisor, planner and controller behaviour

**Supervisor (`supervisor.py`, deterministic, no LLM).** Rules are checked in this order:

1. Tool directive → refuse.
2. Only unavailable routes (ML prediction, procurement planning) → refuse.
3. Routes outside the authorization → refuse. Routes are never widened.
4. `mode=investigation`, more than one route, or multi-step wording → plan. Multi-step wording means investigate,
   root cause, explain why, step by step, and then, cross-check, reconcile, verify against, in each case, or for each
   of.
5. Exactly one route → **fast path**: a one-task plan with no LLM call.
6. Otherwise → clarify.

**Planner (`planner.py`).**

- The LLM output must parse as a strict `DraftPlan` (1–12 tasks, worker literal, operation, dependencies, evidence,
  completion). Otherwise it is rejected as `malformed_output` or `schema_violation`.
- `build_plan` assigns task IDs, default evidence requirements and idempotency keys. LLM operations get one attempt;
  other operations get two.
- Unknown dependencies are kept, so the controller rejects them visibly instead of having them silently dropped.
- **Bounded replanning:** `max_replans = 1`, triggered by:
  - planner errors;
  - invalid LLM plans;
  - a required task failing with `NO_EVIDENCE`.

  A malformed Claude reply (`invalid_model_output` or `model_output_truncated`) is replannable; any other `LlmError`
  is final.
- Offline tests use `FakePlanner`. `ClaudePlanner` is tested only over an in-memory transport.

**Controller (`controller.py`).**

- **Plan validation** returns any of these codes, and an invalid plan never reaches a worker:
  - `plan_investigation_mismatch`, `duplicate_task_id`, `too_many_tasks`;
  - `unknown_operation`, `operation_not_authorized`, `execution_not_authorized`;
  - `self_dependency`, `unknown_dependency`, `dependency_cycle` (Kahn), `dependency_too_deep`;
  - `too_many_attempts`, `task_deadline_too_long`, `evidence_not_producible`, `unknown_required_task`.
- **Scheduling:**
  - `ready(plan, states, limit)` returns pending tasks whose dependencies are all usable, in batches of up to
    `max_parallel` (1 in the MVP).
  - The `execute` node runs a batch sequentially.
  - Dependents of failed tasks are cancelled, never run.
- **Per-attempt pipeline:**
  1. Idempotent reuse: a task already done under the same key returns `SKIPPED` with the earlier evidence.
  2. Permission and budget checks.
  3. Deadline, then circuit breaker, then a ticket.
  4. A thread-pool call with a timeout.
  5. Retries only for `TransientWorkerError` or a timeout, with exponential backoff (0.25 s × 2ⁿ, capped at 2 s).
  6. An unexpected exception is recorded as `INTERNAL` by class name only.
- **Partial results:**
  - An optional task's failure yields a `PARTIAL` investigation.
  - Validated-but-not-executed SQL in an execution task is `PARTIAL`.

## 7. Conversation, HITL and memory interfaces

**Conversation (`conversation_bridge.py`).**

- `ConversationRunner` plugs into the existing `ConversationService` as a route runner. It reads the trusted owner
  from a `ContextVar` set by `ConversationFrontDoor`, and raises `MissingTrustedOwner` without it.
- When the existing router reports mixed structured-plus-document intent, `ConversationFrontDoor` escalates to one
  combined investigation and appends the assistant turn with `route="investigation"`. This can be disabled with
  `allow_combined=False`.
- Assistant-turn metadata records status, route and source objects only. No SQL or rows are stored.

**HITL (`hitl.py`, `graph.py`, `engine.py`).**

- **Default policy:** read-only SQL execution inside a *planned* investigation needs approval. A fast-path execution
  the user asked for does not, and `execution_requires_approval="always"` overrides that. LLM-plan and large-plan
  (4 or more tasks) approval are policy options. The TTL is 30 minutes.
- **The flow:** `request_approval` creates an `ApprovalRequest` with a deterministic ID, a payload SHA-256 and the
  owner. `await_approval` calls `interrupt()`, and `InvestigationEngine.resume(owner, thread_id, approval_id, approved)`
  resumes with `Command(resume=…)`.
- **`decision_problem` rejects:**
  - `approval_id_mismatch`;
  - `approval_already_used`;
  - `not_the_requesting_user`;
  - `payload_mismatch`;
  - `approval_expired`.
- **Outcomes:**
  - Rejection or expiry cancels every task and runs nothing.
  - A resume after a "restart" (new engine, workers and ticket secret, same checkpointer) executes exactly once.
  - A second resume raises `ApprovalNotPending`.
- **Ownership:**
  - Another user gets `InvestigationNotFound` on resume and on `outcome`.
  - `ConversationFrontDoor.approve` also checks thread ownership through the store, then records the decision as a
    conversation decision.
- **Audit:** events (`investigation.started`, `approval.requested`, `approval.decided`, …) contain IDs and decisions
  only, never question text.

**Memory and compression (`memory.py`).**

- `ConversationMemoryView` exposes owner-checked recent messages, the summary, decisions, relevant memory and the
  context budget. `OwnerScopedMemoryRetriever` only searches the caller's own threads.
- `build_digest` builds an `InvestigationDigest` containing:
  - constraint IDs and text;
  - pending approval IDs;
  - project IDs;
  - evidence IDs;
  - unresolved task IDs;
  - status and a checkpoint reference.
- `digest_losses` lists anything a compressed digest dropped. `render_digest` reports `over_budget` instead of
  truncating, so unresolved tasks and approvals are never silently dropped.

## 8. Guardrails, budgets and circuit breakers

**Guardrails.**

- The input guard is the existing NeMo pipeline (no LLM rails). It runs at `intake`, before any planning.
- Upstream evidence passed to downstream workers is filtered with `contains_instruction_like_text`.
- Every response claim is checked by `check_claim` and removed if any of these apply:
  - `unknown_evidence`;
  - `secret_shaped_value`;
  - `unsupported_number`, where numbers must appear in the cited approved evidence, after cited citation strings
    (document IDs, pages) are stripped;
  - `unapproved_numeric_source`, where the number comes from a PDF table, typed record or unverified page;
  - `unsupported_date`;
  - `unsupported_confidence`, since no confidence scores are generated.
- Conflicts are flagged only for the same query: the same fact, objects, SQL hash and parameter hash. Different
  filters on one view are not a conflict.

**Budgets (`InvestigationBudget`, defaults).**

| Limit | Default |
|---|---|
| Tasks | 6 |
| Dependency depth | 3 |
| Parallelism | 1 |
| LLM calls | 8 |
| Input tokens | 80k |
| Output tokens | 12k |
| SQL executions | 3 |
| Attempts per task | 2 |
| Task deadline | 60 s |
| Overall deadline | 120 s |
| Replans | 1 |
| Cost (`max_cost_usd`) | Optional |

Budgets are checked before every call (`check_budget`). Cost is computed only from an explicit `PriceTable`. Without
one it is reported as unknown, never estimated.

**Circuit breakers.** The existing `CircuitBreaker` is used per worker and for the LLM (defaults: 3 failures in 60 s,
30 s cooldown, at most 300 s). Every state transition emits a `circuit_breaker_transition` event. An open breaker fails
the task fast, without calling the worker.

## 9. Logfire instrumentation

- `OrchestratorTracer.local()` calls `logfire.configure(send_to_logfire=False, token=None, console=False,
  inspect_arguments=False, metrics=False, …)` with caller-supplied span processors. Tests use the OpenTelemetry
  `InMemorySpanExporter`. `OrchestratorTracer.disabled()` is the default.
- **Spans:** `orchestrator.node.<name>` and `orchestrator.task_attempt`.
- **Events:**
  - `task_retry`;
  - `circuit_breaker_transition`;
  - `approval_requested` and `approval_decision`;
  - `investigation_finished`, which carries token and LLM-call counters.
- **Attributes** are filtered by `safe_attributes`:
  - only keys in the `SAFE_ATTRIBUTES` allow-list are kept (investigation, plan, task, worker, operation, state,
    latency, tokens, cost, retries, error codes, breaker state, approval decision);
  - string values must match `^[A-Za-z0-9_.:@\-]{0,100}$`, so free text is dropped.
- **Test check:** the spans contain no question text, SQL, secrets, email addresses or document text.
- **LangSmith:**
  - `disable_external_tracing()` forces `LANGSMITH_TRACING`, `LANGSMITH_TRACING_V2`, `LANGCHAIN_TRACING` and
    `LANGCHAIN_TRACING_V2` to `false` on import and on every invocation, and also sets
    `NEMO_GUARDRAILS_NO_USAGE_STATS=1`.
  - Graph calls run inside `no_external_tracing()`, which also applies `langsmith` `tracing_context(enabled=False)`.
  - A test that sets the flags to `true` confirms they are overridden.
- **Audit:** sinks are `MemoryAuditSink` or a `JsonlAuditSink` with an explicit path. The test `conftest` points
  `WBG_AUDIT_LOG` at a temporary file and blocks non-loopback sockets.

## 10. Test results

**Orchestrator suite** (`orchestrator/tests`, 82 tests): **82 passed** in 12.72 s, with 8 third-party deprecation
warnings.

| # | Acceptance item | Tests (file::test) |
|---|---|---|
| 1 | Simple structured question | `test_scenarios::test_simple_structured_question_uses_the_fast_path_without_an_llm_planner`, `…_without_execution_validates_only`; `test_integration::test_real_nl2sql_workflow_behind_the_structured_adapter` |
| 2 | Simple document question | `test_scenarios::test_simple_document_question_uses_the_default_rag_release`, `test_pilot_v2_1_is_an_explicit_option`; `test_integration::test_real_answer_pipeline_on_pilot_v2_1_behind_the_document_adapter` |
| 3 | Complex structured, dependent tasks | `test_scenarios::test_complex_structured_investigation_with_dependent_tasks`, `test_dependents_of_a_failed_task_are_cancelled_not_run` |
| 4 | Combined structured + document | `test_scenarios::test_combined_structured_and_document_investigation`; `test_conversation::test_combined_data_and_document_question_becomes_one_investigation` |
| 5 | Valid / invalid DAGs | `test_plans::test_valid_dependency_graph_passes`, `test_invalid_dependency_graphs_are_rejected` ×7 (cycle, self, unknown dependency, too deep, too many tasks, unknown required task, evidence not producible) |
| 6 | Unauthorized tool rejection | `test_plans::test_unauthorized_tool_requests_are_rejected` ×4, `test_operations_outside_the_routed_capabilities_are_rejected`, `test_unauthorized_plan_never_reaches_a_worker`; `test_resilience::test_workers_refuse_calls_without_a_controller_ticket` |
| 7 | Malformed planner output | `test_plans::test_malformed_planner_output_is_rejected` ×3, `test_malformed_output_gets_one_bounded_replan_then_succeeds`, `test_repeated_malformed_output_fails_safely_without_running_anything`, `test_a_worker_worker_name_unknown_to_the_schema_is_a_planner_error`; `test_integration::test_malformed_claude_plan_is_rejected_like_any_planner_output` |
| 8 | Timeout + bounded retry | `test_resilience::test_tool_timeout_is_retried_with_backoff_and_then_succeeds`, `test_retries_are_bounded`, `test_a_hung_worker_is_stopped_at_its_deadline_and_llm_tasks_are_not_retried` |
| 9 | Circuit breaker | `test_resilience::test_circuit_breaker_opens_and_stops_calls_to_the_failing_worker` |
| 10 | Partial evidence | `test_scenarios::test_failed_optional_task_gives_a_safe_partial_answer`; `test_resilience::test_task_budget_stops_dispatch_and_cancels_the_rest`, `test_execution_budget_is_enforced_before_the_call` |
| 11 | Missing / conflicting evidence | `test_scenarios::test_missing_evidence_is_reported_not_invented`, `test_conflicting_results_of_the_same_query_are_surfaced`, `test_different_filters_on_the_same_view_are_not_a_conflict`, `test_pdf_table_values_and_invented_figures_are_never_approved`, `test_unverified_page_checks_never_become_numeric_support` |
| 12 | HITL interrupt / reject / same-user resume | `test_hitl` (8 tests: interrupt before execution, other-user refusal, rejection, restart-resume exactly once, expiry, decision checks, audit metadata, policy) |
| 13 | Cross-user isolation | `test_conversation::test_cross_user_isolation_of_threads_investigations_approvals_and_memory`, `test_memory_view_reads_only_the_owners_recent_messages_summary_and_decisions`, `test_runner_refuses_without_a_trusted_owner`; `test_hitl::test_another_user_cannot_resume_or_read_the_investigation` |
| 14 | Compression preserves references | `test_conversation::test_compression_preserves_constraints_approvals_projects_evidence_and_unresolved_tasks` |
| 15 | Immutable contracts | `test_contracts` (13 tests: frozen/closed, JSON round-trip, bounded requests, terminal results, append-only reducer, forward-only states, owner hash) |
| 16 | Metadata-safe Logfire traces | `test_observability::test_traces_are_metadata_only`, `test_attribute_filter_drops_free_text_and_unknown_keys`, `test_langsmith_stays_off_even_when_the_environment_asks_for_it` |
| 17 | Existing regressions | See the table below |

**Additional coverage:**

- `test_integration` runs the real NeMo guard, the real `NlToSqlWorkflow` over the local registry (with `FakePortkey`
  and `EchoColumnsExecutor`), the real `AnswerPipeline` on `pilot_v2_1`, and the Claude planner and responder over an
  in-memory transport.
- `test_scenarios` also covers guardrail blocking, withheld personal data, clarification and refusals.
- `test_resilience` also covers token counting, the cost price table, and idempotent reuse across a replan.

**Existing system regression (item 17).** All suites ran unchanged, with `WBG_AUDIT_LOG` pointed at a scratch file and
Hugging Face offline.

| Suite | Interpreter | Result |
|---|---|---|
| `structured_agent/tests`, `tests_workflow_mcp`, `conversation/tests`, `app_config/test_check_env.py`, `rag/retrieval/test_model_loading_offline.py` | `.venv-structured-mcp` | 501 passed |
| Same, without `tests_workflow_mcp` | base (`/opt/anaconda3`) | 454 passed, 5 skipped |
| `rag/ingestion/tests` | base | 45 passed |
| `rag/tests` | base | 72 passed |
| `rag/embedding_cache/tests` | base | 101 passed |
| `rag/release_v2_1/tests` | base | 30 passed |
| `ml/tests` | base | 23 passed |
| `discovery/tests` | base | 129 passed |
| `audits/pdf_table_quality/tests` | base | 12 passed |
| `infrastructure/uc_volume_poc/tests` | base | 5 passed |
| `infrastructure/lakebase_poc/tests` (offline only; `tests_live` not run) | lakebase_poc `.venv` | 41 passed |
| `structured_agent/tests_mcp` | — | **Not run.** Its autouse fixture opens live Databricks Spark sessions (`databricks_state_at_start`), and several servers launched with `server_parameters()` default to the persistent audit log. Running it would be an external data request and would append to `runs/audit_events.jsonl` |

**Integrity checks.**

- The `structured_agent/runs/audit_events.jsonl` SHA-256 was the same before and after all runs
  (`d34868ea4831e463f8090f114efdbc2de38ae2253eff542da36e8a39c2bb2680`, 232,854 bytes).
- Inventory comparison (`INV_LG.csv` before, `INV_MA.csv` after): 56,486 → 87,712 files, with 0 removed and 0 changed.
  - All 31,226 added files are under `orchestrator/`: `.venv` (31,189), `wbg_orchestrator/` (24), `tests/` (11),
    `pyproject.toml` and `uv.lock`.
  - No Bronze, Silver or Gold data, KPI views, RAG defaults, embeddings, PDF extraction, ML models, MCP servers or
    `.env` changed.

## 11. Known limitations

1. **Execution is synchronous.** `max_parallel = 1`, and the execute node runs a batch sequentially. The contracts
   (`ready(limit)`, per-task tickets and deadlines) allow bounded parallelism, but it is not implemented or tested.
2. **Timeouts cannot kill a thread.** A timed-out worker thread is abandoned, and cancellation is cooperative through
   `cancel_event`. A hung call keeps its pool thread (4 maximum) until it returns.
3. **Checkpointing is `InMemorySaver` only.** Checkpoints are not durable or encrypted. Result rows are request-scoped
   and are re-queried after a restart.
4. **Cost is unknown unless a `PriceTable` is supplied.** No Claude prices are hard-coded.
5. **Breakers and limits are per process.** There is no shared limiter store across instances (readiness decision 15).
6. **The Claude planner and responder are validated only against an in-memory transport.** Prompt quality, schema
   adherence and token use against real Claude are unmeasured.
7. **The supervisor's complexity detection is rule-based** (the `COMPLEX` regex plus existing route signals). It can
   send borderline questions to the fast path or ask for clarification.
8. **Combined escalation follows the existing router.** It triggers only when that router reports mixed intent for
   exactly structured + document.
9. **Structured execution in tests uses `EchoColumnsExecutor`.** The read-only Databricks principal is still open
   (readiness decision 13). With development owner credentials, evidence carries `dev_owner_credentials`.
10. **Authorization is a shared application identity.** There is no per-user Databricks OAuth and no user-scoped Unity
    Catalog enforcement (future scope, readiness decision 7). Conversation and approval ownership are per user.
11. **The ML prediction and procurement planning routes remain unavailable** (refused as `route_unavailable`).
12. **`structured_agent/tests_mcp` was not re-run** in this milestone (see §10).

## 12. Readiness for the next (live) tests — not started

| Next step | Ready? | What is in place / what is needed first |
|---|---|---|
| **Live Portkey / Claude** | Code-ready | `ClaudePlanner` and `ClaudeResponseGenerator` use the existing `PortkeyClaudeClient` (Claude-only route, LLM breaker, budgets, malformed-output replan). **Needed:** explicit approval for live calls, a `PriceTable` with current Claude prices, a small fixed question set with expected plan shapes, and a token or cost cap per run |
| **Lakebase checkpointing** | Design-ready | State is JSON-only and the thread ID is owner-scoped; the restart-resume test already runs against a shared saver. **Needed:** approval to add `langgraph-checkpoint-postgres` and `psycopg-pool` to this uv project, a dedicated `wbg_orchestrator` schema on a dev branch, `EncryptedSerializer` key handling and retention (readiness decisions 6 and 9), and the isolated live checkpoint/resume test proposed in the readiness report §6 |
| **Databricks Apps** | Not ready | **Needed:** a read-only service principal (decision 13), the execution path (decision 8), model loading in Apps (decision 10), a telemetry export target (decision 11), a durable audit sink (decision 14), and the HITL policy sign-off (decision 17) |

**Open item carried over from Step 6E:** the decision whether to truncate
`structured_agent/runs/audit_events.jsonl` back to 226,591 bytes. It has not been modified in this milestone.

**Stopped here:** no live LLM invocation, Lakebase write, data migration or Databricks deployment was performed.
