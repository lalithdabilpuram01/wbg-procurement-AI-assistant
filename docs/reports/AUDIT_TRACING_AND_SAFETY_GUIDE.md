# Audit, Tracing and Safety: How to Read and Debug the Platform

**Date:** 2026-10-27 · **Scope:** the local app (API + LangGraph orchestrator + web UI), Milestone G.

## 1. Where the records are

| Record | Location | Contents |
|---|---|---|
| Investigation audit | `orchestrator/runs/app_audit.jsonl` | One line per step: guardrail, plan, each task, approval, answer, finish. Metadata only |
| SQL gateway audit | `orchestrator/runs/structured_audit.jsonl` | Every validated or executed query: validation id, objects, checks, row count |
| Claude spend ledger | `orchestrator/runs/llm_ledger.json` | Every Claude attempt: tokens, worst-case and committed USD, caps |
| Logfire traces | Logfire project (when `LOGFIRE_SEND_TO_LOGFIRE=true`) | Spans per node, task attempt and Claude call, with the same correlation ids |
| Conversation history | Local PostgreSQL, schema `conversation` | Turns (personal data masked), routes, statuses, guardrail reasons |

**Never recorded anywhere:** question or answer text in audit and traces; SQL result rows; credentials; personal data.

## 2. Debugging one answer

1. **Find the request.** The UI and the API return a `request_id` for each answer. Or list recent runs:

   ```bash
   cd orchestrator
   env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -m wbg_orchestrator.audit list
   ```

2. **Read its timeline.** Use the request id or the investigation id:

   ```bash
   env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -m wbg_orchestrator.audit show req_...
   ```

   Example (Planning Brief, offline test run):

   ```text
     +   0.0s  input guardrail   allowed  (app.input)
     +   0.0s  plan              fast_path (brief), 8 task(s)
     +   0.0s  task similar      procurement_planning.match_similar_projects -> succeeded  4 evidence
     +   0.8s  task predict      ml_prediction.predict_planned_packages -> succeeded  6 evidence, 777 ms
     +   0.8s  task lessons      document_intelligence.answer_document_question -> succeeded  1 evidence
     +   2.1s  answer            answered: 12 claim(s) kept, 0 removed, from 18 evidence
     +   2.1s  finished          answered: 8 task(s), 0 Claude call(s)
   ```

3. **Open the trace.** The timeline prints the Logfire trace id. In Logfire, search `trace_id = '<id>'` to see:
   - every span (`orchestrator.node.*`, `orchestrator.task_attempt`, `llm.call`) with durations and token counts;
   - retries, circuit-breaker transitions and checkpoint events.

4. **Read the error codes.** Common ones:

   | Code | Meaning | Where to look |
   |---|---|---|
   | `insufficient_evidence`, `cannot_answer` | The document or data step found nothing it could cite | The step's `evidence_count`; the question wording |
   | `gold_lookup_rejected` | A fixed query was refused by the SQL validator | `structured_audit.jsonl` (validation errors) |
   | `task_limit`, `deadline`, `llm_call_limit` | An investigation budget was reached | The finished line (tasks, calls); the request budget |
   | `model_output_truncated` | Claude's answer exceeded its output cap | `llm_ledger.json`; `OUTPUT_CAPS` in `runtime.py` |
   | `SpendLimitReached` | The app's Claude ledger cap was reached | `llm_ledger.json` (`WBG_LLM_MAX_ATTEMPTS` / `WBG_LLM_MAX_USD`) |
   | `cancelled_by_user` | Stop was pressed | The task states after the stop |

## 3. Safety controls and what the user sees

