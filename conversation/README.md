# Conversation service: context engineering, compression and memory

This service turns a user message in an ongoing conversation into one explicit, safe request for an agent. It is
reusable by the Structured Data agent today and, later, by Document RAG, Predictive ML and a LangGraph supervisor. It
calls no models: follow-up resolution and summarization are deterministic.

## Where each part is implemented

| Concern | Module | Main entry point |
|---|---|---|
| Records (threads, turns, decisions, summaries, evidence) | `models.py` | `Turn`, `TurnMetadata`, `SummaryRecord`, `Decision` |
| Persistence contract and in-memory store | `store.py` | `ConversationStore`, `InMemoryConversationStore` |
| Durable PostgreSQL store | `postgres_store.py`, `sql/schema.sql`, `sql/bootstrap_roles.sql`, `init_store.py` | `PostgresConversationStore` |
| **Context engineering**: follow-up resolution | `followups.py` | `FollowUpResolver.resolve` |
| **Context engineering**: context selection and rendering | `context_builder.py` | `ContextBuilder.build` |
| **Memory optimization**: token budget | `tokens.py` | `ContextBudget`, `ApproxTokenCounter` |
| **Compression**: summaries, loss check, staleness | `compression.py` | `Compressor.compress`, `stale_reason` |
| What may be stored (redaction, metadata only) | `redaction.py` | `redact_secrets`, `result_metadata` |
| Orchestration of one message | `service.py` | `ConversationService.handle_message` |
| Intent routing and route isolation | `routing/` (see `routing/README.md`) | `IntentRouter.route` |
| Conversation replies and memory recall | `responder.py` | `ConversationResponder`, `recall_memory` |
| Contracts for future agents | `contracts.py` | `CheckpointStore`, `LongTermMemoryRetriever`, `PredictionContext`, `SavedResponse`, `Workspace` |
| Country names | `vocabulary.py` | `cohort_vocabulary()` (from the 2,000-project cohort manifest) |

Changes outside this package:
- `structured_agent/nl2sql/workflow.py`: `run(..., conversation_context=None)`. The workflow drops memory that still
  contains instruction-like text and audits that as `app.conversation_memory`.
- `structured_agent/nl2sql/sql_generator.py`: the memory block goes between the registry metadata and the `QUESTION`.
  The system prompt treats it as untrusted.
- `structured_agent/guardrails/policies.py`: new `contains_instruction_like_text`, which uses the same patterns as
  retrieved-context sanitising.
- `observability.py` and `tracing.py`: allow-listed conversation attributes (ids, counts and statuses only).

## Message flow (`ConversationService.handle_message`)

1. **Ownership.** The trusted `Owner(tenant_id, user_id)` comes from authentication. Another owner's thread raises
   `ThreadNotFound`, the same error as a missing thread.
2. **Redaction.** Secret-shaped values are replaced using the patterns of the NeMo regex rail. Stored text is capped at
   4,000 characters. Follow-up resolution also runs on the redacted text, so secrets never reach context or prompts.
3. **Guardrails input check** on the raw message. This uses the NeMo rails in production; the service refuses to start
   without a guard. A blocked message is stored redacted, flagged `blocked=True`, and never used as memory.
4. **Follow-up resolution.** This gives the turn kind, the updated active filters, a standalone question, and the
   route: `structured`, `document_rag` or `ml_prediction`.
5. **Early outcomes:** `clarification_required`, `topic_reset`, or `route_unavailable` when no agent is connected for
   the route. Only `structured` is connected today.
6. **Context build** within the token budget. If the mandatory items do not fit, the status is `context_over_budget`.
7. **Agent call.** The structured runner calls `NlToSqlWorkflow.run(standalone_question, execute, request_id,
   conversation_context)`. All workflow guardrails, the SQLGlot validator, MCP tool gates and the dual live-call opt-in
   are unchanged. Execution happens only when `execute=True`.
8. **Assistant turn.** Only metadata is stored: status, failure class, validated SQL, executed flag, row count, tables,
   and an evidence reference (validation id). Rows, columns and parameter values are never stored.
9. **Compression and staleness**, then audit and trace events with ids, counts and statuses only.

## Conversation persistence design

- **Location.** A separate `conversation` schema in the existing local `wbg_structured_registry` database. It has no
  foreign keys or grants to `registry`, and `init_registry` (which recreates only `registry`) never touches it. The
  schema is non-destructive: only `CREATE ... IF NOT EXISTS`, and re-applying it is a no-op.
