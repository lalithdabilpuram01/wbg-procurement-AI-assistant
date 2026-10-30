"""World Bank country/economy code handling.

WB sources (DS00005, projects API) use WB codes that "might be different from the ISO code"
(DS00005 column description). Discovery on 2026-10-20 compared every WB code seen in DS00005
and v3 projects with the ISO2 ids used by the Indicators API (WGI/CPIA). Of 221 codes,
47 were absent from WGI. They fall in three groups:

  * legacy aliases of a current ISO code (curated below, verified by name)
  * regional / multi-country groupings (no single-country governance value exists)
  * dissolved states or economies WGI does not cover

Fuzzy name matching was tested and rejected: it mapped ZR (DR Congo) to 'Congo, Rep.' and
'Multi-Regional' to 'Bulgaria'. Only the explicit alias table below is used.
"""
from __future__ import annotations

import re

WB_TO_ISO2 = {
    "ZR": "CD",  # Congo, Democratic Republic of
    "GZ": "PS",  # West Bank and Gaza
    "YF": "RS",  # Serbia
    "TP": "TL",  # Timor-Leste
    "RY": "YE",  # Yemen, Republic of
}
DISSOLVED_OR_UNCOVERED = {"CS": "Czechoslovakia", "YU": "Yugoslavia, former", "SX": "St Maarten", "TW": "Taiwan, China"}
_REGIONAL = re.compile(r"^(\d[A-Z0-9]|[EWP]\d|AC|CB|ZZ|99)$")


def is_regional(code: str | None) -> bool:
    """True for WB regional / multi-country grouping codes (e.g. 3W, 6O, E5, P2, 99)."""
    return bool(code) and bool(_REGIONAL.match(code.strip().upper()))


def to_iso2(code: str | None) -> str | None:
    """Map a WB country code to ISO 3166-1 alpha-2 as used by api.worldbank.org.

    Returns None for empty, regional, dissolved or otherwise unmappable codes, so callers
    can count them instead of silently joining to the wrong country.
    """
    if not code:
        return None
    c = code.strip().upper()
    if is_regional(c) or c in DISSOLVED_OR_UNCOVERED:
        return None
    if c == "NULL":
        return None
    return WB_TO_ISO2.get(c, c if re.fullmatch(r"[A-Z]{2}", c) else None)
