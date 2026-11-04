"""Compression of older turns into versioned structured summaries, with loss and staleness checks.

* Recent-message sliding window: the last `recent_window_turns` turns are never summarized; they go to the context
  builder verbatim.
* Older turns of the current topic are summarized into a `StructuredSummary` (lists of ids and entities plus a short
  narrative). Each summary is a new version; the original turns are never changed or deleted.
* Loss check: project ids, verified evidence ids, citations, active decision ids and countries of the covered turns must
  all appear in the summary. If any are missing, the summary is stored as invalid (`compression_loss`) and the context
  builder falls back to the original turns.
* Staleness: a summary is not used after a topic reset, a correction of an entity it mentions, the revocation of a
  decision it references, a change of registry build, or after `max_summary_age`.

Summaries are generated memory, NOT verified procurement evidence; the context builder labels them that way.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol

from conversation.followups import PROJECT_ID
from conversation.models import Decision, StructuredSummary, SummaryRecord, ThreadInfo, Turn, TurnKind, utc_now

MAX_SUMMARY_QUESTION_CHARS = 160


class Summarizer(Protocol):
    """Turns older turns into a StructuredSummary; `name` is stored on each summary as its generator."""

    name: str

    def summarize(self, turns: list[Turn], decisions: list[Decision], superseded_countries: set[str]) -> StructuredSummary: ...


@dataclass(frozen=True)
class CompressionPolicy:
    """When to summarize: how many recent turns stay verbatim, and how old a summary may get before it is stale."""

    recent_window_turns: int = 6           # user + assistant turns kept verbatim (3 exchanges)
    min_turns_to_summarize: int = 4        # do not summarize fewer older turns than this
    max_summary_age: dt.timedelta = dt.timedelta(days=30)


@dataclass
class CompressionOutcome:
    """Result of one compression attempt; `missing` lists the facts a failed summary lost."""

    status: str                            # not_needed | up_to_date | created | compression_loss
    record: SummaryRecord | None = None
    missing: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ summarizer
class DeterministicSummarizer:
    """Builds the summary from turn metadata only (no model call). Superseded (corrected) countries are left out."""

    name = "deterministic-v1"

    def summarize(self, turns: list[Turn], decisions: list[Decision], superseded_countries: set[str]) -> StructuredSummary:
        """Summary of the user questions, assistant outcomes and required ids of `turns`."""
        facts = required_facts(turns, decisions, superseded_countries)
        user_turns = [t for t in usable(turns) if t.role == "user"
                      and t.kind not in (TurnKind.CLARIFICATION_REQUIRED, TurnKind.CONVERSATION)]
        questions = [_short(t.metadata.standalone_question or t.text) for t in user_turns]
        outcomes = [f"turn {t.seq}: {t.metadata.status}" for t in usable(turns) if t.role == "assistant" and t.metadata.status]
        years = sorted({y for t in user_turns for y in t.context.fiscal_years})
        return StructuredSummary(
            questions=questions, countries=sorted(facts.countries), fiscal_years=years,
            project_ids=sorted(facts.project_ids), decision_ids=sorted(facts.decision_ids),
            evidence_source_ids=sorted(facts.evidence_ids), citations=sorted(facts.citations), outcomes=outcomes,
            corrections=_corrections(turns),
            narrative=f"{len(user_turns)} earlier questions about {', '.join(sorted(facts.countries)) or 'no specific country'}"
                      f"{' in fiscal years ' + ', '.join(map(str, years)) if years else ''}.")


# ----------------------------------------------------------------------------------------- facts and checks
@dataclass
class RequiredFacts:
    """Ids and entities from the covered turns that a summary must keep (the input of the loss check)."""

    project_ids: set[str] = field(default_factory=set)
    evidence_ids: set[str] = field(default_factory=set)
    citations: set[str] = field(default_factory=set)
    decision_ids: set[str] = field(default_factory=set)
    countries: set[str] = field(default_factory=set)

    def is_empty(self) -> bool:
        return not (self.project_ids or self.evidence_ids or self.citations or self.decision_ids or self.countries)


def usable(turns: list[Turn]) -> list[Turn]:
    """Turns that may be remembered: blocked turns (and their replies) are excluded."""
    return [t for t in turns if not t.metadata.blocked]


def required_facts(turns: list[Turn], decisions: list[Decision], superseded_countries: set[str] = frozenset()) -> RequiredFacts:
    """The facts a summary of `turns` must preserve."""
    facts = RequiredFacts()
    last_seq = max((t.seq for t in turns), default=0)
    for turn in usable(turns):
        facts.project_ids |= set(turn.context.project_ids) | set(PROJECT_ID.findall(turn.text))
        facts.countries |= {c.code for c in turn.context.countries}
        for evidence in turn.metadata.evidence:
            facts.evidence_ids.add(evidence.source_id)
            if evidence.citation:
                facts.citations.add(evidence.citation)
    facts.countries -= superseded_countries
    facts.decision_ids = {d.decision_id for d in decisions
                          if d.active and (d.source_turn_seq is None or d.source_turn_seq <= last_seq)}
    return facts


def missing_facts(summary: StructuredSummary, facts: RequiredFacts) -> list[str]:
    """Required facts that the summary left out, as "label:value" strings; an empty list means nothing was lost."""
    checks = [("project_id", facts.project_ids, summary.project_ids), ("evidence_id", facts.evidence_ids, summary.evidence_source_ids),
              ("citation", facts.citations, summary.citations), ("decision_id", facts.decision_ids, summary.decision_ids),
              ("country", facts.countries, summary.countries)]
    return [f"{label}:{value}" for label, required, kept in checks for value in sorted(required - set(kept))]


def superseded_countries(turns: list[Turn]) -> set[str]:
    """Countries removed by an explicit correction in these turns ("I meant Tanzania, not Uganda" -> {"UG"})."""
    removed: set[str] = set()
    for previous, correction in _correction_pairs(turns):
        removed |= {c.code for c in previous.context.countries} - {c.code for c in correction.context.countries}
    return removed


def stale_reason(summary: SummaryRecord, thread: ThreadInfo, turns: list[Turn], decisions: list[Decision],
                 registry_build_id: str | None, max_age: dt.timedelta, now: dt.datetime | None = None) -> str | None:
    """Why this summary must not be used, or None if it is current."""
    if not summary.valid:
        return summary.invalidation_reason or "invalidated"
    if summary.topic_epoch != thread.topic_epoch:
        return "topic_reset"
    if set(summary.content.countries) & superseded_countries(turns):
        return "corrected_entity"
    revoked = {d.decision_id for d in decisions if not d.active}
    if revoked & set(summary.content.decision_ids):
        return "decision_revoked"
    if registry_build_id and summary.registry_build_id and registry_build_id != summary.registry_build_id:
        return "registry_changed"
    if (now or utc_now()) - summary.created_at > max_age:
        return "expired"
    return None


# ------------------------------------------------------------------------------------------------- compressor
class Compressor:
    """Decides when older turns need a new summary and builds it, marking it invalid if the loss check fails."""

    def __init__(self, summarizer: Summarizer, policy: CompressionPolicy = CompressionPolicy()):
        self.summarizer = summarizer
        self.policy = policy

    def older_turns(self, epoch_turns: list[Turn]) -> list[Turn]:
        """Turns of the current topic outside the recent window."""
        window = self.policy.recent_window_turns
        return epoch_turns[:-window] if len(epoch_turns) > window else []

    def compress(self, thread: ThreadInfo, epoch_turns: list[Turn], decisions: list[Decision],
                 latest: SummaryRecord | None, registry_build_id: str | None) -> CompressionOutcome:
        """Summarize the older turns of the current topic if they are not yet covered by a usable summary."""
        older = self.older_turns(epoch_turns)
        if len(older) < self.policy.min_turns_to_summarize:
            return CompressionOutcome("not_needed")
        # A valid summary of this topic already reaches the last older turn: nothing new to summarize.
        if latest and latest.valid and latest.topic_epoch == thread.topic_epoch and latest.covers_through_seq >= older[-1].seq:
            return CompressionOutcome("up_to_date", latest)

        superseded = superseded_countries(epoch_turns)
        content = self.summarizer.summarize(older, decisions, superseded)
        record = SummaryRecord(
            thread_id=thread.thread_id, version=(latest.version + 1) if latest else 1, topic_epoch=thread.topic_epoch,
            covers_from_seq=older[0].seq, covers_through_seq=older[-1].seq, content=content,
            generator=self.summarizer.name, inputs_sha256=_inputs_hash(older, decisions), registry_build_id=registry_build_id)
        missing = missing_facts(content, required_facts(older, decisions, superseded))
        # A lossy summary is still returned so it can be stored as invalid; it is never used as memory.
        if missing:
            record.invalidated_at, record.invalidation_reason = utc_now(), "compression_loss"
            return CompressionOutcome("compression_loss", record, missing)
        return CompressionOutcome("created", record)


# ---------------------------------------------------------------------------------------------------- helpers
def _correction_pairs(turns: list[Turn]) -> list[tuple[Turn, Turn]]:
    """(previous user question, correction) pairs; clarification turns are skipped so the correction is paired
    with the question it fixes."""
    pairs, previous = [], None
    for turn in usable(turns):
        if turn.role != "user" or turn.kind == TurnKind.CLARIFICATION_REQUIRED:
            continue
        if turn.kind == TurnKind.CORRECTION and previous is not None:
            pairs.append((previous, turn))
        previous = turn
    return pairs


def _corrections(turns: list[Turn]) -> list[str]:
    """Readable notes of what each correction replaced, e.g. "turn 7: Uganda replaced by Tanzania"."""
    notes = []
    for previous, correction in _correction_pairs(turns):
        before, after = {c.name for c in previous.context.countries}, {c.name for c in correction.context.countries}
        removed, added = sorted(before - after), sorted(after - before)
        if removed or added:
            notes.append(f"turn {correction.seq}: {', '.join(removed) or 'nothing'} replaced by {', '.join(added) or 'nothing'}")
    return notes


def _short(text: str) -> str:
    return text if len(text) <= MAX_SUMMARY_QUESTION_CHARS else text[:MAX_SUMMARY_QUESTION_CHARS - 3] + "..."


def _inputs_hash(turns: list[Turn], decisions: list[Decision]) -> str:
    payload = {"turns": [[t.turn_id, t.seq] for t in turns], "decisions": sorted((d.decision_id, d.active) for d in decisions)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
