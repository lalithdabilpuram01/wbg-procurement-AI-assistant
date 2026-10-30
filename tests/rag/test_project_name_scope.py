"""Milestone D retrieval guard additions: opt-in project-name scoping and contract-number checks (pilot corpus, offline)."""
from __future__ import annotations

import pytest

from rag.retrieval import core

QUESTION = "What procurement delays are reported in the documents for the Ethiopia Expressway project?"


@pytest.fixture(scope="module")
def corpus() -> core.Corpus:
    return core.Corpus("C")


def test_distinctive_title_words_identify_one_project(corpus):
    index = corpus.project_name_index()
    assert index["expressway"] == "P148850" and index["casamance"] == "P175325"
    assert "economic" not in index and "project" not in index           # common across the corpus, or generic


def test_a_named_project_narrows_the_search_only_when_enabled(corpus):
    assert core.Retriever(corpus, resolve_project_names=True).guard(QUESTION)["scope_projects"] == {"P148850"}
    assert core.Retriever(corpus).guard(QUESTION)["scope_projects"] == {"P148850", "P163452"}   # default: country only


def test_an_explicit_project_id_wins_over_a_name(corpus):
    guard = core.Retriever(corpus, resolve_project_names=True).guard("Expressway delays in P163452?")
    assert guard["scope_projects"] == {"P163452"}


def test_ordinary_questions_are_not_narrowed(corpus):
    assert core.Retriever(corpus, resolve_project_names=True).guard("What economic risks were reported?")["scope_projects"] is None


def test_an_unknown_contract_number_is_refused(corpus):
    assert core.Retriever(corpus).guard("Which supplier won contract number 9999999?")["status"] == "insufficient_evidence"
