"""Helpers for STEP procurement-plan tables extracted from PDF (see scripts/p05).

pdfplumber reproduces cell line-wraps as injected spaces, so one logical column appears under
several header spellings ("Process Status", "Process St atus", "Pro ces s Sta tus") and values
carry injected spaces ("Open - Internationa l"). Phase 1 read only the first header variant and
under-counted populated cells; these helpers fix that (2026-10-21 alignment review).
"""
from __future__ import annotations

import re

import pandas as pd


def header_key(name: str) -> str:
    """Whitespace-insensitive, case-insensitive header key."""
    return re.sub(r"\s+", "", str(name)).lower()


def merge_header_variants(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse columns whose headers differ only by whitespace into one column.

    The merged value is the first non-empty value across variants (a row has at most one
    populated variant, because each source table uses a single spelling). The kept name is
    the variant with the fewest spaces.
    """
    groups: dict[str, list[str]] = {}
    for c in df.columns:
        groups.setdefault(header_key(c), []).append(c)
    out = {}
    for cols in groups.values():
        name = min(cols, key=lambda c: (c.count(" "), len(c)))
        if len(cols) == 1:
            out[name] = df[cols[0]]
        else:
            out[name] = df[cols].apply(
                lambda r: next((v for v in r if isinstance(v, str) and v.strip()), None), axis=1)
    return pd.DataFrame(out, index=df.index)


_STATUS_RULES = [
    ("cancelled", ("cancel", "annul")),
    ("terminated", ("terminat", "résili", "resili")),
    ("signed", ("signed", "signé", "signe", "assinado", "firmado")),
    ("completed", ("complet", "achev", "conclu")),
    ("draft", ("draft", "brouillon", "borrador")),
    ("under review", ("review", "exame", "revis")),
    ("under/pending implementation", ("implement", "exécut", "execut", "ejecu", "cours", "pending", "attente", "pendien")),
]


def normalize_plan_status(value) -> str:
    """Map multilingual STEP 'Process Status' values (with injected spaces) to one vocabulary.

    Returns 'blank' for empty values and 'unclassified' for garbled text (overlapping glyphs).
    """
    if value is None or (isinstance(value, float) and value != value):
        return "blank"
    s = re.sub(r"\s+", "", str(value)).lower()
    if not s:
        return "blank"
    for label, needles in _STATUS_RULES:
        if any(n in s for n in needles):
            return label
    return "unclassified"
