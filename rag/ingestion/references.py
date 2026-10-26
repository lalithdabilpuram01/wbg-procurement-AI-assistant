"""Procurement-reference normalisation (wraps wbdisc.normalize.normalize_ref) and label vocabularies."""
from __future__ import annotations

import re
import unicodedata

from . import config  # noqa: F401  (sys.path for wbdisc)
from wbdisc.normalize import normalize_ref  # noqa: E402
from wbdisc.procurement_plan import header_key, normalize_plan_status  # noqa: E402

__all__ = ["normalize_ref", "header_key", "normalize_plan_status", "package_core", "package_core_in_text", "activity_number",
           "category_family", "squash", "review_code", "find_packages"]

# STEP package reference: COUNTRY-AGENCY(-SUB)-NNNNNN-(CW|GO|CS|NC|TA)-METHOD. Contracts may add lot / sequence
# suffixes ('-1', '-LOT2', '-LTD') or programme prefixes ('CERC-'), so the package core is searched, not anchored.
STEP_CORE = re.compile(r"(?<![A-Z0-9])[A-Z]{2}-[A-Z0-9&()]+(?:-[A-Z0-9&()]+)*?-\d{5,7}-(?:CW|GO|CS|NC|TA)-[A-Z]+")
ANCHOR = re.compile(r"-(\d{5,7})-(CW|GO|CS|NC|TA)-([A-Z]+)")
REVIEW = {"post": "post", "prior": "prior", "aposteriori": "post", "apriori": "prior", "expost": "post", "exante": "prior",
          "posterior": "post", "previa": "prior", "previo": "prior"}


def squash(x: str | None) -> str:
    x = unicodedata.normalize("NFKD", x or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", x)


def package_core(ref: str | None, method_codes: set[str] | None = None) -> str | None:
    """STEP package reference inside a (normalised) reference; text glued after the method code is cut when known."""
    n = normalize_ref(ref)
    if not n:
        return None
    m = STEP_CORE.search(n)
    if not m:
        return None
    core = m.group(0)
    if method_codes:
        head, meth = core.rsplit("-", 1)
        code = max((c for c in method_codes if meth.startswith(c)), key=len, default=meth)
        core = f"{head}-{code}"
    return core


def package_core_in_text(text: str | None, prefixes: set[str], method_codes: set[str] | None = None) -> tuple[str | None, bool]:
    """Package reference inside a table cell that may start with text spilled over from the previous row
    ('Total LK-MIHAPCLG-164131-CW- RFB / ...'). The search is anchored on the project's country prefixes.
    Returns (core, had_leading_text)."""
    flat = re.sub(r"[‐-―_/\\.]", "-", re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).upper())
    if not prefixes:
        return None, False
    rx = re.compile(rf"(?:{'|'.join(sorted(map(re.escape, prefixes)))})-[A-Z0-9&()]+(?:-[A-Z0-9&()]+)*?-\d{{5,7}}-(?:CW|GO|CS|NC|TA)-[A-Z]+")
    m = rx.search(flat)
    if not m:
        return None, False
    return package_core(m.group(0), method_codes), m.start() > 0


def find_packages(text: str) -> list[str]:
    """All STEP package references in free text (whitespace inside references removed first)."""
    flat = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).upper()
    flat = re.sub(r"[‐-―_/\\.]", "-", flat)
    return [m.group(0) for m in STEP_CORE.finditer(flat)]


def activity_number(core: str | None) -> str | None:
    m = ANCHOR.search(core or "")
    return m.group(1) if m else None


def category_family(x: str | None) -> str | None:
    """Plan section heading or DS00005 procurement_category -> WORK | GOOD | NONC | CONS."""
    k = re.sub(r"[^A-Z]", "", unicodedata.normalize("NFKD", x or "").encode("ascii", "ignore").decode().upper())
    if not k:
        return None
    if k.startswith(("NON", "SERVICESAUTRES", "SERVICIOSDENO")):
        return "NONC"
    if k.startswith(("WORK", "CIVIL", "TRAVAUX", "OBRAS")):
        return "WORK"
    if k.startswith(("GOOD", "FOURNITURE", "BIENS", "BIENES")):
        return "GOOD"
    if k.startswith(("CONS", "INDIVIDUAL", "CABINETS", "BUREAUX", "FIRMAS", "CONSULTORES")):
        return "CONS"
    return k[:4]


def review_code(x: str | None) -> str | None:
    s = squash(x)
    return REVIEW.get(s) if s else None
