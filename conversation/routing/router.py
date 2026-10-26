"""Intent router: deterministic fast path, conversation-aware follow-ups, optional pluggable classifier.

Order of rules (first match wins):
  1. empty message                          -> clarification
  2. tries to choose tools/agents/routes    -> clarification (never grants anything)
  3. every clause conversational            -> conversation
  4. topic reset                            -> conversation (+ reset), or the task named after the reset phrase
  5. memory recall                          -> conversation_memory
     "recheck/verify against the data"      -> previous task route (re-run on the active filters)
  6. definition question, no entities       -> conversation
  7. specific task phrases                  -> that task route; two different task routes -> clarification
  8. follow-up cue (refine/compare/...)     -> previous task route, if there is one
  9. follow-up that the resolver could not resolve -> clarification
 10. general question, no entities          -> conversation
 11. otherwise                              -> classifier (if configured and confident) or clarification
A task route is never kept when the resolver found the referent ambiguous.
"""
from __future__ import annotations

from dataclasses import dataclass

from conversation.followups import Resolution, strip_reset_phrase
from conversation.models import TurnKind
from conversation.routing import rules
from conversation.routing.classifier import MAX_CLASSIFIER_CHARS, ClassifierInput, IntentClassifier
from conversation.routing.routes import ReasonCategory, Route, RoutingDecision, decision

FOLLOW_UP_KINDS = frozenset({TurnKind.REFINEMENT, TurnKind.COMPARISON, TurnKind.REFERENCE, TurnKind.FILTER_REMOVAL,
                             TurnKind.CORRECTION})
ROUTE_LABELS = {Route.STRUCTURED_DATA: "procurement data", Route.DOCUMENT_RAG: "procurement documents",
                Route.ML_PREDICTION: "a prediction", Route.PROCUREMENT_PLANNING: "procurement planning"}


@dataclass(frozen=True)
class RouteRequest:
    text: str                                   # the redacted message
    resolution: Resolution                      # deterministic follow-up resolution of the message
    previous_task_route: Route | None = None    # task route of the latest usable turn of the current topic


