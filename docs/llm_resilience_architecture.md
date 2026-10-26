# LLM and Databricks Resilience Architecture

**Status:** design, 2026-10-25. Nothing in §3–§7 is implemented yet; §8 lists what exists today.
**Scope:** rate limiting, circuit breakers, retries and fallback for the Portkey LLM gateway and Databricks execution.
**Related:** `structured_agent/STRUCTURED_AGENT_MCP_REPORT.md` (current execution gateway), `.env.example`.

## 1. Request path and where each control sits

```text
client ──► API boundary ──────────────► orchestrator ──► LLM client ───────────► Portkey gateway ──► Claude (now)
           1 authenticate                                 4 circuit breaker        5 retries          Grok (later,
           2 rate limits (429)                              per route               + fallback          after §5.4)
           3 request budget (tokens, calls, deadline)       0 app retries          (Portkey Config)
                                      └────────────────► Databricks gateway ──► Databricks
                                                          4 circuit breaker
                                                          + concurrency limit
                                                          (validator, credential gate,
                                                           timeout already exist)
```

**Order matters:**
1. Identity is established first.
2. Then rate limits and budgets are applied.
3. Then policy, SQL validation and guardrails, which run before any upstream call.
4. Breakers and retries apply only to upstream calls that policy has already allowed.

## 2. Identity (prerequisite for every limit)

- Tenant and user come **only** from authenticated credentials, such as token claims verified at the API boundary.
  - A `tenant_id` or `user_id` in a request body, query string, header or tool argument is never authoritative.
  - At most it is a hint that must equal the authenticated value, otherwise `403`.
- **Today** the MCP server has one deployment-level principal per process (`WBG_MCP_PRINCIPAL`), and there is no per-user authentication. Per-user and per-tenant limits therefore need the authentication layer first.
  - Until it exists, the only valid limit scope is "per server process / principal".
- Metric labels and logs carry tenant ids and hashed user ids only, never names or e-mail addresses.

## 3. Rate limiting (application API boundary)

### 3.1 Limits

| Limit | Scope | Purpose | Proposed default (configurable) |
|---|---|---|---|
| Request rate | per user, per tenant | Protects the API | user 30/min, tenant 300/min |
| LLM call rate | per user, per tenant | Cost and provider quota | user 10/min, tenant 100/min |
| Databricks execution rate | per user, per tenant | Warehouse cost | user 10/min, tenant 60/min |
| Concurrent LLM calls | per process (dev); global (prod) | Bounded fan-out | 4 per process; tenant 8 |
| Concurrent Databricks executions | per process (dev); global (prod) | Bounded warehouse load | 2 per process; tenant 4 |
| Per-request budget | per request | Stops runaway chains | ≤ 2 LLM calls for SQL (generation + one correction, matching the existing bound); ≤ 3 Databricks executions; token budget (input + output) set per route after the model is chosen; deadline 90 s |

**Algorithm:**
- Token bucket per `(scope, key, limit)` for rates.
- A counting semaphore with time-limited leases for concurrency, so a crashed worker cannot hold a slot forever.

**Budgets:**
- Checked before each upstream call, and decremented with the actual usage returned by the provider.
- When a budget is exhausted the request stops with a controlled error. It is never retried.

### 3.2 Responses

| Situation | HTTP | Body `error.code` | Headers |
|---|---|---|---|
| Rate or concurrency limit exceeded | **429** | `RATE_LIMITED` (with `limit`: request / llm / databricks / concurrency) | `Retry-After: <seconds until a token or slot is expected>`, rounded up |
| Per-request budget exhausted | 429 | `BUDGET_EXHAUSTED` | `Retry-After` only if the budget is time-windowed |
| Circuit open, no healthy route | **503** | `UPSTREAM_UNAVAILABLE` (names the dependency class, e.g. `llm`, `databricks`) | `Retry-After: <remaining cooldown>` |

The MCP tools return the same codes as typed errors (`ToolError`), consistent with the existing contracts.

### 3.3 Implementation by environment

- **Local single-process development:** in-memory token buckets and `asyncio`/thread semaphores. These are correct only for one process.
- **Multi-instance production:** Redis-backed limits, so all instances share counters.
  - Atomic token bucket or sliding-window counter (one Lua script per check).
  - Semaphores with expiring leases.
  - Keys of the form `rl:{tenant}:{user}:{limit}`.
- **Redis unavailable:** fail **closed** for LLM and Databricks operations (`503`); failing open would remove cost and quota protection. Cheap read-only registry calls may fail open with a warning metric.

## 4. Circuit breakers