| Control | Where | User sees |
|---|---|---|
| Input policy (NeMo Colang + regex) | `structured_agent/guardrails` | A specific reply per reason (instruction override, restricted data, credentials, format) |
| **Topic rail** (NeMo Colang) | `policies.evaluate_topic` | Off-topic: what the assistant can do, with an example. Misconduct judgments: what the records can show |
| Unclear, no procurement content | Conversation front door | The same scope reply (no planner call) |
| Personal data masking (Presidio) | `conversation/pii.py` | E-mails, phones, card and IBAN numbers, IP addresses and SSNs replaced by `<TYPE>` in everything stored, forwarded or answered |
| RAG privacy guard | `rag/retrieval/core.py` | Requests for personal contact data in documents are withheld |
| Read-only data access | SQL validator + `wbg-readonly` principal | Claude-written SQL needs the user's approval; fixed templates are reviewed in code |
| Rate limits | `wbg_orchestrator/rate_limit.py` | 6 work requests per minute and 200 per day per user; 429 with the wait time |
| Spend caps | Claude ledger + per-investigation budgets | The run stops with a budget code; nothing is charged beyond the cap |
| Grounding | `response.py` claim checks | Unsupported numbers, dates and confidence words are removed and counted |

## 4. Configuration

| Setting | Default | Purpose |
|---|---|---|
| `WBG_RATE_PER_MINUTE` / `WBG_RATE_PER_DAY` | 6 / 200 | Per-user request limits |
| `WBG_LLM_MAX_ATTEMPTS` / `WBG_LLM_MAX_USD` | 10 / 2.00 | App-wide Claude ledger caps |
| `LOGFIRE_SEND_TO_LOGFIRE` | set by the operator in `.env` | Export traces to Logfire |

## 5. Agent contracts and policies

**Agent catalog.** Generated by `python -m wbg_orchestrator.catalog` from `wbg_orchestrator/catalog.py`.
`tests/test_agent_contracts.py` fails if the catalog, the workers, the authorization table, the evidence specifications,
the planner schema and the engine's worker registry stop agreeing.

| Worker | Operation | Reads | Returns (evidence kinds) | Data (read-only) | LLM | Attempts | Approval |
|---|---|---|---|---|---|---|---|
| structured_data | answer_data_question | task question; conversation memory | figures from approved Gold/Silver data (or the validated query only) (structured_result) | Databricks via the read-only principal and the SQL validator | yes | 1 | running Claude-written SQL in a planned investigation |
| structured_data | describe_metric | task question | a registered metric definition (metric_definition) | local registry metadata | no | 2 | none |
| structured_data | find_tables | task question | approved tables or views for the question (table_candidate) | local registry metadata | no | 2 | none |
| document_intelligence | answer_document_question | task question; similar-project scope from upstream evidence | page-checked passages with citations (document_passage, document_table, typed_plan_record) | Lakebase pgvector (bge-m3) + local corpus, read-only | no | 1 | none |
| document_intelligence | retrieve_passages | task question | raw matching passages (not page-checked) (document_passage, document_table, typed_plan_record (unverified)) | Lakebase pgvector (bge-m3) + local corpus, read-only | no | 2 | none |
| ml_prediction | predict_package | the package of the request (structured, never text) | three model estimates (Models 2-3 marked synthetic) (model_prediction) | frozen models (hash-checked) + Gold via fixed read-only SQL | no | 2 | none |
| ml_prediction | predict_planned_packages | the planning spec; suggested methods from upstream evidence | three model estimates per planned package (model_prediction) | frozen models + Gold via fixed read-only SQL | no | 2 | none |
| procurement_planning | match_similar_projects | the planning spec (profile) | up to 8 similar past projects with reasons and a document scope (similar_project) | Gold via fixed read-only SQL + Lakebase similarity | no | 2 | none (fixed, reviewed SQL) |
| procurement_planning | market_benchmarks | the planning spec (country, categories) | methods, review types and supplier origin used historically (structured_result) | Gold via fixed read-only SQL | no | 2 | none (fixed, reviewed SQL) |
| procurement_planning | governance_context | the planning spec (country) | latest CPIA, WGI and income group (structured_result) | Gold via fixed read-only SQL | no | 2 | none (fixed, reviewed SQL) |
| tender_design | check_tender_design | the tender spec (package, estimate, lots, draft text) | terms read from the draft and checks against history (bid window, method, review, packaging, qualification ratios) (tender_check) | Gold notices via fixed read-only SQL + typed procurement-plan records | no | 2 | none (fixed, reviewed SQL; deterministic rules) |
| lesson_cards | find_lessons | the tender spec, planning spec or monitored project | reviewer-approved lesson cards that fit the lifecycle stage (or how many await review) (lesson_card) | lesson cards in the Knowledge Graph schema (Lakebase) | no | 2 | none (only approved cards are used) |
| project_monitoring | watch_project | a project ID from the monitoring question | status summary and cited early warnings (plan rows, contract records, ISR ratings) (early_warning) | typed procurement-plan records and ISR ratings (pilot_v3 corpus) | no | 2 | none (read-only, deterministic rules) |

