"""What the user is told when a guardrail stops a message: one plain reply per reason, never a bare refusal."""
from __future__ import annotations

from dataclasses import dataclass

SCOPE = ("I'm the procurement intelligence assistant, so that's outside what I can help with. I can answer questions on "
         "World Bank contract awards and procurement notices, search project appraisal, status and completion reports for "
         "risks and lessons, estimate tender duration and risks for a package, and prepare a Planning Brief for a new "
         "project. For example: “What lessons were learned about road works procurement in Ethiopia?”")


@dataclass(frozen=True)
class GuardReply:
    """The status and message shown when a guardrail stops a message."""

    status: str          # conversation status shown to the user
    message: str
    blocked: bool        # a policy block (audit flag); off-topic requests are answered, not blocked


REPLIES = {
    "off_topic": GuardReply("out_of_scope", SCOPE, blocked=False),
    "misconduct_accusation": GuardReply(
        "declined", "I can't judge whether a company or a person acted corruptly or fraudulently. I can show what project "
        "documents, IEG reviews and contract data record, for example procurement complaints, re-tendering, or fraud and "
        "corruption risks and findings reported in appraisal and completion reports.", blocked=True),
    "instruction_override": GuardReply(
        "blocked_by_guardrail", "I can't change my instructions, tools or permissions. Ask me a procurement question and "
        "I'll answer from the approved data and documents.", blocked=True),
    "restricted_data_request": GuardReply(
        "blocked_by_guardrail", "I can't share restricted data, credentials or personal contact details. I can answer from "
        "the approved public procurement data and documents.", blocked=True),
    "credential_exfiltration": GuardReply(
        "blocked_by_guardrail", "I can't share credentials, keys or connection details.", blocked=True),
    "invalid_size": GuardReply(
        "blocked_by_guardrail", "That message is too short or too long for me to process. Please ask a question of up to "
        "2,000 characters.", blocked=True),
    "invalid_format": GuardReply(
        "blocked_by_guardrail", "That message contains formatting or hidden characters I can't process. Please retype it as "
        "plain text.", blocked=True),
}
DEFAULT = GuardReply("blocked_by_guardrail", "I can't process that request safely. Please rephrase it as a question about "
                     "procurement data or project documents.", blocked=True)


def guard_reply(reason: str | None) -> GuardReply:
    return REPLIES.get(reason or "", DEFAULT)
