"""Section-aware chunking (target ~500 tokens). Chunks never cross a section boundary; tables are separate chunks
(rows kept whole, header row repeated when a table is split); prose is packed by paragraph and split at sentence ends
only when a paragraph alone exceeds the maximum. Text is sanitised before it is stored."""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache

from . import config
from .sanitize import PERSONNEL, sanitize
from .text_extract import Page


@lru_cache(maxsize=1)
def _encoder():
    try:
        import tiktoken
        return tiktoken.get_encoding("cl100k_base"), "tiktoken:cl100k_base"
    except Exception:  # noqa: BLE001  (no network / no cache: deterministic approximation)
        return None, "approx:words*1.3"


def tokenizer_name() -> str:
    return _encoder()[1]


def count_tokens(text: str) -> int:
    enc, _ = _encoder()
    return len(enc.encode(text)) if enc else int(len(re.findall(r"\w+|[^\w\s]", text)) * 1.3)


def _clean_cell(c) -> str:
    return re.sub(r"\s+", " ", (c or "")).strip()


def units_for_section(pages: list[Page], section: dict, next_start: tuple[int, float] | None, start_pos: tuple[int, float]) -> list[dict]:
    """Paragraph and table units of one section, in reading order."""
    lines = [ln for p in pages for ln in p.lines]
    a, b = section["_line_range"]
    units = []
    para: list = []

    def flush():
        if para:
            text = ""
            for ln in para:
                t = ln.text.strip()
                text = (text[:-1] + t) if text.endswith("-") and t[:1].islower() else (f"{text} {t}" if text else t)
            units.append({"kind": "text", "text": text, "page_start": para[0].page, "page_end": para[-1].page, "pos": (para[0].page, para[0].y0)})
            para.clear()

    prev = None
    for ln in lines[a:b]:
        if ln.in_table:
            continue
        if prev is not None and (ln.page != prev.page or ln.block != prev.block):
            flush()
        para.append(ln)
        prev = ln
    flush()
    # a paragraph that continues in the next block or on the next page (no sentence end, next starts lower-case)
    merged: list[dict] = []
    for u in units:
        if merged and re.search(r"[A-Za-z0-9,;(\-–]$", merged[-1]["text"]) and re.match(r"^[a-z(]", u["text"]):
            merged[-1]["text"] = (merged[-1]["text"][:-1] if merged[-1]["text"].endswith("-") else merged[-1]["text"] + " ") + u["text"]
            merged[-1]["page_end"] = u["page_end"]
        else:
            merged.append(u)
    units = merged
    for p in pages:
        for ti, t in enumerate(p.tables):
            pos = (p.number, t["bbox"][1])
            if pos >= start_pos and (next_start is None or pos < next_start):
                rows = [[_clean_cell(c) for c in r] for r in t["rows"]]
                rows = [r for r in rows if any(r)]
                if rows:
                    units.append({"kind": "table", "rows": rows, "page_start": p.number, "page_end": p.number, "pos": pos, "table_index": ti + 1})
    units.sort(key=lambda u: u["pos"])
    return units


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.;:!?])\s+(?=[A-Z(\d])", text) if s]


def make_chunks(document: dict, section: dict, units: list[dict], labels: dict[int, str | None], start_index: int,
                inferred: dict[int, str] | None = None) -> list[dict]:
    """labels: printed page labels read from the PDF; inferred: labels interpolated between printed ones (kept apart)."""
    inferred = inferred or {}
    target, mx, mn = config.CHUNK_TARGET_TOKENS, config.CHUNK_MAX_TOKENS, config.CHUNK_MIN_TOKENS
    pieces: list[dict] = []        # (kind, text, page_start, page_end, table_index)
    for u in units:
        if u["kind"] == "table":
            header, body = u["rows"][0], u["rows"][1:] or []
            hdr = " | ".join(header)
            cur, cur_rows = [hdr], 0
            for r in body:
                line = " | ".join(r)
                if count_tokens("\n".join(cur + [line])) > mx and cur_rows:
                    pieces.append({"kind": "table", "text": "\n".join(cur), "ps": u["page_start"], "pe": u["page_end"], "ti": u["table_index"]})
                    cur, cur_rows = [hdr], 0
                cur.append(line)
                cur_rows += 1
            pieces.append({"kind": "table", "text": "\n".join(cur), "ps": u["page_start"], "pe": u["page_end"], "ti": u["table_index"]})
        else:
            t = u["text"]
            if count_tokens(t) <= mx:
                pieces.append({"kind": "text", "text": t, "ps": u["page_start"], "pe": u["page_end"], "ti": None})
            else:
                buf = ""
                for snt in _sentences(t):
                    cand = f"{buf} {snt}".strip()
                    if buf and count_tokens(cand) > target:
                        pieces.append({"kind": "text", "text": buf, "ps": u["page_start"], "pe": u["page_end"], "ti": None})
                        buf = snt
                    else:
                        buf = cand
                if buf:
                    pieces.append({"kind": "text", "text": buf, "ps": u["page_start"], "pe": u["page_end"], "ti": None})
    # pack consecutive text pieces up to the target; tables stay on their own
    packed: list[dict] = []
    for pc in pieces:
        last = packed[-1] if packed else None
        if (pc["kind"] == "text" and last and last["kind"] == "text"
                and count_tokens(last["text"] + "\n\n" + pc["text"]) <= target):
            last["text"] += "\n\n" + pc["text"]
            last["pe"] = pc["pe"]
        else:
            packed.append(dict(pc))
    # merge a short trailing text chunk into its predecessor when it fits under the maximum
    if len(packed) >= 2 and packed[-1]["kind"] == packed[-2]["kind"] == "text" and count_tokens(packed[-1]["text"]) < mn \
            and count_tokens(packed[-2]["text"] + "\n\n" + packed[-1]["text"]) <= mx:
        packed[-2]["text"] += "\n\n" + packed[-1]["text"]
        packed[-2]["pe"] = packed[-1]["pe"]
        packed.pop()
    out = []
    personnel = PERSONNEL.search(section["heading_text"]) is not None
    for k, pc in enumerate(packed):
        raw = f"{section['heading_text']}\n{pc['text']}" if k == 0 and section["heading_level"] else pc["text"]
        clean, counts = sanitize(raw, personnel_context=True if personnel else None)
        idx = start_index + len(out)
        out.append({
            "chunk_id": f"{document['document_id']}:c{idx:04d}", "document_id": document["document_id"], "project_id": document["project_id"],
            "country_code": document["country_code"], "doc_type": document["doc_type"], "section_id": section["section_id"],
            "section_path": section["section_path"], "chunk_type": pc["kind"], "table_index": pc["ti"],
            "physical_page_start": pc["ps"], "physical_page_end": pc["pe"],
            "printed_page_label_start": labels.get(pc["ps"]), "printed_page_label_end": labels.get(pc["pe"]),
            "printed_page_label_inferred_start": inferred.get(pc["ps"]) if not labels.get(pc["ps"]) else None,
            "heading_prefixed": bool(k == 0 and section["heading_level"]),
            "text": clean, "text_sha256": hashlib.sha256(clean.encode()).hexdigest(), "token_count": count_tokens(clean),
            "tokenizer": tokenizer_name(), "sanitisation": {k2: v for k2, v in counts.items() if v},
            "_raw_probe": (pc["text"][:60], pc["text"][-60:])})
    return out