**Rules every agent follows** (enforced in code):

| Rule | Where |
|---|---|
| Messages between agents are typed, frozen, closed and versioned (`schema_version`) | `contracts.py` |
| A worker runs only with an HMAC ticket the controller issued for that task and attempt | `workers/base.py` |
| An operation must be allowed by the request's routes and capabilities; text never widens them | `authorization.py`, `supervisor.py` |
| Each operation returns only the evidence kinds of its specification; required evidence must be verified | `contracts.OPERATION_EVIDENCE`, `verification.py` |
| Errors carry a category, a code and a safe message, never exception text | `contracts.ExecutionError` |
| Structured inputs (packages, planning specs) come from the API, never from message text | `PackageSpec`, `PlanningSpec` |
| Claude-written SQL in a planned investigation needs the user's approval; fixed templates are reviewed in code | `hitl.py` |

## 6. Reliability

| Failure | Handling |
|---|---|
| Transient worker or provider error | Bounded retries with backoff, per operation; never for LLM calls inside workers |
| A failing dependency | A circuit breaker per worker and for Claude; the step fails fast with `circuit_open` |
| Slow step | Per-task and per-investigation deadlines |
| Process restart | Every step checkpointed (LangGraph + Postgres on Lakebase); `recover` continues from the last checkpoint |
| Repeated request | `client_message_id` idempotency per user and conversation; task idempotency keys |
| Runaway cost | A persistent Claude ledger with worst-case reservation before each call; per-investigation budgets; per-user rate limits |
| Stop | A cooperative cancel before the next step, also before the investigation has started |
| Wrong model or index | Frozen model hashes and the Lakebase release fingerprint are checked before use; a mismatch fails closed |
| Guardrail failure | Fails closed (`guardrail_error`) |
| Unsupported statements | Claim checks remove unsupported numbers, dates and confidence words, and the answer says how many were removed |

## 7. Memory and context

| Layer | What it keeps | Where |
|---|---|---|
| Short-term | A sliding window of recent turns (6), the active filters and entities | `context_builder.py`, `followups.py` |
| Long-term | Approved decisions and constraints; versioned summaries of older turns | Conversation store (Postgres) |
| Compression | Older turns summarized into structured summaries; loss check (project ids, evidence, decisions, countries); stale after a topic reset, a correction, a revoked decision or 30 days | `compression.py` |
| Budget | A token budget per request: mandatory parts first, optional parts while they fit, pruned deterministically | `context_builder.py`, `tokens.py` |
| Planning context | A brief's project profile and similar projects, carried into later questions of the conversation | `conversation_bridge._planning_context` |
| Coverage | Every path, including investigations, briefs and assessments started by the front door (`memory_for`, `compact`) | `conversation/service.py` |
| Prompt caching | The system prompts and the SQL schema context are cache breakpoints (5 minutes); cache reads cost 0.1x, writes 1.25x, and the ledger prices both | `llm_client.py`, `budget.PriceTable` |
