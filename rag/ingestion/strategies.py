"""Three configurable chunking strategies over the same cleaned paragraph units (narrative text; tables and plan
activities have their own structured outputs and are never split here).

  A  baseline            section-aware packing of whole sentences, target ~500 tokens (max 650); paragraph ends preferred
  B  page-aware          recursive (paragraph -> sentence -> words) inside one page and one section, 300-500 tokens
  C  parent + child      parents of 800-1500 tokens inside one section (several bounded parents for long sections);
                         children of 250-400 tokens inside one parent; 30-50 tokens of overlap only when a child boundary
                         falls inside a paragraph

Every chunk: deterministic id '<document_id>:<strategy>:<n>', strategy + version, section id / path, exact source spans
(page, start, end in the original page text), physical pages and printed labels, boundary type at start and end,
sanitised text, token count. Chunks never cross a section boundary.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .chunking import count_tokens, tokenizer_name
from .paragraphs import ends_sentence, sentences, spans
from .sanitize import apply_masks, sanitize

VERSIONS = {"A": "A-1.0", "B": "B-1.0", "C": "C-1.0"}
CONFIG = {"A": {"target": 500, "max": 650, "min": 80}, "B": {"min": 300, "target": 450, "max": 500},
          "C": {"parent_min": 800, "parent_max": 1500, "child_min": 250, "child_target": 350, "child_max": 400, "overlap_min": 30, "overlap_max": 50}}


@dataclass
class Piece:
    unit: dict
    a: int
    b: int
    kind: str                  # sentence | heading | footnote
    para_end: bool             # last sentence of its paragraph
    tokens: int

    @property
    def text(self) -> str:
        return self.unit["text"][self.a:self.b]


def _pieces(units: list[dict]) -> list[Piece]:
    out = []
    for u in units:
        if u["kind"] in ("heading", "footnote"):
            out.append(Piece(u, 0, len(u["text"]), u["kind"], True, count_tokens(u["text"])))
            continue
        sents = sentences(u["text"])
        for k, (a, b) in enumerate(sents):
            t = u["text"][a:b]
            if count_tokens(t) > 400:               # a run-on 'sentence' (lists, tables flattened): split on words
                words, start, cur = t.split(" "), a, a
                buf: list[str] = []
                for w in words:
                    if buf and count_tokens(" ".join(buf + [w])) > 300:
                        out.append(Piece(u, start, cur - 1, "sentence_forced_split", False, count_tokens(" ".join(buf))))
                        start, buf = cur, []
                    buf.append(w)
                    cur += len(w) + 1
                out.append(Piece(u, start, b, "sentence", k == len(sents) - 1, count_tokens(" ".join(buf))))
            else:
                out.append(Piece(u, a, b, "sentence", k == len(sents) - 1, count_tokens(t)))
    return out


def _join(pieces: list[Piece], masked: bool = False) -> str:
    out = ""
    for i, p in enumerate(pieces):
        if i and (pieces[i - 1].unit is not p.unit):
            out += "\n\n"
        elif i:
            out += " "
        out += apply_masks(p.unit["text"], p.a, p.b, p.unit.get("masks", [])) if masked else p.text
    return out


def _record(strategy: str, doc: dict, section: dict, pieces: list[Piece], idx: int, labels: dict, inferred: dict, start_boundary: str,
            parent_id: str | None = None, overlap_tokens: int = 0) -> dict:
    raw = _join(pieces)
    # the unit-level masks (sanitised per section in reading order) are applied to the chunk's substrings, so all
    # strategies mask identically; a contact-data pass (e-mail / phone only) runs again as a safety net
    text, _ = sanitize(_join(pieces, masked=True), personnel_context=False)
    counts = {"person_name": text.count("[PERSON]"), "email": text.count("[EMAIL]"), "phone": text.count("[PHONE]")}
    sp = []
    for p in pieces:
        sp.extend(spans(p.unit, p.a, p.b))
    pages = [s["page"] for s in sp] or [section["physical_page_start"]]
    last = pieces[-1]
    end_boundary = ("section_end" if last is section.get("_last_piece") else "paragraph" if last.para_end else
                    "forced" if last.kind == "sentence_forced_split" else "sentence" if ends_sentence(last.text) else "line")
    rec = {
        "chunk_id": f"{doc['document_id']}:{strategy}:{idx:04d}", "strategy": strategy, "strategy_version": VERSIONS[strategy],
        "document_id": doc["document_id"], "project_id": doc["project_id"], "country_code": doc["country_code"], "doc_type": doc["doc_type"],
        "section_id": section["section_id"], "section_path": section["section_path"], "parent_id": parent_id,
        "physical_page_start": min(pages), "physical_page_end": max(pages),
        "printed_page_label_start": labels.get(min(pages)), "printed_page_label_end": labels.get(max(pages)),
        "printed_page_label_inferred_start": None if labels.get(min(pages)) else inferred.get(min(pages)),
        "source_spans": sp, "boundary_start": start_boundary, "boundary_end": end_boundary, "overlap_tokens_with_previous": overlap_tokens,
        "contains_footnote": any(p.kind == "footnote" for p in pieces), "text": text,
        "text_normalized_sha256": hashlib.sha256(raw.encode()).hexdigest(), "token_count": count_tokens(text), "tokenizer": tokenizer_name(),
        "sanitisation": {k: v for k, v in counts.items() if v}}
    return rec


def _pack(pieces: list[Piece], target: int, maximum: int, minimum: int) -> list[list[Piece]]:
    """Greedy packing of whole pieces; closes a group at a paragraph end once it reaches 60% of the target, otherwise at
    the target; a group never exceeds the maximum unless a single piece does."""
    groups: list[list[Piece]] = []
    cur: list[Piece] = []
    tok = 0
    for p in pieces:
        if cur and tok + p.tokens > maximum:
            groups.append(cur)
            cur, tok = [], 0
        cur.append(p)
        tok += p.tokens
        if (p.para_end and tok >= 0.6 * target) or tok >= target:
            groups.append(cur)
            cur, tok = [], 0
    if cur:
        if groups and tok < minimum and sum(x.tokens for x in groups[-1]) + tok <= maximum:
            groups[-1].extend(cur)
        else:
            groups.append(cur)
    return groups


def chunk(strategy: str, doc: dict, sections: list[dict], units: list[dict], labels: dict, inferred: dict) -> tuple[list[dict], list[dict]]:
    """Returns (chunks, parents). Parents only for strategy C."""
    if strategy == "C":
        return _parent_child(doc, sections, units, labels, inferred)
    chunks: list[dict] = []
    for s in sections:
        su = [u for u in units if u["section_id"] == s["section_id"]]
        pcs = _pieces(su)
        if not pcs:
            continue
        s["_last_piece"] = pcs[-1]
        if strategy == "A":
            cfg = CONFIG["A"]
            for k, g in enumerate(_pack(pcs, cfg["target"], cfg["max"], cfg["min"])):
                chunks.append(_record("A", doc, s, g, len(chunks), labels, inferred, "section_start" if k == 0 else _start(g)))
        elif strategy == "B":
            cfg = CONFIG["B"]
            by_page: dict[int, list[Piece]] = {}
            for p in pcs:   # a sentence that crosses a page break is cut at the break
                for part in _split_at_pages(p):
                    by_page.setdefault(spans(part.unit, part.a, part.b)[0]["page"], []).append(part)
            first = True
            for pg in sorted(by_page):
                for j, g in enumerate(_pack(by_page[pg], cfg["target"], cfg["max"], cfg["min"])):
                    chunks.append(_record("B", doc, s, g, len(chunks), labels, inferred, "section_start" if first else "page" if j == 0 else _start(g)))
                    first = False
        s.pop("_last_piece", None)
    return chunks, []


def _groups(sections: list[dict]) -> list[list[dict]]:
    """Consecutive sections under the same top-level heading (a level-1 section and its level-2 subsections)."""
    out: list[list[dict]] = []
    for s in sections:
        top = s["section_path"].split(" > ")[0]
        if out and s["heading_level"] == 2 and out[-1][0]["section_path"].split(" > ")[0] == top:
            out[-1].append(s)
        else:
            out.append([s])
    return out


def _parent_child(doc: dict, sections: list[dict], units: list[dict], labels: dict, inferred: dict) -> tuple[list[dict], list[dict]]:
    """Parents (800-1500 tokens) pack whole subsections / paragraphs of one top-level section group; children (250-400)
    pack sentences of one subsection inside one parent, with 30-50 tokens of overlap only inside a paragraph."""
    cfg = CONFIG["C"]
    chunks, parents = [], []
    for grp in _groups(sections):
        seq = []                                       # (section, pieces) in order
        for s in grp:
            pcs = _pieces([u for u in units if u["section_id"] == s["section_id"]])
            if pcs:
                s["_last_piece"] = pcs[-1]
                seq.append((s, pcs))
        if not seq:
            continue
        # parent packing over the group's pieces, closing preferably at subsection ends, then paragraph ends
        flat = [(s, p) for s, pcs in seq for p in pcs]
        groups, cur, tok = [], [], 0
        for i, (s, p) in enumerate(flat):
            if cur and tok + p.tokens > cfg["parent_max"]:
                groups.append(cur)
                cur, tok = [], 0
            cur.append((s, p))
            tok += p.tokens
            sec_end = i + 1 == len(flat) or flat[i + 1][0] is not s
            if tok >= cfg["parent_min"] and (sec_end or (p.para_end and tok >= (cfg["parent_min"] + cfg["parent_max"]) // 2)):
                groups.append(cur)
                cur, tok = [], 0
        if cur:
            if groups and tok < cfg["parent_min"] // 2 and sum(x[1].tokens for x in groups[-1]) + tok <= cfg["parent_max"]:
                groups[-1].extend(cur)
            else:
                groups.append(cur)
        for g in groups:
            pid = f"{doc['document_id']}:C:p{len(parents):03d}"
            first_sec = g[0][0]
            prec = _record("C", doc, first_sec, [p for _, p in g], len(parents), labels, inferred,
                           "section_start" if g[0][1] is _first_piece(seq, first_sec) else _start([g[0][1]]))
            prec.update(chunk_id=pid, parent_id=None, record_type="parent", section_ids=sorted({s["section_id"] for s, _ in g}, key=lambda x: x),
                        section_path=first_sec["section_path"].split(" > ")[0] if len({s["section_id"] for s, _ in g}) > 1 else first_sec["section_path"])
            child_ids = []
            for s in [x for x, _ in seq if any(y is x for y, _ in g)]:
                sp = [p for y, p in g if y is s]
                kids = _pack(sp, cfg["child_target"], cfg["child_max"], cfg["child_min"] // 2)
                for k, kid in enumerate(kids):
                    overlap = []
                    if k and not kids[k - 1][-1].para_end:          # boundary inside a paragraph: carry 30-50 tokens back
                        for p in reversed(kids[k - 1]):
                            if sum(x.tokens for x in overlap) + p.tokens > cfg["overlap_max"]:
                                break
                            overlap.insert(0, p)
                        if sum(x.tokens for x in overlap) < cfg["overlap_min"]:
                            overlap = []
                    start = "section_start" if kid[0] is _first_piece(seq, s) else "overlap" if overlap else _start(kid)
                    rec = _record("C", doc, s, overlap + kid, len(chunks), labels, inferred, start, parent_id=pid,
                                  overlap_tokens=sum(x.tokens for x in overlap))
                    rec["record_type"] = "child"
                    chunks.append(rec)
                    child_ids.append(rec["chunk_id"])
            prec["child_ids"] = child_ids
            parents.append(prec)
        for s, _ in seq:
            s.pop("_last_piece", None)
    return chunks, parents


def _first_piece(seq, s):
    return next(p[0] for x, p in seq if x is s)


def _start(g: list[Piece]) -> str:
    return "paragraph" if g[0].a == 0 or g[0].kind in ("heading", "footnote") else "sentence"


def _split_at_pages(p: Piece) -> list[Piece]:
    sp = spans(p.unit, p.a, p.b)
    if len({s["page"] for s in sp}) <= 1:
        return [p]
    out, pos = [], p.a
    # cut at the first character of each new page inside the piece
    cuts = []
    prev_pg = None
    for ns, ne, pg, rs in p.unit["segs"]:
        if ne <= p.a or ns >= p.b:
            continue
        if prev_pg is not None and pg != prev_pg:
            cuts.append(max(ns, p.a))
        prev_pg = pg
    for c in cuts + [p.b]:
        t = p.unit["text"][pos:c]
        if t.strip():
            out.append(Piece(p.unit, pos, c, p.kind, p.para_end and c == p.b, count_tokens(t)))
        pos = c
    return out