### 4.1 Breakers

| Breaker | Covers | Key |
|---|---|---|
| `llm.claude_only` | Portkey requests that send only `PORTKEY_CLAUDE_MODEL`, with no Config | the route the application calls |
| `llm.fallback_config` (later) | Portkey requests that use the Claude → Grok Config | the Config route |
| `databricks.execution` | Execution gateway calls to Databricks | per workspace / compute |

Breakers are keyed by the **route the application calls**, not by individual providers behind Portkey. The application cannot see which Portkey target served a request; that is visible only in Portkey logs and traces.

### 4.2 States

```text
           eligible failures ≥ threshold within window
 CLOSED ───────────────────────────────────────────────► OPEN
   ▲                                                     │ cooldown elapsed
   │ success_threshold consecutive probe successes       ▼
   └──────────────────────────────────────────────── HALF-OPEN ──► OPEN (any probe failure;
                                                     (≤ N probes)          cooldown doubles, capped)
```

- **CLOSED:** calls flow. Eligible failures are counted in a rolling window.
- **OPEN:** calls fail fast with `503 UPSTREAM_UNAVAILABLE` and `Retry-After` set to the remaining cooldown. No upstream call is made.
- **HALF-OPEN:** at most `max_probes` real requests pass concurrently; other requests fail fast.
  - `success_threshold` consecutive successes → CLOSED.
  - Any eligible failure → OPEN again, with the cooldown doubled up to a cap.

**Proposed defaults** (configurable per breaker):

| Breaker | Failure threshold | Rolling window | Cooldown (cap) | Max probes | Successes to close |
|---|---:|---:|---|---:|---:|
| LLM routes | 5 | 60 s | 30 s (5 min) | 1 | 2 |
| Databricks | 3 | 120 s | 60 s (10 min) | 1 | 2 |

In production, breaker state is shared through Redis, so one instance's view does not differ from another's. In local development it is in memory.

### 4.3 What counts as a failure

**Counted (eligible transient upstream failures)**, based on the **final** outcome of one upstream call, after Portkey's own retries:
- connection errors and timeouts;
- HTTP 429, 5xx and 529 from the gateway or provider;
- Databricks connection, timeout or internal errors.

**Never counted:**
- authorization or policy denials;
- SQL validation rejections (`FORBIDDEN_OPERATION`, `FAN_OUT_RISK`, …);
- guardrail or NeMo Guardrails rejections;
- `400`/`422` request errors;
- empty search results or zero-row query results;
- the caller's own cancellation;
- rate-limit or budget rejections produced by this application.

**Not counted, but alerting:** `401`/`403` from Portkey or Databricks. These are configuration errors that retrying cannot fix. Fail fast with `UPSTREAM_AUTH_ERROR` and raise an alert.

### 4.4 Audit and metrics for transitions

Every transition emits an audit event and a metric with these fields only:
- breaker name;
- from and to state;
- reason class (e.g. `timeout`, `http_5xx`);
- failure count in the window;
- cooldown;
- timestamp.

No prompts, SQL text, results or identifiers. This uses the same allow-list approach as `structured_agent/observability.py`.

## 5. Retries and fallback

### 5.1 One retry owner per layer (no nested retries)

| Layer | Retries upstream calls? | Rule |
|---|---|---|
| Portkey Config (`retry`) | **Yes, the only retry owner for LLM calls** | Retries only eligible transient status codes, with exponential backoff and `use_retry_after_headers: true` |
| Application LLM client | **No** (0 automatic retries) | Sees only the final outcome, which the breaker counts once |
| Orchestrator | No retries | The existing *bounded correction* (one regeneration after a recoverable SQL-validation error) is a new, policy-approved request, not a retry. It counts against the request's LLM-call budget and is never triggered by provider errors or by security rejections |
| Databricks gateway | **No** (0, as today) | Reads are idempotent, but warehouse cost and timeouts make blind retries unsafe. A later option is at most 1 retry for a connection failure *before* the statement is submitted, with jittered backoff |

**Upstream attempt bound** (Portkey `retry.attempts = 2`; allowed values are up to 5):

| Path | Upstream attempts per LLM call | Per user request (≤ 2 LLM calls) |
|---|---:|---:|
| Claude-only route (now) | ≤ 3 | ≤ 6 |
| Fallback Config route (later, 2 targets) | ≤ 6, if retries apply per target (**to verify in Portkey trace logs**) | ≤ 12 |

**Request deadline:** the 90 s request deadline (§3.1) bounds wall time. Portkey caps cumulative retry waiting at 60 s, and a single `Retry-After` over 60 s fails the request immediately, so the application's per-call timeout must leave room for that.

