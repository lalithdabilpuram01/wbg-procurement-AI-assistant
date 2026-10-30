"""The Claude conversation responder: answers from the transcript only, keeps the instant fixed replies, and falls back
to them whenever Claude is unavailable."""
from __future__ import annotations

from conversation.responder import ConversationRequest, OfflineResponder
from conversation.routing.routes import ReasonCategory, Route
from structured_agent.nl2sql.llm_client import LlmError, LlmFailure

from wbg_orchestrator.conversation_responder import NO_HISTORY, HistoryResponder

HISTORY = "BEGIN CONVERSATION HISTORY\nassistant (turn 2): Kenya published 412 notices in FY2024.\nEND CONVERSATION HISTORY"


class FakeJson:
    def __init__(self, answer: str | None = None, error: Exception | None = None):
        self.answer, self.error, self.calls = answer, error, []

    def stream_text(self, *, on_text, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        for word in self.answer.split(" "):
            on_text(word + " ")
        return self.answer, None


def ask(message: str, reason=ReasonCategory.ABOUT_PREVIOUS_ANSWER, history=HISTORY) -> ConversationRequest:
    return ConversationRequest(message=message, route=Route.CONVERSATION, reason=reason, transcript=history)


def test_claude_answers_from_the_transcript_only_and_streams_it():
    client = FakeJson("In short: Kenya had 412 notices in fiscal year 2024.")
    pieces: list[str] = []
    reply = HistoryResponder(client).respond(ask("explain that simply"), pieces.append)
    assert reply.text.startswith("In short") and reply.evidence_status == "conversation_memory_not_reverified"
    assert len(pieces) > 1 and "".join(pieces).strip() == reply.text
    [call] = client.calls
    assert "Kenya published 412 notices" in call["user"] and "ONLY the conversation history" in call["system"]
    assert call["schema_name"] == "conversation_answer"


def test_greetings_keep_their_instant_reply_and_never_call_claude():
    client = FakeJson("unused")
    reply = HistoryResponder(client).respond(ask("hi", reason=ReasonCategory.GREETING))
    assert reply.text == OfflineResponder.REPLIES[ReasonCategory.GREETING] and client.calls == []


def test_without_history_or_without_claude_nothing_is_invented():
    assert HistoryResponder(FakeJson("x")).respond(ask("why?", history="")).text == NO_HISTORY
    off = HistoryResponder(FakeJson(error=LlmError(LlmFailure.DISABLED))).respond(ask("why?"))
    assert off.text == OfflineResponder.MODEL_NOT_ENABLED


# ------------------------------------------------------------------------------------------ the first router
def test_the_topic_router_maps_claudes_choice_and_clamps_the_confidence():
    from conversation.routing.classifier import ClassifierInput
    from wbg_orchestrator.topic_router import ClaudeTopicRouter

    class Json:
        def __init__(self, reply):
            self.reply, self.calls = reply, []

        def complete(self, **kwargs):
            self.calls.append(kwargs)
            return self.reply, "", None

    client = Json({"route": "procurement_documents", "confidence": 1.7})
    out = ClaudeTopicRouter(client).classify(ClassifierInput(text="why did the road works slip", previous_task_route=Route.STRUCTURED_DATA))
    assert out.route == Route.DOCUMENT_RAG and out.score == 1.0 and out.score_kind == "uncalibrated_score"
    assert client.calls[0]["schema_name"] == "route_message" and "PREVIOUS TASK ROUTE: structured_data" in client.calls[0]["user"]
    chat = ClaudeTopicRouter(Json({"route": "conversation", "confidence": 0.8})).classify(ClassifierInput(text="ok and then?"))
    assert chat.route == Route.CONVERSATION


def test_an_off_topic_message_goes_to_the_scope_reply_not_to_an_agent():
    from conversation.followups import FollowUpResolver
    from conversation.models import ActiveContext
    from conversation.routing.router import IntentRouter, RouteRequest
    from wbg_orchestrator.conversation_bridge import UNCLEAR
    from wbg_orchestrator.topic_router import ClaudeTopicRouter

    class OffTopic:
        def complete(self, **kwargs):
            return {"route": "off_topic", "confidence": 0.95}, "", None

    text = "write me a poem about the sea"
    router = IntentRouter(classifier=ClaudeTopicRouter(OffTopic()))
    decision = router.route(RouteRequest(text=text, resolution=FollowUpResolver().resolve(text, ActiveContext())))
    assert decision.route == Route.CLARIFICATION and decision.reason in UNCLEAR       # the front door's scope reply
