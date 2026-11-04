"""Deterministic follow-up resolution.

Given the active context (entities and filters of the current query) and a new user message, decide what kind of turn
it is, update the context, and write an explicit standalone question for the agents. Rules are cue-based and
conservative: when the referent is missing or not unique, the result is CLARIFICATION_REQUIRED instead of a guess.
This handles common, explicit follow-ups; it does not understand every possible conversational reference.

Precedence of cues: topic reset > correction > filter removal > comparison > refinement > reference > new question.
A new question does NOT inherit earlier filters; only explicit follow-up cues carry context forward. Which agent a
message goes to is decided by the intent router (routing/), not here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from conversation.models import ActiveContext, CountryRef, TurnKind
from conversation.vocabulary import CountryVocabulary, cohort_vocabulary

FISCAL_YEAR = re.compile(r"\b(?:fiscal year|fy)\s*'?(\d{4}|\d{2})\b", re.IGNORECASE)
BARE_YEAR = re.compile(r"\b(20[0-4]\d)\b")
PROJECT_ID = re.compile(r"\bP\d{6}\b")

RESET = re.compile(r"\b(new topic|start (over|again)|change (the )?(topic|subject)|forget (that|everything|the previous|previous (filters|context|questions))|"
                   r"reset (the )?(context|filters|conversation))\b", re.IGNORECASE)
CORRECTION = re.compile(r"\b(i meant|i mean|sorry,? i meant|correction:?|not\s+\w[\w ]*?,?\s+but)\b", re.IGNORECASE)
NEGATED_COUNTRY = re.compile(r"\bnot\s+(?:in\s+)?([A-Z][\w' ]+?)(?:[,.;]|\s+but\b|$)")
REMOVE = re.compile(r"\b(remove|drop|clear|without|ignore|exclude|forget)\b", re.IGNORECASE)
YEAR_FILTER_WORDS = re.compile(r"\b(year|years|fiscal year|date|period|time)\b", re.IGNORECASE)
COUNTRY_FILTER_WORDS = re.compile(r"\b(country|countries)\b", re.IGNORECASE)
COMPARE = re.compile(r"\b(compare|comparison|versus|vs\.?|against|relative to)\b", re.IGNORECASE)
REFINE_START = re.compile(r"^\s*(only|just|now|also|and|but|limit|restrict|filter|same|add|include|what about|how about)\b",
                          re.IGNORECASE)
ADD_WORDS = re.compile(r"\b(also|add|and also|as well|plus|including)\b", re.IGNORECASE)
ANAPHORA = re.compile(r"\b(that|those|these|them|it|same|the above|previous|earlier|this|other|else|additional)\b",
                      re.IGNORECASE)
REFERENCED_NOUN = re.compile(r"\b(those|these|that|the same|the)\s+(projects?|countries|country|contracts?|notices?|results?|operations?)\b",
                             re.IGNORECASE)
SINGULAR_COUNTRY_REFERENCE = re.compile(r"\b(that|this|the) country\b", re.IGNORECASE)


@dataclass
class Resolution:
    """Outcome of one message: its kind, the context after it, and the standalone question (or a clarification)."""

    kind: TurnKind
    context: ActiveContext
    standalone_question: str | None             # None when clarification is required or the topic was reset
    clarification: str | None = None
    clarification_options: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Mentions:
    """Entities named in one message. Bare years ("2021") are kept apart: reading them as fiscal years is assumed."""

    countries: list[CountryRef]
    ambiguous: list[tuple[str, list[CountryRef]]]
    fiscal_years: list[int]
    assumed_fiscal_years: list[int]
    project_ids: list[str]


class FollowUpResolver:
    """Turns a message plus the active context into a Resolution, using the cue precedence in the module docstring."""

    def __init__(self, vocabulary: CountryVocabulary | None = None):
        self._vocabulary = vocabulary or cohort_vocabulary()

    # ------------------------------------------------------------------------------------------------- entry point
    def resolve(self, message: str, active: ActiveContext) -> Resolution:
        """Classify the message as a follow-up kind or a new question and return the updated context."""
        mentions = self._mentions(message)
        # An ambiguous country name ("Congo") always asks first, whatever kind of turn this is.
        if mentions.ambiguous:
            return self._clarify(active, f"'{mentions.ambiguous[0][0]}' matches several countries; which one do you mean?",
                                 [c.name for c in mentions.ambiguous[0][1]])
        if RESET.search(message):
            return self._finish(TurnKind.TOPIC_RESET, ActiveContext(), message, mentions, notes=["Earlier context cleared."])
        if CORRECTION.search(message) and not active.is_empty():
            return self._correction(message, active, mentions)
        if REMOVE.search(message) and not active.is_empty():
            return self._removal(message, active, mentions)
        if COMPARE.search(message):
            return self._comparison(message, active, mentions)
        if REFINE_START.search(message):
            return self._refinement(message, active, mentions)
        if ANAPHORA.search(message) and not self._has_entities(mentions):
            return self._reference(message, active)
        return self._new_question(message, mentions)

    def restate(self, active: ActiveContext) -> Resolution:
        """The active query restated as a standalone question (used to re-verify remembered results)."""
        if active.topic is None:
            return self._clarify(active, "There is no earlier question to recheck.")
        return Resolution(TurnKind.REFERENCE, active, _standalone(active), notes=["Re-running the active query."])

    # ------------------------------------------------------------------------------------------------- turn kinds
    def _new_question(self, message: str, mentions: Mentions) -> Resolution:
        """A fresh question: only the entities it names itself, nothing inherited."""
        if len(message.split()) < 3 and not self._has_entities(mentions):
            return self._clarify(ActiveContext(), "The question is too short to interpret; please ask it in full.")
        context = ActiveContext(topic=message.strip(), countries=mentions.countries, fiscal_years=self._years(mentions),
                                project_ids=mentions.project_ids, assumptions=self._year_assumptions(mentions))
        return self._finish(TurnKind.QUESTION, context, message, mentions, standalone=message.strip())

    def _refinement(self, message: str, active: ActiveContext, mentions: Mentions) -> Resolution:
        """Change the active filters to the ones named; countries are added on "also/add", otherwise replaced."""
        if active.topic is None:
            return self._clarify(active, "There is no earlier question to refine; please ask the full question.")
        if not self._has_entities(mentions):
            return self._clarify(active, "Which filter should change? Please name the country, project or fiscal year.")
        context = active.model_copy(deep=True)
        if mentions.countries:
            context.countries = _merge(context.countries, mentions.countries) if ADD_WORDS.search(message) else mentions.countries
        if self._years(mentions):
            context.fiscal_years = self._years(mentions)
        if mentions.project_ids:
            context.project_ids = mentions.project_ids
        context.assumptions += self._year_assumptions(mentions)
        return self._finish(TurnKind.REFINEMENT, context, message, mentions)

    def _comparison(self, message: str, active: ActiveContext, mentions: Mentions) -> Resolution:
        """Add the newly named countries to the active query and compare by country."""
        if active.topic is None:
            if not mentions.countries:
                return self._clarify(active, "There is nothing earlier to compare; please name what to compare.")
            return self._new_question(message, mentions)       # a self-contained comparison question
        new = [c for c in mentions.countries if c not in active.countries]
        if not new:
            return self._clarify(active, "Compare with what? Please name the country to compare with.")
        context = active.model_copy(deep=True)
        context.countries = _merge(active.countries, new)
        context.compare_by_country = True
        return self._finish(TurnKind.COMPARISON, context, message, mentions)

    def _reference(self, message: str, active: ActiveContext) -> Resolution:
        """Read "that/those/the same" as the active scope; ask when "that country" could mean more than one."""
        if active.topic is None:
            return self._clarify(active, "It is not clear what 'that' refers to; please name the country, project or year.")
        if SINGULAR_COUNTRY_REFERENCE.search(message) and len(active.countries) != 1:
            return self._clarify(active, "Which country do you mean?", [c.name for c in active.countries])
        context = active.model_copy(deep=True)
        # The new topic keeps the user's wording with the reference made neutral ("those projects" -> "the projects");
        # the active filters are then appended explicitly, so later turns can still change them.
        context.topic = REFERENCED_NOUN.sub(lambda m: f"the {m.group(2)}", message.strip(), count=1)
        return self._finish(TurnKind.REFERENCE, context, message, None,
                            notes=[f"'{_referenced_words(message)}' read as the active scope ({_scope_phrase(active)})."])

    def _removal(self, message: str, active: ActiveContext, mentions: Mentions) -> Resolution:
        """Remove the fiscal-year filter, the named countries, or the whole country filter, as the message says."""
        context = active.model_copy(deep=True)
        notes = []
        if YEAR_FILTER_WORDS.search(message) and not mentions.countries:
            if not active.fiscal_years:
                return self._clarify(active, "There is no year filter to remove.")
            context.fiscal_years, notes = [], ["Fiscal-year filter removed."]
        elif mentions.countries:
            remaining = [c for c in active.countries if c not in mentions.countries]
            if len(remaining) == len(active.countries):
                return self._clarify(active, "That country is not in the current filters.", [c.name for c in active.countries])
            context.countries, notes = remaining, [f"Removed {', '.join(c.name for c in mentions.countries)}."]
            context.compare_by_country = context.compare_by_country and len(remaining) > 1
        elif COUNTRY_FILTER_WORDS.search(message):
            context.countries, context.compare_by_country, notes = [], False, ["Country filter removed."]
        else:
            return self._clarify(active, "Which filter should be removed: the country, the fiscal year or a project?")
        return self._finish(TurnKind.FILTER_REMOVAL, context, message, mentions, notes=notes)

    def _correction(self, message: str, active: ActiveContext, mentions: Mentions) -> Resolution:
        """Replace an earlier value ("I meant Tanzania, not Uganda"). With several active countries and no "not X",
        it asks which one to replace."""
        negated = self._negated_countries(message)
        replacements = [c for c in mentions.countries if c not in negated]
        if not replacements and not self._years(mentions):
            return self._clarify(active, "What should replace the earlier value?")
        context = active.model_copy(deep=True)
        if replacements:
            if negated:
                context.countries = [c for c in active.countries if c not in negated]
                context.countries = _merge(context.countries, replacements)
            elif len(active.countries) == 1:
                context.countries = replacements
            else:
                return self._clarify(active, "Which country should be replaced?", [c.name for c in active.countries])
        if self._years(mentions):
            context.fiscal_years = self._years(mentions)
        return self._finish(TurnKind.CORRECTION, context, message, mentions, notes=["Earlier value corrected."])

    # ---------------------------------------------------------------------------------------------------- helpers
    def _finish(self, kind: TurnKind, context: ActiveContext, message: str, mentions: Mentions | None,
                standalone: str | None = None, notes: list[str] | None = None) -> Resolution:
        """Build the Resolution; the standalone question is written from the context unless one is given."""
        if kind == TurnKind.TOPIC_RESET:
            return Resolution(kind, context, None, notes=notes or [])
        question = standalone or _standalone(context)
        return Resolution(kind, context, question, notes=(notes or []) + context.assumptions)

    @staticmethod
    def _clarify(active: ActiveContext, question: str, options: list[str] | None = None) -> Resolution:
        return Resolution(TurnKind.CLARIFICATION_REQUIRED, active, None, clarification=question,
                          clarification_options=options or [])

    def _mentions(self, message: str) -> Mentions:
        """Countries (unique and ambiguous), fiscal years, bare years and project ids named in the message."""
        countries, ambiguous = [], []
        for text, candidates in self._vocabulary.find(message):
            if len(candidates) > 1:
                ambiguous.append((text, candidates))
            elif candidates[0] not in countries:
                countries.append(candidates[0])
        fiscal = sorted({_full_year(y) for y in FISCAL_YEAR.findall(message)})
        # Explicit "FY 2021" mentions are removed first so their digits are not counted again as bare years.
        bare = sorted({int(y) for y in BARE_YEAR.findall(FISCAL_YEAR.sub(" ", message))} - set(fiscal))
        return Mentions(countries, ambiguous, fiscal, bare, sorted(set(PROJECT_ID.findall(message))))

    def _negated_countries(self, message: str) -> list[CountryRef]:
        """Countries named after "not" ("not Uganda"), i.e. the values a correction takes away."""
        negated = []
        for phrase in NEGATED_COUNTRY.findall(message):
            for _text, candidates in self._vocabulary.find(phrase):
                negated += candidates
        return negated

    @staticmethod
    def _years(mentions: Mentions) -> list[int]:
        return sorted(set(mentions.fiscal_years) | set(mentions.assumed_fiscal_years))

    @staticmethod
    def _year_assumptions(mentions: Mentions) -> list[str]:
        return [f"{y} was read as World Bank fiscal year {y} (July {y - 1} to June {y})." for y in mentions.assumed_fiscal_years]

    @staticmethod
    def _has_entities(mentions: Mentions) -> bool:
        return bool(mentions.countries or mentions.fiscal_years or mentions.assumed_fiscal_years or mentions.project_ids)


# -------------------------------------------------------------------------------------------- standalone wording
ENTITY_PHRASE = re.compile(r"\s*\b((in|for|from|of|during)\s+)?((fiscal year|fy)\s*'?\d{2,4}|20[0-4]\d)\b"
                           r"|\s*\b((in|for|of)\s+)?(projects?\s+)?P\d{6}\b", re.IGNORECASE)


def _standalone(context: ActiveContext) -> str:
    """The topic without its entity phrases, then the active scope ("Show contracts for Kenya, in fiscal year 2022")."""
    topic = context.topic or "Show procurement activity"
    ending = "?" if topic.rstrip().endswith("?") else "."
    return f"{_strip_entities(topic)} {_scope_phrase(context)}".strip() + ending


def _referenced_words(message: str) -> str:
    match = REFERENCED_NOUN.search(message) or ANAPHORA.search(message)
    return match.group(0) if match else message


def _strip_entities(text: str) -> str:
    """The text without year, project and "in <country>" phrases, so the scope phrase can add them back once."""
    vocabulary = cohort_vocabulary()
    text = ENTITY_PHRASE.sub("", text)
    # [3:-3] drops the leading "\b(" and trailing ")\b" of the vocabulary pattern, leaving the list of country names.
    text = re.sub(r"\s*\b(in|for|from)\s+(" + vocabulary.mention_pattern().pattern[3:-3] + r")\b(\s*(,|and)\s*(" +
                  vocabulary.mention_pattern().pattern[3:-3] + r"))*", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" .?!,")


def _scope_phrase(context: ActiveContext) -> str:
    """The active filters in words ("for Kenya and Uganda, in fiscal year 2022") or "across all countries and years"."""
    parts = []
    if context.countries:
        parts.append("for " + _join([c.name for c in context.countries]))
    if context.project_ids:
        parts.append("for project " + _join(context.project_ids))
    if context.fiscal_years:
        label = "fiscal year" if len(context.fiscal_years) == 1 else "fiscal years"
        parts.append(f"in {label} " + _join([str(y) for y in context.fiscal_years]))
    if context.compare_by_country and len(context.countries) > 1:
        parts.append("compared by country")
    return ", ".join(parts) if parts else "across all countries and years"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _merge(current: list[CountryRef], new: list[CountryRef]) -> list[CountryRef]:
    return current + [c for c in new if c not in current]


def _full_year(value: str) -> int:
    return int(value) if len(value) == 4 else 2000 + int(value)


def strip_reset_phrase(message: str) -> str:
    """The message without its topic-reset phrase ("Start over and show ..." -> "show ...")."""
    return re.sub(r"^\W*(and|then)\b", "", RESET.sub("", message), flags=re.IGNORECASE).strip(" ,.;:-")