**Jitter:** Portkey documents exponential backoff (1, 2, 4, 8, 16 s) but not jitter. Jitter is applied wherever the application itself schedules a delay (Databricks, queued work).

### 5.2 What may be retried or trigger fallback

**Eligible:** `408`, `429`, `500`, `502`, `503`, `504`, `529`, timeouts and connection errors.

**Never:**
- `400`, `401`, `403`, `404`, `422`;
- content-policy responses;
- anything the application rejected: SQL validator, authorization, data policy, guardrails, budgets;
- **non-idempotent or human-approved actions** (e.g. future publication or export). These are executed once, with an idempotency key; after a failure they need an explicit new approval.

**Portkey's default** is to fall back on **any non-2xx** status. The fallback Config must therefore set `strategy.on_status_codes` to the eligible list, otherwise a malformed request (`400`) would be re-sent to Grok.

### 5.3 Portkey Config (reuse; adjust before activation)

Shape of the existing Claude → Grok Config, as it should be before it is activated. Slugs are illustrative; model names come from the Model Catalog.

```json
{
  "strategy": { "mode": "fallback", "on_status_codes": [429, 500, 502, 503, 504, 529] },
  "retry": { "attempts": 2, "on_status_codes": [408, 429, 500, 502, 503, 504, 529], "use_retry_after_headers": true },
  "targets": [
    { "override_params": { "model": "@wbg-anthropic/<claude model slug>" } },
    { "override_params": { "model": "@<xai integration slug>/<grok model slug>" } }
  ]
}
```

### 5.4 Keeping Grok out of the effective path until it is verified

A local flag cannot disable a fallback that lives inside a Portkey Config. The Claude-only path is therefore enforced at the gateway level:

1. **Requests send no Config.**
   - They use only the Model Catalog model: `model = PORTKEY_CLAUDE_MODEL`, which has the form `@wbg-anthropic/<model slug>`.
   - `PORTKEY_CONFIG_ID` is deliberately **not** in `.env`, and `app_config/check_env.py` flags it if it is added.
2. **The service API key must have no default Config attached.**
   - Portkey applies a key's default Config to every request that does not specify one.
   - Check it in Portkey: **API Keys → edit the key → Config**. It must be empty.
   - If a default Config is required by policy, use a separate key for Claude-only development instead.
3. **The breaker covers only `llm.claude_only`.** No application code path selects Grok directly.
4. **Activation checklist for the fallback route** (all required, with your approval):
   1. Confirm the xAI quota in the xAI console.
   2. Run one approved test request through the Grok integration only, and record the outcome in Portkey logs.
   3. Restrict fallback `on_status_codes` as in §5.3.
   4. Verify in Portkey trace logs how retries and fallback combine, and update the attempt bound in §5.1.
   5. Add `PORTKEY_CONFIG_ID`, plus the `llm.fallback_config` breaker.

### 5.5 Gateway fallback vs application breakers (no conflict)

- **Portkey** decides *within* one request whether to retry or fail over between its targets.
- **The application** decides *across* requests whether to call a route at all.
- The application never performs its own provider failover on top of Portkey's, so there is no double fallback.
- While `llm.fallback_config` is CLOSED, Portkey may serve from Grok. If both targets keep failing, the application breaker opens and requests fail fast with `503` instead of hammering two providers.
- An OPEN breaker on `llm.claude_only` is **not** a reason to switch to the fallback route automatically. Switching routes is a deliberate, configured decision, not a runtime reaction.

## 6. Observability

Metrics use OpenTelemetry instruments, so they work with Logfire later; no hosted export until approved.

| Metric | Type | Labels (no identifiers beyond tenant / hashed user) |
|---|---|---|
| `wbg.requests` | counter | `route`, `status_class` |
| `wbg.request.duration` | histogram (s) | `route`, `stage` (`llm`, `databricks`, `total`) |
| `wbg.rate_limit.rejections` | counter (HTTP 429) | `limit`, `scope` (`user`, `tenant`, `process`) |
| `wbg.requests.rejected` | counter | `reason` (`policy`, `sql_validation`, `guardrail`, `budget`) |
| `wbg.inflight` | up/down counter | `kind` (`llm`, `databricks`) |
| `wbg.circuit_breaker.state` | gauge (0 closed, 1 half-open, 2 open) | `breaker` |
| `wbg.circuit_breaker.transitions` | counter | `breaker`, `from`, `to`, `reason_class` |
| `wbg.upstream.calls` | counter | `route`, `outcome` (`ok`, `transient_error`, `client_error`, `auth_error`) |
| `wbg.upstream.errors` | counter | `route`, `error_class` (`timeout`, `http_429`, `http_5xx`, …) |
| `wbg.upstream.retries` | counter | `route`. Source: Portkey logs and analytics (the application sees only final outcomes) |
| `wbg.fallback.activations` | counter | `config`. Source: Portkey trace logs |
| `wbg.llm.tokens` | counter | `route`, `direction` (`input`, `output`). From response usage |

