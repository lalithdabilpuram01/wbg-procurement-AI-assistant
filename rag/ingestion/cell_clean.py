"""Table-cell text repair: pdfplumber returns wrapped cell text with a newline where a narrow column broke the line, often
inside a word ('Request for Quota\\ntions', 'T\\nechnical', '9,890,912.0\\n7'). A break is closed without a space when
  * it splits a number or ISO date (digit / separator on both sides), or
  * the joined token is a known word and at least one side is not (vocabulary built from the corpus's own prose lines,
    which are not wrapped by table cells, plus a short procurement lexicon), or
  * neither side is a known word and the second part starts in lower case (a fragment).
Otherwise the break becomes a space. The original cell text is kept unchanged alongside the cleaned value.
"""
from __future__ import annotations

import re
import unicodedata

LEXICON = """request quotations quotation bids bidding proposals selection consultant consultants consulting individual quality
cost based least fixed budget direct contracting procurement process processes status implementation pending completed
signed cancelled canceled terminated envelope stage single open national international limited market approach review
prior post method estimated actual amount planned signed contract completion evaluation report recommendation award
invitation identified selected negotiated draft justification specific notice prequalification documents issued
submission opening minutes reference description component technical assistance institutional strengthening capacity
building supply installation commissioning equipment construction rehabilitation maintenance works goods services
non individual firms recruitment acquisition fournitures recrutement consultant individuel sélection fondée qualité
coût demande prix appel offres achevé annulé signé exécution attente""".split()


def build_vocab(texts: list[str]) -> set[str]:
    vocab = {w for w in LEXICON}
    for t in texts:
        for w in re.findall(r"[A-Za-zÀ-ÿ]{2,}", t):
            vocab.add(w.lower())
    return vocab


def _strip(w: str) -> str:
    return re.sub(r"[^A-Za-zÀ-ÿ]", "", unicodedata.normalize("NFKC", w)).lower()


def clean_cell(raw: str | None, vocab: set[str]) -> tuple[str, int]:
    """(cleaned value, number of in-word breaks closed)."""
    if not raw:
        return "", 0
    parts = [p.strip() for p in raw.replace("\r", "").split("\n")]
    parts = [p for p in parts if p]
    if not parts:
        return "", 0
    out, closed = parts[0], 0
    for nxt in parts[1:]:
        a_tok = re.findall(r"\S+$", out)[0]
        b_tok = re.findall(r"^\S+", nxt)[0]
        a, b = _strip(a_tok), _strip(b_tok)
        numeric = re.search(r"[\d,.\-/]$", out) and re.match(r"^[\d,.\-/]", nxt) and re.search(r"\d", a_tok + b_tok)
        word_break = False
        if a and b and out[-1:].isalpha() and nxt[:1].isalpha():
            joined = (a + b) in vocab
            if joined and (a not in vocab or b not in vocab or len(b) <= 2):
                word_break = True
            elif not joined and a not in vocab and b not in vocab and nxt[:1].islower():
                word_break = True
        hyphen = out.endswith("-") and nxt[:1].isalpha()      # 'Cost-' + 'Based' -> 'Cost-Based'
        if numeric or word_break or hyphen:
            out += nxt
            closed += 1
        else:
            out += " " + nxt
    return re.sub(r"\s+", " ", out).strip(), closed
