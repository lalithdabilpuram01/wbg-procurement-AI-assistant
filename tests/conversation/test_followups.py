"""Deterministic follow-up resolution: references, filter changes, corrections, resets and ambiguity."""
from __future__ import annotations

import pytest

from conversation.followups import FollowUpResolver, Resolution
from conversation.models import ActiveContext, TurnKind


@pytest.fixture(scope="module")
def resolver() -> FollowUpResolver:
    return FollowUpResolver()


def converse(resolver: FollowUpResolver, *messages: str) -> list[Resolution]:
    context, results = ActiveContext(), []
    for message in messages:
        result = resolver.resolve(message, context)
        results.append(result)
        if result.kind != TurnKind.CLARIFICATION_REQUIRED:
            context = result.context
    return results


def codes(result: Resolution) -> list[str]:
    return [c.code for c in result.context.countries]


SCENARIO = ["Show procurement activity in Kenya.", "Only include fiscal year 2024.", "Now compare that with Uganda.",
            "What did we learn from those projects?", "Remove the year filter."]


def test_required_five_turn_scenario(resolver):
    first, year, compare, lessons, remove = converse(resolver, *SCENARIO)

    assert (first.kind, codes(first), first.context.fiscal_years) == (TurnKind.QUESTION, ["KE"], [])
    assert first.standalone_question == "Show procurement activity in Kenya."

    assert (year.kind, codes(year), year.context.fiscal_years) == (TurnKind.REFINEMENT, ["KE"], [2024])
    assert year.standalone_question == "Show procurement activity for Kenya, in fiscal year 2024."

    assert (compare.kind, codes(compare), compare.context.fiscal_years) == (TurnKind.COMPARISON, ["KE", "UG"], [2024])
    assert compare.context.compare_by_country
    assert compare.standalone_question == ("Show procurement activity for Kenya and Uganda, in fiscal year 2024, "
                                           "compared by country.")

    assert (lessons.kind, codes(lessons), lessons.context.fiscal_years) == (TurnKind.REFERENCE, ["KE", "UG"], [2024])
    assert "those" not in lessons.standalone_question
    assert "Kenya and Uganda" in lessons.standalone_question and "fiscal year 2024" in lessons.standalone_question

    assert (remove.kind, codes(remove), remove.context.fiscal_years) == (TurnKind.FILTER_REMOVAL, ["KE", "UG"], [])
    assert "2024" not in remove.standalone_question and "Kenya and Uganda" in remove.standalone_question
    assert remove.notes == ["Fiscal-year filter removed."]


def test_new_question_does_not_inherit_filters(resolver):
    _first, _year, fresh = converse(resolver, "Show procurement activity in Kenya.", "Only include fiscal year 2024.",
                                    "How many bid invitations were published by procurement method?")
    assert fresh.kind == TurnKind.QUESTION
    assert fresh.context.countries == [] and fresh.context.fiscal_years == []


def test_correction_replaces_the_named_country(resolver):
    *_, corrected = converse(resolver, *SCENARIO[:3], "I meant Tanzania, not Uganda.")
    assert corrected.kind == TurnKind.CORRECTION
    assert codes(corrected) == ["KE", "TZ"] and corrected.context.fiscal_years == [2024]


def test_topic_reset_clears_context_and_needs_no_agent(resolver):
    *_, reset = converse(resolver, *SCENARIO[:3], "Start over.")
    assert reset.kind == TurnKind.TOPIC_RESET and reset.context.is_empty() and reset.standalone_question is None


def test_adding_a_country_keeps_the_others(resolver):
    *_, added = converse(resolver, "Show procurement activity in Kenya.", "Also add Tanzania.")
    assert added.kind == TurnKind.REFINEMENT and codes(added) == ["KE", "TZ"]


def test_bare_year_is_an_explicit_assumption(resolver):
    result = resolver.resolve("How many contracts were signed in Kenya in 2023?", ActiveContext())
    assert result.context.fiscal_years == [2023]
    assert any("fiscal year 2023" in note for note in result.notes)


def test_project_ids_are_tracked(resolver):
    first, follow = converse(resolver, "Show the contracts of project P123456.", "Only include fiscal year 2022.")
    assert first.context.project_ids == ["P123456"] and follow.context.project_ids == ["P123456"]
    assert "P123456" in follow.standalone_question


@pytest.mark.parametrize("history, message, expected_options", [
    ([], "What about Congo?", ["Democratic Republic of Congo", "Republic of Congo"]),             # ambiguous name
    ([], "What did we learn from those projects?", []),                                          # nothing to refer to
    ([], "Only include fiscal year 2024.", []),                                                  # nothing to refine
    ([], "Compare that with it.", []),                                                           # nothing to compare
    (SCENARIO[:3], "Show only that country.", ["Kenya", "Uganda"]),                              # which country?
    (SCENARIO[:1], "Remove the year filter.", []),                                               # no year filter
    (SCENARIO[:1], "Remove Uganda.", ["Kenya"]),                                                 # not in the filters
    (SCENARIO[:1], "Now compare that.", []),                                                     # compare with what?
    ([], "And?", []),                                                                            # too short
])
def test_ambiguous_follow_ups_require_clarification(resolver, history, message, expected_options):
    *_, result = converse(resolver, *history, message) if history else [resolver.resolve(message, ActiveContext())]
    assert result.kind == TurnKind.CLARIFICATION_REQUIRED
    assert result.standalone_question is None and result.clarification
    assert result.clarification_options == expected_options


def test_clarification_keeps_the_previous_context(resolver):
    before = converse(resolver, *SCENARIO[:3])[-1].context
    result = resolver.resolve("Show only that country.", before)
    assert result.context == before
