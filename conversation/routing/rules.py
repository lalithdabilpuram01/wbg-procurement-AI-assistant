"""Deterministic routing signals (pure functions over the redacted message).

Conservative by design: a task route needs a specific *phrase* (an aggregation over a data noun, "lessons learned",
"predict", "prepare a procurement plan" ...). Broad words such as "project", "procurement" or "contract" alone never
select a route. Conversational patterns must match the whole message, clause by clause, so a greeting in front of a
task ("Hi, how many contracts ...") does not hide the task.
"""
from __future__ import annotations

import re

from conversation.followups import BARE_YEAR, FISCAL_YEAR, PROJECT_ID
from conversation.routing.routes import ReasonCategory, Route
from conversation.vocabulary import cohort_vocabulary
from structured_agent.guardrails.pipeline import EXECUTION_TOOLS, RETRIEVAL_TOOLS, VALIDATION_TOOLS


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


# ------------------------------------------------------------------------------------------------ conversation
CONVERSATIONAL_CLAUSES: dict[ReasonCategory, re.Pattern] = {
    ReasonCategory.GREETING: _rx(r"^(hi|hello|hey|hiya|greetings|good (morning|afternoon|evening|day))"
                                 r"( (there|team|everyone|all))?$"),
    ReasonCategory.GRATITUDE: _rx(r"^((many )?thanks|thank you|thank you (so|very) much|thanks (a lot|so much|again)|thx|"
                                  r"cheers|much appreciated|(great|perfect|awesome|ok|okay) (thanks|thank you)|"
                                  r"that('s| is| was) (helpful|very helpful|great|perfect|useful))$"),
    ReasonCategory.SMALL_TALK: _rx(r"^(how are you( doing)?( today)?|how is it going|what can you do|what can you help "
                                   r"(me )?with|who are you|what are you|ok(ay)?|great|cool|nice|perfect|awesome|got it|"
                                   r"sounds good|understood|i see|good|yes|no|sure)$"),
    ReasonCategory.FAREWELL: _rx(r"^(bye|goodbye|good bye|see you( later| soon)?|talk (to you )?later|"
                                 r"that('s| is) all( for (now|today))?)$"),
}
CLAUSE_SPLIT = re.compile(r"[.!?,;:]+|\s+-\s+")

# ------------------------------------------------------------------------------------------- the previous answer
# The whole message asks about what was already said: answered from the conversation history, never by a new search.
_THAT = r"(that|this|it|those|these|your( last| previous)? (answer|reply|response|point)|the (last|previous) (answer|reply)|what you (just )?said)"
_HOW = r"(simply|more simply|in simple(r)? (terms|words|english)|in plain (english|words|terms)|in (more )?detail|briefly|again|step by step|for me|please)"
ABOUT_PREVIOUS_ANSWER = _rx(
    rf"^(please |can you |could you |would you )?(explain|clarify|elaborate( on)?|expand( on)?|simplify|rephrase|reword|"
    rf"summari[sz]e|shorten|break down|unpack)( {_THAT})?( {_HOW})*( please)?$"
    rf"|^(what do you mean( by {_THAT})?|what does {_THAT} mean|what is meant by {_THAT}|why( is| was| would)?( {_THAT})?( so)?|"
    rf"how so|how come|really|are you sure|in simple(r)? (terms|words)|say {_THAT} again|tell me more( about {_THAT})?|"
    rf"go on|what else|anything else|is that (all|right|correct)|(and )?what about the rest|(give|show) me (a )?(shorter|simpler) version)$")


def is_about_previous_answer(text: str) -> bool:
    return bool(ABOUT_PREVIOUS_ANSWER.match(text.strip().rstrip("?!. ")))


# ------------------------------------------------------------------------------------------------ memory recall
MEMORY_RECALL = _rx(r"\b(summari[sz]e|recap|remind me|go over|go back over)\b.{0,40}\b(we|our|this|the|i)\s+"
                    r"(discussed|discussion|conversation|talked|covered|decided|chat|asked|said|agreed)\b"
                    r"|\bwhat (did|have) (we|i) (decide|decided|discuss|discussed|say|said|ask|asked|agree|agreed|"
                    r"conclude|concluded|cover|covered)\b"
                    r"|\bwhat was my (first|last|previous|earlier) question\b"
                    r"|\b(recap|summary) of (our|this|the) (conversation|discussion|chat)\b")