class IntentRouter:
    def __init__(self, classifier: IntentClassifier | None = None, min_classifier_score: float = 0.75):
        if not 0.0 < min_classifier_score <= 1.0:
            raise ValueError("min_classifier_score must be in (0, 1]")
        self._classifier = classifier
        self._min_score = min_classifier_score

    def route(self, request: RouteRequest) -> RoutingDecision:
        text = rules.normalize(request.text)
        candidate = self._rules(text, request) or self._classify(text, request)
        rerun = candidate.reset_topic or candidate.reason == ReasonCategory.EVIDENCE_RECHECK
        if candidate.is_task and request.resolution.kind == TurnKind.CLARIFICATION_REQUIRED and not rerun:
            return self._unresolved_follow_up(request)
        return candidate

    # ------------------------------------------------------------------------------------------------- rules
    def _rules(self, text: str, request: RouteRequest) -> RoutingDecision | None:
        if not text:
            return _clarify(ReasonCategory.EMPTY_MESSAGE, "Please type a question or message.")
        if rules.is_tool_directive(text):
            return _clarify(ReasonCategory.TOOL_DIRECTIVE_REFUSED,
                            "Tools and agents are chosen by the platform, not by the message. What would you like to know?",
                            signals=("tool_directive",))
        if (reason := rules.conversational_reason(text)) is not None:
            return decision(Route.CONVERSATION, reason, "high", "deterministic_rule", matched_signals=(reason.value,))
        if request.resolution.kind == TurnKind.TOPIC_RESET:
            return self._topic_reset(strip_reset_phrase(text))
        if rules.needs_evidence_recheck(text):
            return self._recheck(request.previous_task_route)
        if rules.is_memory_recall(text):
            return self._memory_recall(text)
        if rules.is_strong_definition(text) and not rules.mentions_entities(text):
            return decision(Route.CONVERSATION, ReasonCategory.CONCEPTUAL_QUESTION, "high", "deterministic_rule",
                            matched_signals=("definition",))
        if (explicit := _explicit_task(text)) is not None:
            return explicit
        if request.resolution.kind in FOLLOW_UP_KINDS:
            return self._inherit(request)
        if request.resolution.kind == TurnKind.CLARIFICATION_REQUIRED:
            return self._unresolved_follow_up(request)
        if rules.is_weak_definition(text) and not rules.mentions_entities(text):
            return decision(Route.CONVERSATION, ReasonCategory.CONCEPTUAL_QUESTION, "medium", "deterministic_rule",
                            matched_signals=("general_question",))
        return None

    @staticmethod
    def _topic_reset(remainder: str) -> RoutingDecision:
        explicit = _explicit_task(remainder) if remainder else None
        if explicit is not None:
            return explicit.model_copy(update={"reset_topic": True,
                                               "matched_signals": ("topic_reset",) + explicit.matched_signals})
        return decision(Route.CONVERSATION, ReasonCategory.TOPIC_CHANGE, "high", "deterministic_rule", reset_topic=True,
                        matched_signals=("topic_reset",))

    @staticmethod
    def _recheck(previous: Route | None) -> RoutingDecision:
        """Remembered results are not evidence: re-verifying them goes back to the agent that produced them."""
        if previous is None:
            return _clarify(ReasonCategory.EVIDENCE_RECHECK, "Which data should be rechecked?", signals=("evidence_recheck",))
        return decision(previous, ReasonCategory.EVIDENCE_RECHECK, "medium", "conversation_inheritance",
                        inherited_from=previous, matched_signals=("evidence_recheck",))

    @staticmethod
    def _memory_recall(text: str) -> RoutingDecision:
        if rules.task_signals(text):
            return _mixed([Route.CONVERSATION_MEMORY, *rules.task_signals(text)])
        return decision(Route.CONVERSATION_MEMORY, ReasonCategory.MEMORY_RECALL, "high", "deterministic_rule",
                        matched_signals=("memory_recall",))

    @staticmethod
    def _inherit(request: RouteRequest) -> RoutingDecision:
        previous = request.previous_task_route
        if previous is None:
            return _clarify(ReasonCategory.AMBIGUOUS_REFERENT,
                            "There is no earlier task to continue; please ask the full question.",
                            signals=(request.resolution.kind.value,))
        return decision(previous, ReasonCategory.FOLLOW_UP_INHERITED, "medium", "conversation_inheritance",
                        inherited_from=previous, matched_signals=(request.resolution.kind.value,))

    @staticmethod
    def _unresolved_follow_up(request: RouteRequest) -> RoutingDecision:
        resolution = request.resolution
        return _clarify(ReasonCategory.AMBIGUOUS_REFERENT, resolution.clarification or "Could you say that more fully?",
                        options=tuple(resolution.clarification_options), signals=("unresolved_reference",))

    # -------------------------------------------------------------------------------------------- classifier
    def _classify(self, text: str, request: RouteRequest) -> RoutingDecision:
        if self._classifier is None:
            return _clarify(ReasonCategory.NO_CLEAR_INTENT, "Could you tell me a bit more about what you need?")
        name = self._classifier.name
        try:
            output = self._classifier.classify(ClassifierInput(
                text=text[:MAX_CLASSIFIER_CHARS], previous_task_route=request.previous_task_route,
                follow_up_kind=request.resolution.kind))
        except Exception:   # a broken classifier must never break or widen routing
            return _clarify(ReasonCategory.CLASSIFIER_ERROR, "Could you tell me a bit more about what you need?", by=name)
        calibrated = output.score_kind == "calibrated_probability"
        if output.score < self._min_score or output.route == Route.CLARIFICATION:
            return _clarify(ReasonCategory.CLASSIFIER_LOW_SCORE, "Could you tell me a bit more about what you need?",
                            by=name, score=output.score, calibrated=calibrated)
        return decision(output.route, ReasonCategory.CLASSIFIER, "medium", "classifier_score", decided_by=name,
                        score=output.score, calibrated=calibrated)


# ------------------------------------------------------------------------------------------------------ helpers
def _explicit_task(text: str) -> RoutingDecision | None:
    signals = rules.task_signals(text)
    if len(signals) > 1:
        return _mixed(list(signals))
    if len(signals) == 1:
        route, ids = next(iter(signals.items()))
        return decision(route, ReasonCategory.EXPLICIT_TASK_REQUEST, "high", "deterministic_rule", matched_signals=tuple(ids))
    return None


def _mixed(routes: list[Route]) -> RoutingDecision:
    labels = tuple(ROUTE_LABELS.get(r, "our earlier conversation") for r in routes)
    return _clarify(ReasonCategory.MIXED_INTENT, "This asks for several different things. Which should I do first?",
                    options=labels, signals=tuple(r.value for r in routes))


def _clarify(reason: ReasonCategory, question: str, options: tuple[str, ...] = (), signals: tuple[str, ...] = (),
             by: str = "rules", score: float | None = None, calibrated: bool = False) -> RoutingDecision:
    basis = "classifier_score" if score is not None else ("deterministic_rule" if by == "rules" else "none")
    return decision(Route.CLARIFICATION, reason, "low" if score is not None else "high", basis, decided_by=by,
                    clarification=question, clarification_options=options, matched_signals=signals, score=score,
                    calibrated=calibrated)