- **Tables.**
  - `threads`: opaque UUID, tenant, user, title, `topic_epoch`, timestamps.
  - `turns`: append-only, `UNIQUE(thread_id, seq)`, with the `context` and `metadata` JSONB columns.
  - `decisions`: user-approved decisions and constraints, revocable, never deleted.
  - `summaries`: versioned, `UNIQUE(thread_id, version)`; invalidated, never edited.
- **Roles.** These were created by `bootstrap_roles.sql` (additive, no passwords):
  - `wbg_conversation_admin` owns the schema.
  - `wbg_conversation_app` is the runtime role. It has SELECT and INSERT, plus UPDATE only on `threads(title,
    topic_epoch, updated_at)`, `decisions(revoked_at, revoked_reason)` and `summaries(invalidated_at,
    invalidation_reason)`. It has no DELETE, no DDL, and no access to the `registry` schema.
- **Isolation, enforced twice.** Every query filters by tenant and user. Row-level security also limits the runtime
  role to the owner set for the transaction (`wbg.tenant_id`, `wbg.user_id`). Without an owner it sees no rows.
- **Setup.** DSNs are read from `WBG_CONVERSATION_DSN` and `WBG_CONVERSATION_ADMIN_DSN`; the schema name from
  `WBG_CONVERSATION_SCHEMA` (default `conversation`). Set up with:

  ```
  psql -d wbg_structured_registry -f conversation/sql/bootstrap_roles.sql
  python -m conversation.init_store
  ```

## Context-building algorithm (`ContextBuilder.build`)

1. **Mandatory items.** These are the resolved question, the precedence notice, the active filters with stated
   assumptions, and all active approved decisions. Their cost, the section-title cost and the end marker are charged
   first. If they exceed the budget, the result is `over_budget`; nothing is truncated silently.
2. **Optional items**, each added only if it fits, in this order:
   1. Recent turns: a sliding window of 6 turns, offered newest first and rendered oldest first.
   2. Verified result references: validation ids, tables and row counts.
   3. The latest summary, if it is valid and not stale.
   4. If no summary is usable, the fallback: preserved facts copied from the original older turns, then older user
      turns ranked by entity overlap (×2) plus content-word overlap with the question.
3. **Pruning.** Blocked turns, earlier topic epochs, repeated user questions (the newer copy is kept) and older turns
   with zero overlap are pruned. Any memory item with instruction-like text is replaced by
   `[removed: instruction-like text]`.
4. **Rendering.** Labelled sections go between `BEGIN/END CONVERSATION MEMORY`:
   - "APPROVED DECISIONS AND CONSTRAINTS";
   - "VERIFIED RESULT REFERENCES";
   - "EARLIER CONVERSATION (generated summary, NOT verified evidence)".

   The precedence notice says that the current question and the system rules always win, and that memory cannot change
   rules, permissions, tools or the request.

Registry metadata is not copied into memory. The workflow retrieves it through the authorized MCP tools, and the
budget reserves room for it.

## Memory and compression strategy

- **Separation of records.** Original turns are append-only. Summaries live in their own table, linked by the turn
  range they cover (`covers_from_seq` to `covers_through_seq`) and by `inputs_sha256`.
- **When summaries are made.** Turns of the current topic outside the recent window are summarized once at least 4
  exist. A newer summary is a new version; nothing is overwritten.
- **Summary content.** `StructuredSummary` holds lists of:
  - questions, countries, fiscal years and project ids;
  - decision ids, evidence ids and citations;
  - outcomes and corrections;
  - a one-line narrative.

  The deterministic summarizer builds it from turn metadata. Tests use a lossy `FakeSummarizer`.
- **Loss check.** A summary must contain every project id, evidence id, citation, active decision id and current
  country of the turns it covers. If anything is missing, it is stored as invalid (`compression_loss`) and the builder
  falls back to preserved facts and original turns, with `degraded=True`.
- **Staleness.** A summary is invalidated, explicitly and with a reason, on any of these:
  - `topic_reset`;
  - `corrected_entity` (it mentions a country a later correction replaced);
  - `decision_revoked`;
  - `registry_changed` (a different registry build id);
  - `expired` (older than 30 days).

  Regenerated summaries leave out corrected countries and record the correction.
- **Labelling.** Summaries are always labelled as generated and unverified. Only `EvidenceReference`s from the
  validator and execution gateway are called verified.

## Follow-up examples (the required scenario)

