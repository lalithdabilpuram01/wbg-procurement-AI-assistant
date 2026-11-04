"""Facts every document parser needs from the file name and the cover pages: project, date and title."""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from pathlib import Path

from rapidfuzz import fuzz

from ..reference import Reference, slug

PROJECT_ID = re.compile(r"\bP\d{6}\b")
PROJECT_IN_NAME = re.compile(r"(?<![A-Z0-9])P\d{6}(?!\d)")   # "P148850_plan.pdf": "_" is not a word boundary
TITLE_MATCH = 85                    # rapidfuzz token-set ratio for a project title found on the cover pages
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_PATTERNS = [  # (pattern, strptime formats); the first date found on the cover pages is the document date
    (re.compile(rf"\b((?:{MONTHS})\s+\d{{1,2}},\s+\d{{4}})\b"), ("%B %d, %Y",)),
    (re.compile(rf"\b(\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}})\b"), ("%d %B %Y",)),
    (re.compile(r"\b(\d{1,2}-[A-Z][a-z]{2}-\d{4})\b"), ("%d-%b-%Y",)),
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"), ("%Y-%m-%d",)),
]


def document_id_for(folder: str, path: Path) -> str:
    """Stable per file name, so a corrected file dropped under the same name replaces the earlier version.
    A World Bank document number (all digits) is kept as is."""
    stem = path.stem
    return stem if stem.isdigit() else f"{folder}-{slug(stem)}"


def project_for(name: str, text: str, ref: Reference) -> tuple[str | None, str, float]:
    """(project id, match class, confidence): from the file name (E), the ids printed on the cover pages (E), or
    the project title printed there (P, fuzzy). None when nothing identifies the project."""
    in_name = PROJECT_IN_NAME.findall(name.upper())
    if in_name:
        return in_name[0], "E", 1.0
    in_text = Counter(PROJECT_ID.findall(text[:20000]))
    if in_text:
        return in_text.most_common(1)[0][0], "E", 0.95
    head = text[:4000]
    best = max(((pid, fuzz.token_set_ratio(p["title"], head)) for pid, p in ref.projects.items()), key=lambda x: x[1], default=None)
    if best and best[1] >= TITLE_MATCH:
        return best[0], "P", round(best[1] / 100, 2)
    return None, "none", 0.0


def document_date(text: str) -> str | None:
    """The first plausible date printed on the cover pages (years 1980 to next year), as ISO text; None if none."""
    head = text[:6000]
    found = []
    for pattern, formats in DATE_PATTERNS:
        for match in pattern.finditer(head):
            for fmt in formats:
                try:
                    found.append((match.start(), dt.datetime.strptime(match.group(1), fmt).date()))
                except ValueError:
                    continue
    plausible = [(pos, d) for pos, d in found if 1980 <= d.year <= dt.date.today().year + 1]
    return min(plausible)[1].isoformat() if plausible else None


NAMES_PROJECT = re.compile(r"\b(project|program|programme|projet|plan)\b", re.I)
TITLE_SKIP = ("document of", "report no", "public disclosure", "for official use", "international development association",
              "international bank for reconstruction", "the world bank", "project appraisal document", "on a proposed")


def title_for(text: str, fallback: str) -> str:
    """The cover line that names the project, else the first substantial cover line, else the file name."""
    lines = [line.strip() for line in text.splitlines()[:60]]
    candidates = [line for line in lines if 15 <= len(line) <= 200 and sum(c.isalpha() for c in line) > len(line) * 0.6
                  and not line.lower().startswith(TITLE_SKIP)]
    naming = next((line for line in candidates if NAMES_PROJECT.search(line)), None)
    return naming or (candidates[0] if candidates else fallback)