**Never logged or exported:**
- API keys and authorization headers;
- prompts or completions containing documents;
- SQL text or result rows;
- restricted procurement data (D-14, D-14b);
- user names or e-mail addresses.

SQL is referenced only by its SHA-256 and validation id, as the existing audit log already does.

## 7. Configuration

**Current `.env` (development):**

| Name | Required now | Notes |
|---|---|---|
| `PORTKEY_API_KEY` | yes | Portkey service key; read automatically by the Portkey SDK |
| `PORTKEY_CLAUDE_MODEL` | yes | Model Catalog string `@wbg-anthropic/<model slug>`; sent without a Config |
| `WBG_ALLOW_DEV_OWNER_CREDENTIALS` | existing | `false` = default deny for the owner Databricks credential |

**Not in `.env`:**
- Raw Anthropic or xAI keys (held by Portkey integrations).
- `PORTKEY_CONFIG_ID` (§5.4).
- Rate-limit, breaker and Logfire settings. These become typed code defaults with optional overrides when they are implemented, not speculative variables now.

The gateway URL is the Portkey SDK default, so no base-URL variable is needed.

## 8. Documented now vs implemented

| Item | Status |
|---|---|
| Databricks execution timeout (60 s), row limit, SELECT-only validator, credential gate, typed errors, sanitised audit, OpenTelemetry spans | **Implemented** (`structured_agent/`) |
| Bounded SQL-generation correction (max 1, recoverable errors only, no policy broadening) | **Implemented** (`structured_agent/generation.py`) |
| `.env` minimal variables and `app_config/check_env.py` (names only, flags raw provider keys and `PORTKEY_CONFIG_ID`) | **Implemented** |
| Portkey client: Claude-only route, no Config | **Implemented** (`structured_agent/nl2sql/llm_client.py`); one unapproved live request was sent accidentally on 2026-10-25 (HTTP 400), see the nl2sql report |
| Authentication and per-user / per-tenant identity | Not implemented (prerequisite for §3) |
| Rate limits, concurrency limits, budgets, 429 / `Retry-After` | **Partly implemented, in memory, single process** (`structured_agent/nl2sql/resilience.py`): per-principal token buckets, concurrency slots, 2 LLM calls per request, 90 s deadline, `retry_after_seconds` in results. Token budgets and HTTP 429 responses wait for the API layer |
| Circuit breakers (LLM route, Databricks) and transition audit | **Implemented, in memory, single process** (`llm.claude_only`, `databricks.execution`) |
| Redis-backed shared limits and breaker state | Design only (production) |
| Metrics in §6; Logfire export | **Logfire tracing implemented** for the NL2SQL workflow (`structured_agent/nl2sql/tracing.py`): spans and events with allow-listed attributes, local instance, export off unless `LOGFIRE_SEND_TO_LOGFIRE=true`. Metrics instruments not yet implemented |
| Grok fallback route | Disabled by design until §5.4 is complete |

**Note on existing code:** `structured_agent/generation.py` contains two disabled provider stubs, `ClaudeProvider` (direct Anthropic SDK with `ANTHROPIC_API_KEY`) and `PortkeyProvider` (legacy virtual-key variable). They predate this design. The Portkey implementation phase should replace them with the Claude-only Model Catalog route. Direct Anthropic calls would bypass the gateway and must not be enabled.

## Sources (Portkey documentation, read 2026-10-25)

- Model Catalog (`@provider_slug/model` without a Config): <https://portkey.ai/docs/product/model-catalog>
- Python SDK (`PORTKEY_API_KEY` read from the environment; `config` parameter): <https://portkey.ai/docs/api-reference/sdk/python>
- Default Configs on API keys (applied when a request specifies none): <https://portkey.ai/docs/product/administration/enforce-default-config>
- Automatic retries (default codes, up to 5 attempts, backoff, `use_retry_after_headers`, 60 s cap): <https://portkey.ai/docs/product/ai-gateway/automatic-retries>
- Fallbacks (default: any non-2xx; `on_status_codes`): <https://portkey.ai/docs/product/ai-gateway/fallbacks>