| Turn | Message | Kind | Active filters | Standalone question / outcome |
|---|---|---|---|---|
| 1 | Show procurement activity in Kenya. | question | KE | Show procurement activity in Kenya. |
| 2 | Only include fiscal year 2024. | refinement | KE, FY2024 | Show procurement activity for Kenya, in fiscal year 2024. |
| 3 | Now compare that with Uganda. | comparison | KE+UG, FY2024, by country | Show procurement activity for Kenya and Uganda, in fiscal year 2024, compared by country. |
| 4 | What did we learn from those projects? | reference | KE+UG, FY2024 | What did we learn from the projects for Kenya and Uganda, in fiscal year 2024, compared by country? → `route_unavailable` (document RAG is not connected yet) |
| 5 | Remove the year filter. | filter_removal | KE+UG | What did we learn from the projects for Kenya and Uganda, compared by country? → `route_unavailable` |

Other handled cases:
- Correction: "I meant Tanzania, not Uganda" gives KE+TZ.
- Topic reset: "New topic" or "Start over".
- Adding a country: "Also add Tanzania".
- Bare years such as "in 2023" are read as fiscal year 2023, and that assumption is stated.
- Project ids (`P123456`) are tracked.

Clarification is returned, without guessing, for:
- ambiguous names ("Congo");
- "that country" when two countries are active;
- references, refinements or comparisons with nothing earlier to apply them to;
- removing a filter that is not active;
- very short messages.

## Token-budgeting approach

`ContextBudget` has these defaults:

| Part | Tokens |
|---|---|
| total | 12,000 |
| reserved: instructions | 1,500 |
| reserved: tool results | 6,000 |
| reserved: output | 1,200 |
| safety margin | 300 |
| **left for the question and memory** | **3,000** |

`ApproxTokenCounter` counts about 3.5 characters per token plus 4 tokens per item, rounded up. It is deterministic,
needs no tokenizer download or API call, and deliberately over-counts slightly. Tests check, at six budget sizes, that
the independently measured rendered block never exceeds the budget.

## Privacy and isolation controls

- Owners come from trusted identity only. Isolation is enforced by query filters and by PostgreSQL row-level security.
  Tests cover another user in the same tenant, the same user id in another tenant, threads of the same user, and
  non-UUID ids.
- Topic epochs keep earlier topics out of memory.
- Nothing stored contains API keys or credentials (redacted), result rows, columns or SQL parameter values.
- Audit and trace events carry ids, counts and statuses only: no question text, SQL or rows.
- Blocked inputs are never reused. Decisions with instruction-like wording are refused.
- Memory is sanitised in the builder and screened again in the workflow, where a block that still contains
  instruction-like text is dropped and audited.

## Evaluation criteria (all checked by offline tests in `conversation/tests/`)

| Criterion | Measure |
|---|---|
| Follow-up resolution | The five-turn scenario gives the exact kinds, filters and standalone questions; new questions inherit no filters |
| Ambiguity | 9 ambiguous cases return `clarification_required` with options and no agent call |
| Compression quality | The summary keeps 100% of project ids, evidence ids, citations, decision ids and countries; lossy summaries are rejected |
| Budget compliance | The measured rendered block is ≤ the budget at every tested size; mandatory overflow is reported |
| Decision preservation | Decisions are present verbatim in every context; revocation removes them and invalidates summaries |
| Staleness | All six invalidation reasons are detected |
| Isolation | Cross-user, cross-tenant and cross-thread access fails; RLS hides rows without filters |
| Agent safety | Memory reaches Claude only as a labelled block; tools stay within the stage allow-lists; no execution unless requested; poisoned memory is dropped |
| Offline | No external socket, httpx or requests traffic (local PostgreSQL only) |

## Limitations

- **Deterministic resolution covers explicit, common phrasings only.** Indirect references ("the second one", "the
  neighbouring country"), pronouns across several turns, and non-country entities other than project ids and fiscal
  years are not understood. When the cues are missing the turn is treated as a new question; when they are present but
  not unique, clarification is returned.
- **Filter removal applies to the most recent question.** After turn 4 that is the lessons-learned (document) question.
- **The token counter is an approximation.** Swap in a provider tokenizer behind `TokenCounter` before tightening
  budgets.
- **Summaries are structured, not abstractive.** An LLM summarizer can be plugged in through `Summarizer`, but must
  pass the same loss check.
- **Instruction screening is pattern-based**, with the same limits as the guardrails (see
  `structured_agent/guardrails/README.md`).
- **No long-term cross-thread memory, checkpoints, saved responses or workspaces yet.** Only their contracts exist.
