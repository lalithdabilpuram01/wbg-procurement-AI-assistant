# Natural-language → SQL workflow (Claude-only, development MVP)

Question → input guardrails → registry tools (stage-gated) → retrieved-context guardrails → Claude through Portkey (Claude-only route) → model-output guardrails → existing `validate_sql` → execution gate → existing `execute_readonly_sql` → final-response guardrails → typed result.

Guardrails (NVIDIA NeMo Guardrails plus application policies) are described in [`../guardrails/README.md`](../guardrails/README.md). The CLI always runs with them; library callers pass `guardrails=` explicitly. Results keep `proposed_sql` (Claude's proposal), `validated_sql` (approved by the validator) and `executed_sql` (actually executed) separate.

## Modules

| Module | Responsibility |
|---|---|
| `settings.py` | Loads `PORTKEY_API_KEY` and `PORTKEY_CLAUDE_MODEL` (environment first, then `.env`) through `app_config/check_env.py`. Raises `ConfigurationError` naming the problem variables. Holds the runtime limits as code defaults |
| `llm_client.py` | `PortkeyClaudeClient`: one HTTP request (httpx) to `https://api.portkey.ai/v1/chat/completions`. Sends the Model Catalog model and **no `x-portkey-config`**, and **no sampling parameters**. Structured output via `response_format` json_schema; `max_tokens` 16000. Typed errors keep the HTTP status and the provider's short error code (never the message). Rejects replies from non-Claude models and truncated replies; no retries |
| `live_guard.py` | Fail-closed live-call guard. A real request needs `--confirm-live-call` **and** `WBG_ALLOW_LIVE_LLM_CALLS=true` in the process environment (never read from `.env`). The client accepts only in-memory mock transports or a `LiveAccess` grant, and re-checks the variable before every live request |
| `tools.py` | `McpToolCaller` (real MCP stdio) and `InProcessToolCaller` (the same service methods). Only the seven approved tools can be called |
| `context.py` | Grounded schema context: at most 3 tables, authorized public columns only, joins between selected tables, metric definitions, at most 2 reviewed SQL patterns |
| `sql_generator.py` | Prompt, strict JSON schema for the reply (`SqlDraft`) and typed parameters |
| `resilience.py` | In-memory token-bucket rate limits, concurrency limits, circuit breakers (CLOSED / OPEN / HALF-OPEN) and the request deadline |
| `workflow.py` | `NlToSqlWorkflow.run(question, execute)`: orchestration, bounded correction, failure classification, sanitised audit events |
| `result.py` | `WorkflowResult`, statuses, failure classes, safe user message |
| `tracing.py` | Logfire settings, safe span/event API (`WorkflowTracer`), traced registry tool calls |
| `cli.py` | Manual live run. Refuses (exit 3) unless both live opt-ins are present |

## Observability (Pydantic Logfire)

`tracing.py` configures a **local** Logfire instance (`logfire.configure(local=True)`). It does not replace the global
OpenTelemetry provider, so the service's existing spans are not duplicated. There is no automatic instrumentation (no
`instrument_httpx`, no `capture_all`), so HTTP headers and bodies are never captured.

**Spans and events:**

| Name | Kind | What it records |
|---|---|---|
| `nl2sql.request` | span | The whole request, with its final status |
| `nl2sql.metadata_retrieval` | span | Registry and MCP metadata retrieval, with one `nl2sql.registry_tool` span per tool call |
| `nl2sql.sql_generation` | span | The generation loop |
| `nl2sql.claude_request` | span | One Claude call through Portkey |
| `nl2sql.sql_validation` | span | One validation of the proposed SQL |
| `nl2sql.databricks_execution` | span | Only when execution is requested |
| `nl2sql.rate_limited` | event | A rate-limit or concurrency rejection |
| `nl2sql.circuit_breaker_transition` | event | A breaker changing state |
| `nl2sql.final_status` | event | The request's final status |

**Attributes:** an allow-list only. Request id, model identifier, latency, token counts, validation outcome and error
codes, provider status code and error type, failure class, row count, Databricks access mode. Exceptions are recorded
only as their class name. Logfire's default scrubbing stays on.

**Export:** off unless `LOGFIRE_SEND_TO_LOGFIRE=true`, even when `LOGFIRE_TOKEN` is set. The JSONL audit trail
(`observability.Telemetry`) remains the durable record. Logfire is installed only in `.venv-structured-mcp`; elsewhere
tracing is disabled and its tests are skipped.

## Safety properties

- **SQL path:** the model only proposes SQL. The existing validator decides, and the existing gateway executes (it validates again, applies the credential gate and timeout). There is no other path to Databricks.
- **Identity:** the principal is fixed by the caller (MCP server deployment, or the in-process trusted context), never by the model or the question.
- **LLM call bound:** at most 2 LLM calls per request (generation plus one correction, only for recoverable validation errors). Provider errors are never retried. Databricks execution is attempted at most once.
- **Failure classes** are kept separate: provider (`provider_*`, `invalid_model_output`, `unexpected_provider`), `sql_validation_denied`, `authorization_denied`, execution (`execution_*`, `result_validation_failed`) and controls (`rate_limited`, `circuit_open`, `deadline_exceeded`).
- **Logs:** audit events hold codes, counts, tokens and durations only. No API key, question text, prompt, SQL or rows.
- **Model text:** the model's description of its query is returned as `model_description_unverified`. The user message is built only from validated facts.

## Run tests (no live calls)

Every test runs with the live opt-in removed and httpx's real network transport disabled (autouse fixture), so valid
credentials in `.env` cannot turn a test into a real request.

```bash
python -m pytest structured_agent/tests app_config -q -p no:cacheprovider                     # shared env (or the venv)
.venv-structured-mcp/bin/python -m pytest structured_agent/tests_workflow_mcp -q -p no:cacheprovider   # over MCP
```

## Live run (only with approval)

```bash
WBG_ALLOW_LIVE_LLM_CALLS=true .venv-structured-mcp/bin/python -m structured_agent.nl2sql.cli --confirm-live-call \
  "How many bid invitations were published in fiscal year 2024?"
# validation only by default; add --execute --allow-dev-owner-credentials to also run the SQL on Databricks
```

## Limitations

- **Single process only.** Rate limits, concurrency limits and breakers are in memory, which is not sufficient for multi-instance deployment; that needs Redis-backed shared state (see `docs/llm_resilience_architecture.md`).
- **Single principal per MCP server.** There is no per-user authentication yet.
- **Unverified model support.** Whether Portkey forwards `response_format` json_schema for the configured model as Claude structured output is not yet confirmed live. `ClaudeSettings.use_json_schema_output=False` falls back to prompt-only JSON, which is still Pydantic-validated.
- **First live request.** The accidental request on 2026-10-25 (trace `req_5f5d2de809c24544`) was rejected with a 4xx client error. It sent `temperature: 0`, which Claude Sonnet 5.5 rejects; that has been removed. The exact status and error code must be confirmed in the Portkey log.
- **Execution timeout.** It is enforced by the gateway (60 s), not by the workflow's per-request deadline.
