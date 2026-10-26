"""Country vocabulary for deterministic entity extraction, built from the approved 2,000-project cohort manifest.

Names map to the WDI 2-letter code used by the Gold tables (`wdi_country_code`). Regional groupings are excluded. Short
forms are derived from WB-style names ("Egypt, Arab Republic of" -> "Egypt"); a short form shared by several countries
("Congo") is kept as ambiguous, so the resolver asks instead of guessing.
"""
from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

from conversation.models import CountryRef

COHORT_MANIFEST = Path(__file__).resolve().parents[1] / "cohort" / "poc_cohort_2000_manifest.csv"


class CountryVocabulary:
    def __init__(self, names_to_countries: dict[str, list[CountryRef]]):
        self._index = names_to_countries
        alternatives = sorted(self._index, key=len, reverse=True)   # longest names first ("South Sudan" before "Sudan")
        self._pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in alternatives) + r")\b", re.IGNORECASE)

    def find(self, text: str) -> list[tuple[str, list[CountryRef]]]:
        """Mentions in order of appearance: (matched text, candidate countries). More than one candidate = ambiguous."""
        found = []
        for match in self._pattern.finditer(text):
            candidates = self._index[match.group(1).lower()]
            found.append((match.group(1), candidates))
        return found

    def mention_pattern(self) -> re.Pattern:
        return self._pattern


@lru_cache(maxsize=1)
def cohort_vocabulary(manifest: Path = COHORT_MANIFEST) -> CountryVocabulary:
    countries: dict[str, CountryRef] = {}
    with manifest.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["country_is_regional"] == "True" or not row["country_iso2"]:
                continue
            countries[row["country_name"]] = CountryRef(name=_display_name(row["country_name"]), code=row["country_iso2"])

    index: dict[str, list[CountryRef]] = {}
    for full_name, country in countries.items():
        for alias in {full_name, _display_name(full_name), _short_form(full_name)}:
            entries = index.setdefault(alias.lower(), [])
            if country not in entries:
                entries.append(country)
    return CountryVocabulary(index)


def _short_form(name: str) -> str:
    return name.split(",")[0].strip()


def _display_name(name: str) -> str:
    """'Gambia, The' -> 'The Gambia'; 'Congo, Democratic Republic of' -> 'Democratic Republic of Congo'."""
    if "," not in name:
        return name
    head, tail = (part.strip() for part in name.split(",", 1))
    return f"{tail} {head}" if tail.lower() in {"the"} or tail.lower().endswith(" of") else head
