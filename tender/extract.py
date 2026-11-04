"""Terms of a draft tender (bidding document, ToR, specification) read by rules, each with its source sentence.

Read: bid window, bid validity, bid security, number of lots, average annual turnover, similar-contract experience
(count and value), years of experience, and the completion or delivery period. Amounts are read in US dollars only
(other currencies are kept as text and never compared). Nothing is inferred: a term the draft does not state is absent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
SCALE = {"million": 1e6, "mn": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "thousand": 1e3, "k": 1e3}
USD = r"(?:US\s?\$|USD|U\.S\.\s?dollars?|\$)"
AMOUNT = USD + r"\s?(\d[\d,]*(?:\.\d+)?)\s*(million|mn|m|billion|bn|thousand|k)?\b"
COUNT = r"(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")"
SENTENCE = re.compile(r"(?<=[.;!?])\s+|\n+")
MAX_SENTENCE = 300


@dataclass(frozen=True)
class Term:
    """One term read from the draft: its value, unit and the sentence it came from."""

    name: str
    value: float
    unit: str                    # days | percent | lots | usd | contracts | years | months
    sentence: str                # where the draft says it


PATTERNS: dict[str, tuple[re.Pattern, str]] = {
    "bid_window_days": (re.compile(r"\b(?:bids?|proposals?|applications?|submissions?)\b[^.;]{0,120}?\b(?:within|no later than|"
                                   r"not less than|at least|period of)\s+" + COUNT + r"\s*(?:\(\d+\)\s*)?(?:calendar\s+|working\s+|"
                                   r"business\s+)?days\b", re.I), "days"),
    "bid_validity_days": (re.compile(r"\b(?:bids?|proposals?)\b[^.;]{0,60}?\bvalid\w*\b[^.;]{0,40}?\b(\d{2,3})\s*(?:\(\w+\)\s*)?"
                                     r"(?:calendar\s+)?days\b", re.I), "days"),
    "bid_security_percent": (re.compile(r"\b(?:bid|tender|proposal) (?:security|guarantee|bond)\b[^.;]{0,80}?\b(\d{1,2}(?:\.\d+)?)\s*"
                                        r"(?:%|per\s?cent)", re.I), "percent"),
    "lots": (re.compile(r"\b(?:in|into|comprising|of)\s+" + COUNT + r"\s*(?:\(\d+\)\s*)?lots?\b", re.I), "lots"),
    "experience_years": (re.compile(r"\b" + COUNT + r"\s*(?:\(\d+\)\s*)?years?\s+(?:of\s+)?(?:general\s+|specific\s+|relevant\s+|"
                                    r"professional\s+)?experience\b", re.I), "years"),
    "completion_months": (re.compile(r"\b(?:completion|construction|contract|implementation|delivery|assignment)\s+(?:period|time|"
                                     r"duration)\b[^.;]{0,60}?\b(\d{1,3})\s*(?:\(\w+\)\s*)?(months?|weeks?|days?)\b", re.I), "months"),
}
TURNOVER = re.compile(r"\b(?:average\s+)?annual\s+(?:\w+\s+){0,2}turnover\b[^.;]{0,140}?" + AMOUNT, re.I)
VALIDITY = re.compile(r"\bvalid", re.I)        # "bids shall remain valid for 120 days" is not a bid window
SIMILAR = re.compile(r"\b(?:at least|minimum(?: of)?|a minimum of)?\s*" + COUNT + r"\s*(?:\(\d+\)\s*)?(?:similar|comparable)\s+"
                     r"(?:\w+\s+){0,2}contracts?\b[^.;]{0,160}?" + AMOUNT, re.I)


def extract(text: str) -> list[Term]:
    """Every term the draft states (the first statement of each wins)."""
    found: dict[str, Term] = {}
    for sentence in (s.strip() for s in SENTENCE.split(text) if s.strip()):
        quote = " ".join(sentence.split())[:MAX_SENTENCE]
        for name, (pattern, unit) in PATTERNS.items():
            if name in found or (name == "bid_window_days" and VALIDITY.search(sentence)):
                continue
            if m := pattern.search(sentence):
                value = _count(m.group(1))
                if name == "completion_months":
                    value = _months(value, m.group(2))
                found[name] = Term(name, value, unit, quote)
        if "turnover_usd" not in found and (m := TURNOVER.search(sentence)):
            found["turnover_usd"] = Term("turnover_usd", _amount(m.group(1), m.group(2)), "usd", quote)
        if "similar_contracts" not in found and (m := SIMILAR.search(sentence)):
            found["similar_contracts"] = Term("similar_contracts", _count(m.group(1)), "contracts", quote)
            found["similar_contract_value_usd"] = Term("similar_contract_value_usd", _amount(m.group(2), m.group(3)), "usd", quote)
    return list(found.values())


def _count(text: str) -> float:
    return float(text) if text.isdigit() else float(NUMBER_WORDS[text.lower()])


def _amount(number: str, scale: str | None) -> float:
    return float(number.replace(",", "")) * SCALE.get((scale or "").lower(), 1.0)


def _months(value: float, unit: str) -> float:
    unit = unit.lower()
    return round(value / 4.345, 1) if unit.startswith("week") else round(value / 30.4, 1) if unit.startswith("day") else value