EVIDENCE_RECHECK = _rx(r"\b(re-?check|re-?verify|verify|re-?run|re-?validate|double-?check|confirm)\b.{0,40}"
                       r"\b(data|database|source|sources|evidence|figures|numbers|results?)\b"
                       r"|\bstill (true|correct|valid|accurate)\b")

# ------------------------------------------------------------------------------------------------ explanations
DEFINITION_STRONG = _rx(r"^(please |can you )?(explain|describe|tell me) (what|how) .{1,80}\b(means?|works?)\b"
                        r"|^what (does|do) .{1,60}\bmean\b|^(please )?(define|definition of|meaning of)\b"
                        r"|\bwhat is meant by\b|\bdifference between\b")
# General questions. "what is the ..." is excluded: it usually asks for a specific fact or data value.
DEFINITION_WEAK = _rx(r"^(please |can you )?(what is (a|an)|what are (?!the\b)|explain|describe|tell me about|how does|"
                      r"how do)\b")

# --------------------------------------------------------------------------------------------------- task signals
DATA_NOUN = (r"(contracts?|awards?|notices?|bids?|bidders?|tenders?|invitations?|procurement activity|commitments?|"
             r"financing|disbursements?|contract values?|amounts?|suppliers?|procurement methods?)")

TASK_SIGNALS: dict[Route, dict[str, re.Pattern]] = {
    Route.STRUCTURED_DATA: {
        "aggregate_metric": _rx(r"\b(how many|number of|count of|total|sum of|average|mean|median|share of|percentage of|"
                                r"proportion of|top \d+|ranking of)\b.{0,60}\b" + DATA_NOUN + r"\b"),
        "metric_after_noun": _rx(r"\b" + DATA_NOUN + r"\b.{0,30}\b(counts?|totals?|average|trend|breakdown|(by|per) (country|year|"
                                 r"fiscal year|(procurement )?method|sector|region|(procurement )?category))\b"),
        # "which methods were used most for contract awards": a ranking question that names a data noun
        "ranking_question": _rx(r"\b(which|what)\b(?=.{0,120}\b" + DATA_NOUN + r"\b).{0,80}\b(most|least|largest|smallest|"
                                r"highest|lowest|biggest|fewest|top)\b"),
        "data_listing": _rx(r"^(please )?(show|list|give me|display|get|retrieve|tabulate|break down)\b.{0,40}\b" + DATA_NOUN + r"\b"),
        "compare_data": _rx(r"\bcompare\b.{0,40}\b" + DATA_NOUN + r"\b"),
    },
    Route.DOCUMENT_RAG: {
        "lessons": _rx(r"\blessons?( learned| learnt)?\b"),
        "what_we_learned": _rx(r"\bwhat (did|have|can) we learn"),
        "similar_projects": _rx(r"\bsimilar (projects?|operations?|contracts?|procurements?)\b"),
        "precedents": _rx(r"\b(precedents?|historical (cases|examples|experience))\b"),
        "specifications": _rx(r"\b(technical specifications?|technical requirements|terms of reference)\b"),
        "documents": _rx(r"\b(in|from|according to) (the )?([\w-]+ ){0,3}(documents?|pdfs?|reports?|icrs?|pads?|implementation "
                         r"completion reports?|project appraisal documents?)\b"),
        "documents_say": _rx(r"\b(documents?|pdfs?|icrs?|pads?)\b.{0,40}\b(say|says|said|mention|mentions|describe|describes|"
                             r"state|states|report|reports)\b"),
        "why_event": _rx(r"\bwhy (was|were|did|do|is|are)\b.{0,60}\b(delayed|cancell?ed|re-?bid|re-?tendered|amended|slow|"
                         r"late|failed|fail)\b"),
        "project_narrative": _rx(r"\b(risks?|(development|project) objectives?|challenges|implementation (status|progress|"
                                 r"arrangements|problems|issues)|procurement arrangements|safeguards?)\b.{0,80}"
                                 r"\b(projects?|programs?|operations?|P\d{6})\b"),
    },
    Route.ML_PREDICTION: {
        "predict": _rx(r"\b(predict|predicted|prediction|forecast|forecasting)\b"),
        "likelihood": _rx(r"\b(probability|likelihood|chance|risk) (of|that)\b.{0,40}\b(delays?|delayed|amendments?|amended|"
                          r"re-?bid(ding)?|re-?tender(ing)?|overruns?|cancell?ations?|failures?)\b"),
        "estimate_duration": _rx(r"\b(how long will|estimate[sd]? (the )?(duration|time|completion)|expected (duration|"
                                 r"completion (time|date)))\b"),
    },
    Route.PROCUREMENT_PLANNING: {
        "draft_plan": _rx(r"\b(prepare|draft|create|write|build|update|develop|start|generate|help me (prepare|draft|write|"
                          r"create|build|with))\b.{0,40}\b(procurement plan|ppsd|procurement strategy)\b"),
        "decision_action": _rx(r"\b(record|log|approve|sign off|submit)\b.{0,20}\b(the |this |a |our )?(decision|"
                               r"procurement plan|ppsd|approval)\b"),
    },
}

