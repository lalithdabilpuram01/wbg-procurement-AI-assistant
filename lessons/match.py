"""Which approved lessons fit a situation: the lifecycle stage must match; then shared topics, the same project,
sector or country, the procurement category, and the weight of the source (an IEG review counts most)."""
from __future__ import annotations

from dataclasses import dataclass

from .cards import LessonCard

SOURCE_WEIGHT = {"ICRR": 1.0, "ICR": 0.8, "PAD": 0.4}
MAX_LESSONS = 4


@dataclass(frozen=True)
class Situation:
    stage: str                                   # planning | tender_design | execution | learning
    topics: frozenset[str] = frozenset()
    category: str | None = None                  # Works | Goods | Consultant Services
    project_id: str | None = None
    sector: str | None = None
    country: str | None = None


def score(card: LessonCard, s: Situation) -> float:
    if s.stage not in card.stages:
        return 0.0
    same_project = s.project_id is not None and card.project_id == s.project_id
    return (2.0 * len(s.topics & set(card.topics)) + 2.0 * same_project
            + 1.0 * (s.sector is not None and card.sector == s.sector) + 1.0 * (s.country is not None and card.country == s.country)
            + 1.0 * (s.category in card.categories) + SOURCE_WEIGHT.get(card.doc_type, 0.0))


def relevant(cards: list[LessonCard], situation: Situation, k: int = MAX_LESSONS) -> list[LessonCard]:
    """The best approved lessons for the situation (only approved cards are ever used)."""
    scored = [(score(c, situation), c) for c in cards if c.status == "approved"]
    return [c for value, c in sorted(scored, key=lambda x: (-x[0], x[1].card_id)) if value > 0][:k]
