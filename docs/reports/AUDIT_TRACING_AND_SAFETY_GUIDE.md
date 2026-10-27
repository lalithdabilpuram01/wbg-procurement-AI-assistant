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
