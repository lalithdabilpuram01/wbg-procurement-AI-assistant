# Intent router

The router decides which handler may serve a message, and with which permissions.

```
user message
  → redaction
  → NeMo input guardrails (blocked → stored, flagged, nothing else runs)
  → follow-up resolution (followups.py)
  → IntentRouter.route()
  → exactly one handler
```

| Route | Handler | May touch |
|---|---|---|
| `conversation` | `ConversationResponder` (`responder.py`) | Recent stored turns of this thread |
| `conversation_memory` | `recall_memory` + responder | This owner's stored turns, decisions, evidence ids and the valid summary for the current topic |
| `structured_data` | Registered runner (NL2SQL workflow) | Registry search, Claude, SQLGlot validator, gateway; all workflow guardrails unchanged |
| `document_rag` | Registered runner (none yet) | `route_unavailable` until connected |
| `ml_prediction` | Registered runner (none yet) | `route_unavailable` until connected |
| `procurement_planning` | Registered runner (none yet) | `route_unavailable` until connected |
| `clarification` | None: a question back | Nothing |

## Files

| File | What it holds |
|---|---|
| `routes.py` | `Route`, `ReasonCategory`, `ROUTE_POLICIES` (the only source of permissions) and `RoutingDecision` |
| `rules.py` | Deterministic signals: conversational clauses, memory recall, evidence recheck, definitions, task phrases and tool directives |
| `router.py` | `IntentRouter`: the ordered rules, conversation-aware inheritance and the classifier fallback |
| `classifier.py` | `IntentClassifier` protocol, `ClassifierInput` and `ClassifierOutput`: the plug-in point for a future model |

## Isolation guarantees

- **Permissions come only from `ROUTE_POLICIES`.** Every decision is built through `routes.decision()`, so a message or
  a classifier can never widen a route's tools.
- **Runners exist only for task routes.** The service refuses runners registered for conversation routes. A task route
  without a runner returns `route_unavailable`; it never falls back to another agent.
- **Messages cannot choose their own tools or routes.** Naming an MCP tool or a route, or asking to "use/route to/switch
  to" an agent or tool, gives `clarification` (`tool_directive_refused`).
- **Ambiguous referents always clarify.** If the resolver cannot resolve a referent, a task route is replaced by
  `clarification`. The only exceptions are a topic reset and an explicit "recheck the data" request.
- **Model-backed replies need live-call approval.** A responder with `uses_live_model=True` requires a `LiveAccess`
  grant from the existing dual opt-in. No model responder exists yet.
- **Logs hold no message text.** Routing metadata in the audit trail and Logfire is limited to route, reason, confidence
  level and basis, permissions, reset flag, inherited route and decider.

## Confidence

- **Rules:** `confidence_basis="deterministic_rule"` (or `"conversation_inheritance"`), with a heuristic level
  (`high`/`medium`), `score=None` and `calibrated=False`.
- **Classifier:** `confidence_basis="classifier_score"` and its score. `calibrated=True` is set only when the classifier
  declares `score_kind="calibrated_probability"`.

No probability is invented.

## Plugging in a routing model later

Implement `IntentClassifier` (`name` and `classify(ClassifierInput) -> ClassifierOutput`) and pass it as
`IntentRouter(classifier=..., min_classifier_score=...)`. Nothing downstream changes.

How the classifier is used:
- It is called only when no deterministic rule matched.
- It receives the redacted text (truncated to 1,000 characters), the previous task route and the follow-up kind; never
  memory, ids or credentials.
- A score below the minimum, a `clarification` proposal or an exception gives `clarification`.
- It cannot override the tool-directive rule, the ambiguity rule or the permissions table.

## Limitations

- **The rules are English and phrase-based.** Paraphrases outside the listed patterns go to the classifier, or to
  clarification when there is none.
- **Mixed requests are not split.** Two different task requests in one message give a clarification.
- **Recall covers the current topic only.** Earlier topics in the thread are not recalled after a topic change.
- **`OfflineResponder` is a placeholder.** It gives fixed acknowledgements and states that general explanations need the
  (not yet enabled) conversational model.
- **Input size floor lowered.** The input policy minimum is now any non-blank message (it was 3 characters), so short
  chat such as "Hi" reaches the router instead of being blocked.