# Attempts to choose tools, agents or routes from the message. These never grant anything; the router asks instead.
_TOOL_NAMES = "|".join(sorted(RETRIEVAL_TOOLS | VALIDATION_TOOLS | EXECUTION_TOOLS))
_ROUTE_NAMES = "|".join(r.value for r in Route)
TOOL_DIRECTIVE = _rx(rf"\b({_TOOL_NAMES}|{_ROUTE_NAMES})\b"
                     r"|\b(route|send|forward|switch|hand)\b.{0,25}\b(route|agent|mode|tool)\b"
                     r"|\b(use|call|invoke|run|enable)\b.{0,20}\b(tool|tools|function|agent|mcp)\b")


# ------------------------------------------------------------------------------------------------------ functions
def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def conversational_reason(text: str) -> ReasonCategory | None:
    """The reason if EVERY clause of the message is conversational ("Hi, thanks!"), else None."""
    clauses = [c.strip().lower() for c in CLAUSE_SPLIT.split(text) if re.search(r"\w", c)]
    if not clauses:
        return None
    reasons = []
    for clause in clauses:
        clause = re.sub(r"[^\w' ]+", "", clause).strip()
        match = next((reason for reason, pattern in CONVERSATIONAL_CLAUSES.items() if pattern.match(clause)), None)
        if match is None:
            return None
        reasons.append(match)
    return next((r for r in reasons if r != ReasonCategory.GREETING), ReasonCategory.GREETING)


def task_signals(text: str) -> dict[Route, list[str]]:
    """Task routes whose specific phrases occur in the message, with the matching rule ids."""
    found: dict[Route, list[str]] = {}
    for route, rules in TASK_SIGNALS.items():
        ids = [rule_id for rule_id, pattern in rules.items() if pattern.search(text)]
        if ids:
            found[route] = ids
    return found


def mentions_entities(text: str) -> bool:
    """Country, fiscal year or project id named in the message itself."""
    return bool(cohort_vocabulary().find(text) or FISCAL_YEAR.search(text) or BARE_YEAR.search(text)
                or PROJECT_ID.search(text))


def is_tool_directive(text: str) -> bool:
    return bool(TOOL_DIRECTIVE.search(text))


def is_memory_recall(text: str) -> bool:
    return bool(MEMORY_RECALL.search(text))


def needs_evidence_recheck(text: str) -> bool:
    return bool(EVIDENCE_RECHECK.search(text))


def is_strong_definition(text: str) -> bool:
    return bool(DEFINITION_STRONG.search(text))


def is_weak_definition(text: str) -> bool:
    return bool(DEFINITION_WEAK.search(text))
