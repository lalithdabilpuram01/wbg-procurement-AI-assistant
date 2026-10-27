"""Conversation service: one entry point that turns a user message into a safe, context-aware agent request.

Per message:
  1. ownership check (trusted owner from authentication; another owner's thread looks like a missing thread);
  2. secret redaction of the text that will be stored;
  3. guardrails input check on the raw message (blocked turns are stored, flagged and never reused as memory);
  4. deterministic follow-up resolution -> kind, active filters, standalone question (or a clarification);
  5. intent routing (routing/) -> exactly one handler, with permissions from ROUTE_POLICIES:
       clarification        -> a question back; nothing else runs;
       conversation(_memory)-> the conversation responder with the owner's stored conversation only;
       task routes          -> the specialist runner registered for that route, or `route_unavailable`;
  6. for task routes: context building within the token budget, then the runner (only the structured NL2SQL
     workflow exists today), which keeps all of its guardrails, validator and MCP authorization;
  7. the assistant turn is stored with metadata only (status, validated SQL, evidence ids; never rows);
  8. compression of older turns and stale-summary invalidation;
  9. audit/trace events with ids, counts, statuses and routing metadata only (no text, SQL or rows).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

from conversation.compression import Compressor, DeterministicSummarizer, stale_reason
from conversation.context_builder import ContextBuilder, ContextPackage
from conversation.followups import FollowUpResolver, Resolution, strip_reset_phrase
from conversation.models import ActiveContext, Decision, Owner, ThreadInfo, Turn, TurnKind, TurnMetadata
from conversation.replies import guard_reply
from conversation.redaction import redact_secrets, result_metadata
from conversation.responder import (ConversationRequest, ConversationResponder, ConversationResponse, OfflineResponder,
                                    recall_memory)
from conversation.routing import CONVERSATIONAL_ROUTES, TASK_ROUTES, IntentRouter, ReasonCategory, Route, RouteRequest
from conversation.routing.routes import RoutingDecision
from conversation.store import ConversationStore
from structured_agent.guardrails.policies import contains_instruction_like_text
from structured_agent.nl2sql.live_guard import LiveAccess, LiveCallsDisabled

TITLE_CHARS = 80
DEFAULT_TITLE = "New conversation"
TASK_ROUTE_VALUES = frozenset(r.value for r in TASK_ROUTES)


class AgentRunner(Protocol):
    """Runs one resolved question for a route. Returns a WorkflowResult-like object (see redaction.result_metadata)."""

    def __call__(self, question: str, conversation_context: str, execute: bool, request_id: str) -> Any: ...


class InputGuard(Protocol):
    def check_input(self, question: str) -> Any: ...          # returns a GuardDecision


class EventSink(Protocol):
    def event(self, name: str, level: str = "info", **attributes: Any) -> None: ...


def structured_runner(workflow: Any) -> AgentRunner:
    """Adapter for NlToSqlWorkflow.run (guardrails, validator and MCP authorization stay inside the workflow)."""
    def run(question: str, conversation_context: str, execute: bool, request_id: str) -> Any:
        return workflow.run(question, execute=execute, request_id=request_id, conversation_context=conversation_context)
    return run


@dataclass(frozen=True)
class ConversationState:
    active: ActiveContext
    previous_task_route: Route | None


@dataclass
class ConversationReply:
    thread_id: str
    status: str        # a WorkflowStatus value, or responded | clarification_required | route_unavailable |
                       # context_over_budget | topic_reset | blocked_by_guardrail
    message: str
    kind: TurnKind
    user_turn_seq: int
    assistant_turn_seq: int
    standalone_question: str | None = None
    clarification_options: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    context: ContextPackage | None = None
    agent_result: Any = None                  # returned to the caller only; never persisted
    routing: RoutingDecision | None = None
    response: ConversationResponse | None = None   # conversation routes only


class ConversationService:
    def __init__(self, store: ConversationStore, runners: dict[Route, AgentRunner], guard: InputGuard | None,
                 resolver: FollowUpResolver | None = None, builder: ContextBuilder | None = None,
                 compressor: Compressor | None = None, telemetry: Any = None, tracer: EventSink | None = None,
                 registry_build_id: str | None = None, router: IntentRouter | None = None,
                 responder: ConversationResponder | None = None, live_access: LiveAccess | None = None,
                 text_filter: Callable[[str], str] | None = None):
        if guard is None:
            raise ValueError("the conversation service requires guardrails (fail closed)")
        if any(Route(route) not in TASK_ROUTES for route in runners):
            raise ValueError("specialist runners may be registered only for task routes")
        self._responder = responder or OfflineResponder()
        if self._responder.uses_live_model and not isinstance(live_access, LiveAccess):
            raise LiveCallsDisabled("a model-backed conversation responder needs the existing live-call approval")
        self._store = store
        # applied to every text that is stored or forwarded: secrets redacted (and personal data masked when the host
        # passes conversation.pii.redact_personal_data)
        self.text_filter = text_filter or redact_secrets
        self._runners = {Route(route): runner for route, runner in runners.items()}
        self._router = router or IntentRouter()
        self._guard = guard
        self._resolver = resolver or FollowUpResolver()
        self._builder = builder or ContextBuilder()
        self._compressor = compressor or Compressor(DeterministicSummarizer())
        self._telemetry = telemetry
        self._tracer = tracer
        self._registry_build_id = registry_build_id

    # ------------------------------------------------------------------------------------------------ threads
    def start_thread(self, owner: Owner, title: str | None = None) -> ThreadInfo:
        return self._store.create_thread(owner, self.text_filter(title or DEFAULT_TITLE)[:TITLE_CHARS])

    def reset_topic(self, owner: Owner, thread_id: str) -> ThreadInfo:
        return self._store.start_new_topic(owner, thread_id)

    def history(self, owner: Owner, thread_id: str, limit: int | None = 50) -> list[Turn]:
        return self._store.turns(owner, thread_id, limit=limit)

    def record_decision(self, owner: Owner, thread_id: str, kind: Literal["decision", "constraint"], statement: str,
                        source_turn_seq: int | None = None) -> Decision:
        """Record an explicit, user-approved decision or constraint. Instruction-like statements are refused."""
        if contains_instruction_like_text(statement):
            raise ValueError("a decision cannot change rules, permissions or tools")
        decision = Decision(thread_id=thread_id, kind=kind, statement=self.text_filter(statement),
                            approved_by=owner.user_id, source_turn_seq=source_turn_seq)
        return self._store.add_decision(owner, decision)

    def revoke_decision(self, owner: Owner, thread_id: str, decision_id: str, reason: str) -> Decision:
        return self._store.revoke_decision(owner, thread_id, decision_id, reason[:200])

    # ------------------------------------------------------------------------------------------------ messages
    def route_message(self, owner: Owner, thread_id: str, text: str) -> RoutingDecision | None:
        """The routing decision a message would get, without storing anything; None when the input guard blocks it
        (handle_message then records the block)."""
        thread = self._store.get_thread(owner, thread_id)                     # ownership check first
        if not self._guard.check_input(text).allowed:
            return None
        stored_text = self.text_filter(text)
        state = self._conversation_state(owner, thread)
        return self._router.route(RouteRequest(stored_text, self._resolver.resolve(stored_text, state.active),
                                               state.previous_task_route))

    def memory_for(self, owner: Owner, thread_id: str, question: str) -> str | None:
        """The budgeted conversation-memory block for a request handled outside handle_message (investigations,
        briefs, assessments started by the host): recent turns, active filters, approved decisions and the latest
        usable summary. Call it before the request's own user turn is stored. None when there is nothing to add."""
        thread = self._store.get_thread(owner, thread_id)
        epoch_turns = self._store.turns(owner, thread.thread_id, topic_epoch=thread.topic_epoch)
        decisions = self._store.decisions(owner, thread.thread_id, include_revoked=True)
        summary, problem = self._usable_summary(owner, thread, epoch_turns, decisions)
        active = self._conversation_state(owner, thread).active or ActiveContext()
        package = self._builder.build(self.text_filter(question), active, epoch_turns, [d for d in decisions if d.active],
                                      self._compressor.policy.recent_window_turns, summary, problem)
        self._event("conversation.memory_built", thread_id=thread.thread_id, tokens_used=package.tokens_used,
                    summary_status=package.summary_status, over_budget=package.over_budget)
        if package.over_budget:
            return None
        return package.text or None

    def compact(self, owner: Owner, thread_id: str) -> None:
        """Summarize older turns of the current topic when needed (after a host-handled request)."""
        self._compress(owner, self._store.get_thread(owner, thread_id))

    def handle_message(self, owner: Owner, thread_id: str, text: str, execute: bool = False) -> ConversationReply:
        """User message -> input guardrails -> intent router -> exactly one permitted handler."""
        started = time.perf_counter()
        request_id = "req_" + uuid.uuid4().hex[:16]
        thread = self._store.get_thread(owner, thread_id)                     # ownership check first
        stored_text = self.text_filter(text)
        state = self._conversation_state(owner, thread)

        guard_decision = self._guard.check_input(text)
        if not guard_decision.allowed:
            reply = self._blocked(owner, thread, stored_text, state.active, request_id, guard_decision)
            return self._finish(reply, request_id, started, None)

        resolution = self._resolver.resolve(stored_text, state.active)  # redacted text: secrets never enter context or prompts
        routing = self._router.route(RouteRequest(stored_text, resolution, state.previous_task_route))
        if routing.reset_topic:
            thread = self._store.start_new_topic(owner, thread_id)
            if routing.is_task:
                resolution = self._resolver.resolve(strip_reset_phrase(stored_text), ActiveContext())
        elif routing.reason == ReasonCategory.EVIDENCE_RECHECK:
            resolution = self._resolver.restate(state.active)

        if routing.route == Route.CLARIFICATION or (routing.is_task and resolution.kind == TurnKind.CLARIFICATION_REQUIRED):
            reply = self._clarify(owner, thread, stored_text, state.active, routing, resolution, request_id)
        elif routing.route in CONVERSATIONAL_ROUTES:
            reply = self._converse(owner, thread, stored_text, state.active, routing, request_id)
        else:
            reply = self._run_task(owner, thread, stored_text, routing, resolution, request_id, execute)
        reply.routing = routing
        return self._finish(reply, request_id, started, reply.context)

    # -------------------------------------------------------------------------------------------- route handlers
    def _clarify(self, owner: Owner, thread: ThreadInfo, stored_text: str, active: ActiveContext, routing: RoutingDecision,
                 resolution: Resolution, request_id: str) -> ConversationReply:
        """No agent, no tools: ask. The active context stays as it was."""
        question = routing.clarification or resolution.clarification or "Could you say that more fully?"
        options = list(routing.clarification_options or resolution.clarification_options)
        user_turn = self._append(owner, thread, "user", TurnKind.CLARIFICATION_REQUIRED, stored_text, active,
                                 self._metadata(request_id, routing, Route.CLARIFICATION, clarification_options=options))
        assistant = self._append(owner, thread, "assistant", TurnKind.ANSWER, question, active,
                                 self._metadata(request_id, routing, Route.CLARIFICATION, status="clarification_required"))
        return ConversationReply(thread.thread_id, "clarification_required", question, TurnKind.CLARIFICATION_REQUIRED,
                                 user_turn.seq, assistant.seq, clarification_options=options)

    def _converse(self, owner: Owner, thread: ThreadInfo, stored_text: str, active: ActiveContext, routing: RoutingDecision,
                  request_id: str) -> ConversationReply:
        """Conversation routes read only the owner's stored conversation; no retrieval, MCP tool or specialist agent."""
        context = ActiveContext() if routing.reset_topic else active
        kind = TurnKind.TOPIC_RESET if routing.reset_topic else TurnKind.CONVERSATION
        user_turn = self._append(owner, thread, "user", kind, stored_text, context, self._metadata(request_id, routing))
        package = self._memory_context(owner, thread, user_turn, stored_text, context)
        recall = recall_memory(self._store, owner, thread) if routing.route == Route.CONVERSATION_MEMORY else None
        response = self._responder.respond(ConversationRequest(message=stored_text, route=routing.route, reason=routing.reason,
                                                               memory_context=package.text, recall=recall))
        status = "topic_reset" if routing.reset_topic else "responded"
        assistant = self._append(owner, thread, "assistant", TurnKind.ANSWER, self.text_filter(response.text), context,
                                 self._metadata(request_id, routing, status=status))
        return ConversationReply(thread.thread_id, status, response.text, kind, user_turn.seq, assistant.seq,
                                 context=package, response=response)

    def _run_task(self, owner: Owner, thread: ThreadInfo, stored_text: str, routing: RoutingDecision,
                  resolution: Resolution, request_id: str, execute: bool) -> ConversationReply:
        """Only the specialist registered for the decided route may run; there is no fallback to another agent."""
        user_turn = self._append(owner, thread, "user", resolution.kind, stored_text, resolution.context,
                                 self._metadata(request_id, routing, standalone_question=resolution.standalone_question,
                                                resolution_notes=resolution.notes))
        if thread.title == DEFAULT_TITLE and resolution.kind == TurnKind.QUESTION:
            self._store.set_title(owner, thread.thread_id, stored_text[:TITLE_CHARS])

        runner = self._runners.get(routing.route)
        if runner is None or not routing.external_tools_allowed:
            message = (f"This request needs the '{routing.route.value}' capability, which is not connected yet. "
                       f"Resolved question: {resolution.standalone_question}")
            return self._reply_without_agent(owner, thread, user_turn, routing, resolution, "route_unavailable", message,
                                             request_id)

        package = self._build_context(owner, thread, user_turn, resolution)
        if package.over_budget:
            message = ("The approved decisions and filters of this conversation exceed the context budget. "
                       "Please revoke some decisions or start a new conversation.")
            reply = self._reply_without_agent(owner, thread, user_turn, routing, resolution, "context_over_budget", message,
                                              request_id)
            reply.context = package
            return reply

        result = runner(resolution.standalone_question, package.text, execute, request_id)
        metadata = result_metadata(result, route=routing.route.value)
        metadata.request_id, metadata.registry_build_id = request_id, self._registry_build_id
        assistant = self._append(owner, thread, "assistant", TurnKind.ANSWER, self.text_filter(result.message),
                                 resolution.context, metadata)
        self._compress(owner, thread)
        return ConversationReply(thread.thread_id, result.status.value, result.message, resolution.kind, user_turn.seq,
                                 assistant.seq, resolution.standalone_question, list(result.clarification_options),
                                 resolution.notes, package, result)

    # ---------------------------------------------------------------------------------------------- internals
    def _conversation_state(self, owner: Owner, thread: ThreadInfo) -> ConversationState:
        """Active filters and the task route of the latest usable user turn of the current topic.
        Blocked and clarification turns are ignored; conversation turns carry the active filters unchanged."""
        active, previous_route = None, None
        for turn in reversed(self._store.turns(owner, thread.thread_id, topic_epoch=thread.topic_epoch)):
            if turn.role != "user" or turn.metadata.blocked or turn.kind == TurnKind.CLARIFICATION_REQUIRED:
                continue
            active = active or turn.context
            if turn.metadata.route in TASK_ROUTE_VALUES:
                previous_route = Route(turn.metadata.route)
                break
        return ConversationState(active or ActiveContext(), previous_route)

    def _memory_context(self, owner: Owner, thread: ThreadInfo, user_turn: Turn, message: str,
                        active: ActiveContext) -> ContextPackage:
        """Recent conversation memory for the responder (stored turns and decisions only; no summary, no retrieval)."""
        epoch_turns = [t for t in self._store.turns(owner, thread.thread_id, topic_epoch=thread.topic_epoch)
                       if t.seq < user_turn.seq]
        return self._builder.build(message, active, epoch_turns, self._store.decisions(owner, thread.thread_id),
                                   self._compressor.policy.recent_window_turns, None)

    @staticmethod
    def _metadata(request_id: str, routing: RoutingDecision, route: Route | None = None, **fields: Any) -> TurnMetadata:
        return TurnMetadata(request_id=request_id, route=(route or routing.route).value, route_reason=routing.reason.value,
                            **fields)

    def _build_context(self, owner: Owner, thread: ThreadInfo, user_turn: Turn, resolution: Resolution) -> ContextPackage:
        epoch_turns = [t for t in self._store.turns(owner, thread.thread_id, topic_epoch=thread.topic_epoch)
                       if t.seq < user_turn.seq]
        decisions = self._store.decisions(owner, thread.thread_id, include_revoked=True)
        summary, problem = self._usable_summary(owner, thread, epoch_turns, decisions)
        return self._builder.build(resolution.standalone_question, resolution.context, epoch_turns,
                                   [d for d in decisions if d.active], self._compressor.policy.recent_window_turns,
                                   summary, problem)

    def _usable_summary(self, owner: Owner, thread: ThreadInfo, epoch_turns: list[Turn], decisions: list[Decision]):
        latest = self._store.latest_summary(owner, thread.thread_id)
        if latest is None:
            return None, None
        reason = stale_reason(latest, thread, epoch_turns, decisions, self._registry_build_id,
                              self._compressor.policy.max_summary_age)
        if reason is None:
            return latest, None
        if latest.valid:
            self._store.invalidate_summary(owner, thread.thread_id, latest.summary_id, reason)   # explicit invalidation
        return None, reason

    def _compress(self, owner: Owner, thread: ThreadInfo) -> None:
        epoch_turns = self._store.turns(owner, thread.thread_id, topic_epoch=thread.topic_epoch)
        decisions = self._store.decisions(owner, thread.thread_id, include_revoked=True)
        latest = self._store.latest_summary(owner, thread.thread_id)
        if latest and latest.valid and stale_reason(latest, thread, epoch_turns, decisions, self._registry_build_id,
                                                    self._compressor.policy.max_summary_age):
            self._store.invalidate_summary(owner, thread.thread_id, latest.summary_id, "stale_before_compression")
            latest = self._store.latest_summary(owner, thread.thread_id)
        outcome = self._compressor.compress(thread, epoch_turns, decisions, latest, self._registry_build_id)
        if outcome.record is not None and outcome.status in ("created", "compression_loss"):
            self._store.add_summary(owner, outcome.record)
            self._event("conversation.compression", thread_id=thread.thread_id, summary_version=outcome.record.version,
                        summary_status=outcome.status, items_dropped=len(outcome.missing))

    def _blocked(self, owner: Owner, thread: ThreadInfo, stored_text: str, active: ActiveContext, request_id: str,
                 decision: Any) -> ConversationReply:
        reason = decision.reason_category.value if decision.reason_category else "blocked"
        reply = guard_reply(reason)                      # a plain answer for every reason, never a bare refusal
        # The text is stored (redacted) for audit, flagged when it was a policy block, and never used as memory; the
        # active context is unchanged. No agent runs.
        metadata = TurnMetadata(request_id=request_id, blocked=reply.blocked, guardrail_reason=reason, status=reply.status)
        user_turn = self._append(owner, thread, "user", TurnKind.QUESTION, stored_text, active, metadata)
        assistant = self._append(owner, thread, "assistant", TurnKind.ANSWER, reply.message, active, metadata)
        return ConversationReply(thread.thread_id, reply.status, reply.message, TurnKind.QUESTION, user_turn.seq, assistant.seq)

    def _reply_without_agent(self, owner: Owner, thread: ThreadInfo, user_turn: Turn, routing: RoutingDecision,
                             resolution: Resolution, status: str, message: str, request_id: str) -> ConversationReply:
        assistant = self._append(owner, thread, "assistant", TurnKind.ANSWER, message, resolution.context,
                                 self._metadata(request_id, routing, status=status))
        return ConversationReply(thread.thread_id, status, message, resolution.kind, user_turn.seq, assistant.seq,
                                 resolution.standalone_question, resolution.clarification_options, resolution.notes)

    def _append(self, owner: Owner, thread: ThreadInfo, role: Literal["user", "assistant"], kind: TurnKind, text: str,
                context: ActiveContext, metadata: TurnMetadata) -> Turn:
        seq = self._store.next_seq(owner, thread.thread_id)
        current = self._store.get_thread(owner, thread.thread_id)
        turn = Turn(thread_id=thread.thread_id, seq=seq, role=role, kind=kind, text=text, topic_epoch=current.topic_epoch,
                    context=context, metadata=metadata)
        return self._store.append_turn(owner, turn)

    def _finish(self, reply: ConversationReply, request_id: str, started: float, package: ContextPackage | None) -> ConversationReply:
        attributes = {"thread_id": reply.thread_id, "turn_seq": reply.user_turn_seq, "resolution": reply.kind.value,
                      "status": reply.status, "duration_ms": round(1000 * (time.perf_counter() - started))}
        if reply.routing is not None:
            attributes.update(reply.routing.log_attributes())
        if package is not None:
            attributes.update(context_tokens=package.tokens_used, context_budget=package.budget_tokens,
                              items_included=len(package.included), items_dropped=len(package.dropped),
                              summary_version=package.summary_version, summary_status=package.summary_status,
                              degraded=package.degraded)
        if self._telemetry is not None:
            self._telemetry.audit(request_id, "inv_" + uuid.uuid4().hex[:16], "conversation.turn", **attributes)
        self._event("conversation.turn", request_id=request_id, **attributes)
        return reply

    def _event(self, name: str, **attributes: Any) -> None:
        if self._tracer is not None:
            self._tracer.event(name, **attributes)
