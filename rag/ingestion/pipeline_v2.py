"""Stage-1b ingestion (quality improvements + three chunking strategies) over the same 10 local PDFs.

  python -m rag.ingestion.pipeline_v2            # reuses the per-document cache when nothing changed
  python -m rag.ingestion.pipeline_v2 --force
  python -m rag.ingestion.pipeline_v2 --profile 2.1.0   # stacked-value cells -> rag/corpus_v3/processed/pilot_v2_1/
  python -m rag.ingestion.pipeline_v2 --profile 3.0.0   # 2.1.0 extraction over the approved 35-project scope -> pilot_v3/

Writes rag/corpus_v3/processed/pilot_v2/ (pilot_v1 is left untouched):
  shared/       projects, documents, document_versions, pages, sections, paragraphs, tables, table_rows,
                table_descriptions, plan_versions, plan_activities, evidence_links   (*.jsonl, sanitised)
  strategies/   A/chunks.jsonl, B/chunks.jsonl, C/parents.jsonl + C/chunks.jsonl
  restricted/   page_text.jsonl (original extracted text, the target of every source span), table_cells_original.jsonl,
                activity_cells_original.jsonl - unsanitised: never to be embedded, sent to an LLM or shared
  reference/    vocab.json (corpus word list for table-cell repair)
  run_manifest.json, by_document/ (cache)
Deterministic ids, sorted outputs, atomic writes; cache key = PDF SHA-256 + extraction version + code hash +
contracts-snapshot SHA-256 + vocabulary SHA-256.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import config
from . import contracts_snapshot, evidence, paragraphs, plan_tables, sections, strategies, tables, text_extract
from .cell_clean import build_vocab, clean_cell
from .chunking import count_tokens, tokenizer_name
from .pdf_validate import project_ids_in_text, validate
from .pipeline import _section_type
from . import sanitize as sanitize_mod
from .sanitize import PERSONNEL, mask_intervals, sanitize

EXTRACTION_VERSION = "ingest-pilot-2.0.0"
# profile -> (extraction version, default output directory, stacked_cells). 2.0.0 is the default and writes pilot_v2.
PROFILES = {"2.0.0": (EXTRACTION_VERSION, config.OUTPUT_V2, False),
            "2.1.0": ("ingest-pilot-2.1.0", config.OUTPUT_V2_1, True),
            "3.0.0": ("ingest-v3-2.1.0", config.OUTPUT_V3, True)}
V3_PROFILES = {"3.0.0"}              # these read the 35-project corpus manifest and contracts snapshot
SHARED = ("projects", "documents", "document_versions", "pages", "sections", "paragraphs", "tables", "table_rows", "table_descriptions",
          "plan_versions", "plan_activities", "evidence_links")
RESTRICTED = ("page_text", "table_cells_original", "activity_cells_original", "mask_events")
STRATS = ("A", "B", "C")


def code_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        h.update(p.read_bytes())
    h.update((config.REPO / "rag" / "module1_discovery" / "plan_text_rows.py").read_bytes())
    return h.hexdigest()[:16]


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _strip(rec):
    if isinstance(rec, dict):
        return {k: _strip(v) for k, v in rec.items() if not k.startswith("_")}
    if isinstance(rec, list):
        return [_strip(v) for v in rec]
    return rec


def vocabulary(corpus: list[dict], out: Path, extraction_version: str = EXTRACTION_VERSION) -> tuple[set[str], set[str], str]:
    key = hashlib.sha256(("|".join(sorted(r["sha256"] for r in corpus)) + extraction_version).encode()).hexdigest()
    path = out / "reference" / "vocab.json"
    if path.exists():
        v = json.loads(path.read_text())
        if v["key"] == key and "lower_words" in v:
            return set(v["words"]), set(v["lower_words"]), v["sha256"]
    texts = []
    for r in corpus:
        for p in text_extract.extract(config.REPO / r["local_path"]):
            texts.append(" ".join(ln.text for ln in p.lines if not ln.in_table))
    words = sorted(build_vocab(texts))
    lower = sorted({w for t in texts for w in re.findall(r"\b[a-z][a-z'’\-]{1,}\b", t)})   # words used in lower case
    sha = hashlib.sha256(("\n".join(words) + "\n#\n" + "\n".join(lower)).encode()).hexdigest()
    _atomic(path, json.dumps({"key": key, "sha256": sha, "words": words, "lower_words": lower,
                              "source": "prose (non-table) lines of the corpus PDFs + lexicon; lower_words: words seen in lower case"}))
    return set(words), set(lower), sha


def process(row: dict, scope: dict, contracts: list[dict], codes: set[str], idx: dict, vocab: set[str], lower_vocab: set[str],
            extraction_version: str = EXTRACTION_VERSION, stacked_cells: bool = False) -> dict:
    did, pid = row["document_id"], row["project_id"]
    path = config.REPO / row["local_path"]
    val = validate(path, row["sha256"])
    pages = text_extract.extract(path)
    inferred = text_extract.infer_labels(pages)
    labels = {p.number: p.printed_label for p in pages if p.printed_label}
    full = "\n".join(ln.text for p in pages for ln in p.lines)
    nn = lambda v: v if v not in ("", None) else None  # noqa: E731
    doc = {"document_id": did, "project_id": pid, "country_code": row["country_code"], "doc_type": row["doc_type"], "role": row["role"],
           "api": {"title": nn(row["title"]), "wds_document_date": nn(row["document_date"]), "wds_disclosure_date": nn(row["disclosure_date"]),
                   "language": nn(row["language"]), "wds_version_type": nn(row["version_type"]), "report_number": nn(row["report_number"]),
                   "guid": nn(row["guid"]), "source_url": nn(row["source_url"]), "final_url": nn(row["final_url_head"]),
                   "txt_url": nn(scope.get("txt_url")), "isr_sequence": nn(scope.get("isr_sequence"))},
           "pdf": {"project_ids_in_text": project_ids_in_text(full), "own_project_id_in_text": pid in project_ids_in_text(full),
                   "page_count": val.get("page_count"), "outline_entries": val.get("pdf_outline_entries"), "producer": val.get("pdf_producer"),
                   "creation_date": val.get("pdf_creation_date")},
           "inferred": {}, "extraction_version": extraction_version}
    version = {"document_version_id": f"{did}:{val['sha256'][:12]}", "document_id": did, "sha256": val["sha256"], "size_bytes": val["size_bytes"],
               "page_count": val.get("page_count"), "local_path": row["local_path"], "validation_errors": val["errors"],
               "download": {"source_url": nn(row["source_url"]), "downloaded_at": nn(row["downloaded_at"]), "manifest_sha256": nn(row["sha256"])},
               "pdf": {"pages_with_text": sum(p.chars >= 100 for p in pages), "low_text_pages": [p.number for p in pages if p.chars < 100],
                       "likely_scanned_pages": [p.number for p in pages if p.chars < 100 and p.image_cover > 0.5],
                       "toc_pages": [p.number for p in pages if p.is_toc], "printed_labels_found": len(labels)},
               "supersedes_document_id": None, "extraction_version": extraction_version}
    page_recs = [{"document_id": did, "page": p.number, "printed_page_label": p.printed_label, "printed_page_label_inferred": inferred.get(p.number),
                  "chars": p.chars, "image_cover": p.image_cover, "likely_scanned": p.chars < 100 and p.image_cover > 0.5, "is_toc": p.is_toc,
                  "tables_detected": len(p.tables), "footnote_lines": sum(ln.footnote for ln in p.lines),
                  "boilerplate_lines_removed": len(p.removed_lines), "orientation": "landscape" if p.width > p.height else "portrait",
                  "raw_text_sha256": hashlib.sha256(p.raw_text.encode()).hexdigest()} for p in pages]
    page_text = [{"document_id": did, "page": p.number, "raw_text": p.raw_text} for p in pages]
    heads = sections.detect(pages, row["doc_type"])
    secs = sections.build_sections(did, pages, heads)
    lines = [ln for p in pages for ln in p.lines]
    starts = [((lines[s["_line_range"][0] - 1].page, lines[s["_line_range"][0] - 1].y0) if s["heading_level"] else (0, -1.0)) for s in secs]
    units = paragraphs.units(pages, secs)
    head_unit = {u["section_id"]: u for u in units if u["kind"] == "heading"}

    def section_at(page: int, y: float) -> dict | None:
        cur = None
        for s, st in zip(secs, starts):
            if st <= (page, y):
                cur = s
        return cur

    for s in secs:
        hu = head_unit.get(s["section_id"])   # first character of the heading (a joined heading starts on its 'III.' line)
        sp0 = paragraphs.spans(hu, 0, len(hu["text"]))[0] if hu else None
        s["start_anchor"] = {"page": sp0["page"], "offset": sp0["start"]} if sp0 else {"page": s["physical_page_start"] if s["heading_level"] else 1, "offset": 0}
        s["section_type"] = _section_type(row["doc_type"], s)
        a, b = s["_line_range"]
        s["contains_personal_data_indicators"] = bool(PERSONNEL.search(" ".join(ln.text for ln in lines[a:b])))
        s["printed_page_label_start"] = labels.get(s["physical_page_start"])
        s["printed_page_label_end"] = labels.get(s["physical_page_end"])
        s["heading_text"] = sanitize(s["heading_text"], personnel_context=False)[0]
        s["section_path"] = sanitize(s["section_path"], personnel_context=False)[0]
    narrative = [s for s in secs if not s["section_type"].startswith("plan_table_")]
    nar_ids = {s["section_id"] for s in narrative}
    nar_units = [u for u in units if u["section_id"] in nar_ids]
    para_recs = []
    sanitize_mod.CAPTURE = events = []           # mask events of the authoritative units (paragraphs, table rows, plan descriptions)
    _mask_units(units)                           # one sanitisation per section, in reading order (label context crosses paragraphs)
    for u in units:
        t = u["text_sanitised"]
        para_recs.append({"paragraph_id": f"{did}:u{u['unit_id']:04d}", "document_id": did, "section_id": u["section_id"], "kind": u["kind"],
                          "text": t, "token_count": count_tokens(t), "source_spans": paragraphs.spans(u, 0, len(u["text"])),
                          "page_start": u["page_start"], "page_end": u["page_end"], "normalisation_ops": u["ops"],
                          "ends_sentence": paragraphs.ends_sentence(u["text"]), "mask_fallback": u.get("mask_fallback", False),
                          "sanitisation": {k: v for k, v in (("person_name", t.count("[PERSON]")), ("email", t.count("[EMAIL]")),
                                                            ("phone", t.count("[PHONE]"))) if v}})
    for e in events:
        e["source"] = "paragraph"
    n0 = len(events)
    tabs, trows, torig = tables.extract(did, path, pages, vocab, section_at, lower_vocab, stacked_cells=stacked_cells)
    for e in events[n0:]:
        e["source"] = "table_row"
    sanitize_mod.CAPTURE = None
    out = {"documents": [doc], "document_versions": [version], "pages": page_recs, "sections": secs, "paragraphs": para_recs, "tables": tabs,
           "table_rows": trows, "table_descriptions": _descriptions(doc, tabs, trows), "plan_versions": [], "plan_activities": [], "evidence_links": [],
           "page_text": page_text, "table_cells_original": torig, "activity_cells_original": [], "mask_events": []}
    for st in STRATS:
        ch, par = strategies.chunk(st, doc, narrative, nar_units, labels, inferred)
        out[f"chunks_{st}"] = ch
        if st == "C":
            out["parents_C"] = par
    if row["doc_type"] == "PP":
        facts = plan_tables.header_facts(path)
        prefixes = {c["borrower_contract_reference"][:2] for c in contracts if c["project_id"] == pid and c["borrower_contract_reference"]} | {row["country_code"]}
        prefixes = {p for p in prefixes if p.isalpha() and len(p) == 2}
        acts = plan_tables.activities(did, "PENDING", path, prefixes, codes)
        agency = plan_tables.reference_agency_code(acts)
        revision = max(facts["revised_plan_dates"]) if facts["revised_plan_dates"] else None
        stream = f"{pid}:{agency}" if agency else f"{pid}:document:{did}"
        pv_id = f"{stream}:{revision or 'undated:' + did}"
        out["plan_versions"].append({
            "plan_version_id": pv_id, "plan_stream_id": stream, "document_id": did, "project_id": pid, "pdf": facts | {"revision_date": revision},
            "api": {"wds_document_date": doc["api"]["wds_document_date"], "wds_disclosure_date": doc["api"]["wds_disclosure_date"]},
            "inferred": {"reference_agency_code": agency, "plan_stream_basis": "agency segment of the STEP activity references in this PDF"},
            "previous_version_id": None, "version_rule": "versions are ordered only within one plan_stream_id; different executing agencies are never treated as revisions",
            "activities": len(acts), "extraction_version": extraction_version})
        tid_of = {(t["page"], t["index_on_page"]): t["table_id"] for t in tabs}
        for act in acts:  # noqa: B007
            raw_ref, raw_cells = act.pop("_ref_cell_raw", ""), act.pop("_cells_raw", {})
            desc_raw = raw_ref.split(" / ", 1)[1] if " / " in raw_ref else (raw_ref.split("/\n", 1)[1] if "/\n" in raw_ref else "")
            desc_clean, _ = clean_cell(desc_raw, vocab)
            act.pop("description_text", None)
            sanitize_mod.CAPTURE = devents = []
            act["description_clean"], dcounts = sanitize(desc_clean)
            sanitize_mod.CAPTURE = None
            events.extend(dict(e, source="plan_description") for e in devents)
            act["description_clean"] = act["description_clean"] or None
            act["description_sanitisation"] = {k: v for k, v in dcounts.items() if v}
            for f, names in (("method_clean", ("Method",)), ("process_status_clean", ("Process Status",)), ("component_clean", ("Component",)),
                             ("market_approach_clean", ("Market Approach",))):
                rc = next((v for k, v in raw_cells.items() if k.replace(" ", "").lower() == names[0].replace(" ", "").lower()), "")
                act[f] = clean_cell(rc, vocab)[0] or None
            act["table_id"] = tid_of.get((act["pdf_page"], act["table_index"]))
            act["plan_version_id"], act["plan_stream_id"], act["project_id"] = pv_id, stream, pid
            out["activity_cells_original"].append({"activity_id": act["activity_id"], "reference_cell_original": raw_ref, "cells_original": raw_cells})
            out["evidence_links"].extend(evidence.link(pid, act, idx))
            act["row_text_for_embedding"] = sanitize(
                f"Procurement plan {did} ({stream}, revision {revision}) - {act['section'] or 'section unknown'}: {act['activity_reference_printed']} - "
                f"{act['description_clean'] or 'no description'}; review {act['review_type']}; method {act['method_clean']}; status {act['process_status']}; "
                f"estimated US$ {act['estimated_amount_usd']}; actual US$ {act['actual_amount_usd']}; signed contract planned {act['signed_contract_planned']}, "
                f"actual {act['signed_contract_actual']}.")[0]
            out["plan_activities"].append(act)
    seen = set()
    for e in events:
        k = (e["type"], e["rule"], e["original"], e["source"])
        if k not in seen:
            seen.add(k)
            out["mask_events"].append({"document_id": did, **e, "event_id": hashlib.sha256(f"{did}|{'|'.join(k)}".encode()).hexdigest()[:16]})
    return out


def _mask_units(units: list[dict]) -> None:
    """Sanitise each section's units together (one line per unit) and keep, per unit, the exact masked character ranges
    so every chunking strategy applies the same masks to any substring."""
    by_sec: dict[str, list[dict]] = {}
    for u in units:
        by_sec.setdefault(u["section_id"], []).append(u)
    for us in by_sec.values():
        clean, _ = sanitize("\n".join(u["text"] for u in us))
        lines = clean.split("\n")
        for i, u in enumerate(us):
            ln = lines[i] if len(lines) == len(us) else None
            m = mask_intervals(u["text"], ln) if ln is not None else None
            if m is None:                          # fall back to the unit alone (flagged)
                ln, _ = sanitize(u["text"])
                m = mask_intervals(u["text"], ln) or []
                u["mask_fallback"] = True
            u["masks"], u["text_sanitised"] = m, ln


def _descriptions(doc: dict, tabs: list[dict], rows: list[dict]) -> list[dict]:
    """One description per continuation group (a table and its continuations), for future embedding."""
    out = []
    for g in sorted({t["continuation_group"] for t in tabs}):
        members = [t for t in tabs if t["continuation_group"] == g]
        first = members[0]
        n_data = sum(t["n_data_rows"] for t in members)
        cols = [c for c in first["columns"] if not c.startswith("col_")][:12]
        sample = [r for r in rows if r["table_id"] == first["table_id"] and not r["is_header"]][:2]
        text = (f"Table in {doc['doc_type']} {doc['document_id']} ({doc['api']['title']}), section '{first['section_path'] or 'front matter'}', "
                f"pages {members[0]['page']}-{members[-1]['page']}, {len(members)} part(s), {n_data} data rows, kind {first['kind']}. "
                f"Caption: {first['caption'] or 'none'}. Columns: {'; '.join(cols) or 'not labelled'}. "
                f"Example rows: {' || '.join(' | '.join(c for c in r['cells_clean'] if c)[:200] for r in sample)}")
        clean, _ = sanitize(text)
        out.append({"table_group_id": g, "document_id": doc["document_id"], "table_ids": [t["table_id"] for t in members], "kind": first["kind"],
                    "pages": [members[0]["page"], members[-1]["page"]], "n_data_rows": n_data, "section_id": first["section_id"], "description": clean,
                    "token_count": count_tokens(clean)})
    return out


def run(force: bool = False, out_dir: Path | None = None, profile: str = "2.0.0") -> dict:
    extraction_version, default_out, stacked_cells = PROFILES[profile]
    out_dir = out_dir or default_out
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    corpus_manifest, _, _ = contracts_snapshot.paths(v3=profile in V3_PROFILES)
    with corpus_manifest.open(newline="") as fh:
        corpus = list(csv.DictReader(fh))
    with config.SCOPE_MANIFEST.open(newline="") as fh:
        scope = {r["document_id"]: r for r in csv.DictReader(fh)}
    with config.PROJECT_MANIFEST.open(newline="") as fh:
        projects = {r["project_id"]: r for r in csv.DictReader(fh)}
    contracts, snap = contracts_snapshot.load(v3=profile in V3_PROFILES)
    codes = evidence.method_codes(contracts)
    idx = evidence.index_contracts(contracts, codes)
    vocab, lower_vocab, vocab_sha = vocabulary(corpus, out_dir, extraction_version)
    chash = code_hash()
    keys = SHARED[1:] + RESTRICTED + tuple(f"chunks_{s}" for s in STRATS) + ("parents_C",)
    agg: dict[str, list] = {k: [] for k in keys}
    status = {}
    for row in sorted(corpus, key=lambda r: r["document_id"]):
        key = {"sha256": row["sha256"], "extraction_version": extraction_version, "code_hash": chash, "vocab_sha256": vocab_sha,
               "contracts_sha256": snap["sha256"] if row["doc_type"] == "PP" else None, "strategies": strategies.VERSIONS}
        cache = out_dir / "by_document" / f"{row['document_id']}.json"
        recs = None
        if cache.exists() and not force:
            c = json.loads(cache.read_text())
            if c.get("cache_key") == key:
                recs, status[row["document_id"]] = c["records"], "cached"
        if recs is None:
            try:
                recs = _strip(process(row, scope.get(row["document_id"], {}), contracts, codes, idx, vocab, lower_vocab,
                                      extraction_version, stacked_cells))
                _atomic(cache, json.dumps({"cache_key": key, "records": recs}, sort_keys=True, default=str))
                status[row["document_id"]] = "processed"
            except Exception as exc:  # noqa: BLE001
                status[row["document_id"]] = f"failed: {type(exc).__name__}: {str(exc)[:200]}"
                continue
        for k in keys:
            agg[k].extend(recs.get(k, []))
    projects_out = [{"project_id": p, "source": "rag_scope_v3_35_projects/PROJECT_SELECTION_35.csv (structured, not from PDFs)",
                     "api": {k: (projects.get(p, {}).get(k) or None) for k in ("project_title", "country_code", "country_name", "region",
                                                                               "income_group_fy2026", "sector", "approval_fy", "status")}}
                    for p in sorted({r["project_id"] for r in corpus})]
    sort_keys = {"documents": ("document_id",), "document_versions": ("document_id",), "pages": ("document_id", "page"), "sections": ("document_id", "order"),
                 "paragraphs": ("paragraph_id",), "tables": ("table_id",), "table_rows": ("table_id", "row_index"), "table_descriptions": ("table_group_id",),
                 "plan_versions": ("plan_version_id",), "plan_activities": ("activity_id",), "evidence_links": ("activity_id", "wb_contract_number"),
                 "page_text": ("document_id", "page"), "table_cells_original": ("table_id", "row_index"), "activity_cells_original": ("activity_id",),
                 "mask_events": ("event_id",),
                 "chunks_A": ("chunk_id",), "chunks_B": ("chunk_id",), "chunks_C": ("chunk_id",), "parents_C": ("chunk_id",)}
    targets = {**{k: out_dir / "shared" / f"{k}.jsonl" for k in SHARED}, **{k: out_dir / "restricted" / f"{k}.jsonl" for k in RESTRICTED},
               **{f"chunks_{s}": out_dir / "strategies" / s / "chunks.jsonl" for s in STRATS}, "parents_C": out_dir / "strategies" / "C" / "parents.jsonl"}
    agg["projects"] = projects_out
    outputs = {}
    for k, path in targets.items():
        rows = sorted(agg[k], key=lambda r: tuple(str(_get(r, f)) for f in sort_keys.get(k, ("project_id",))))
        body = "".join(json.dumps(r, sort_keys=True, default=str, ensure_ascii=False) + "\n" for r in rows)
        _atomic(path, body)
        outputs[str(path.relative_to(out_dir))] = {"rows": len(rows), "sha256": hashlib.sha256(body.encode()).hexdigest()}
    _atomic(out_dir / "restricted" / "README.txt",
            "Unsanitised original text (PDF page text, table cells, plan cells). Source spans point here.\n"
            "Do not embed, index, send to any LLM/API or share. Use the sanitised files in shared/ and strategies/.\n")
    manifest = {"extraction_version": extraction_version, "strategy_versions": strategies.VERSIONS, "strategy_config": strategies.CONFIG,
                **({"profile": profile, "stacked_cells": stacked_cells} if profile != "2.0.0" else {}),
                "code_hash": chash, "tokenizer": tokenizer_name(), "vocab_sha256": vocab_sha,
                "contracts_snapshot": {k: snap[k] for k in ("sha256", "rows", "source", "source_as_of_dates")},
                "inputs": [{"document_id": r["document_id"], "sha256": r["sha256"], "local_path": r["local_path"]} for r in corpus],
                "document_status": status, "outputs": outputs, "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _atomic(out_dir / "run_manifest.json", json.dumps(manifest, indent=1, sort_keys=True))
    return manifest


def _get(r: dict, f: str):
    return r.get(f)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--profile", choices=sorted(PROFILES), default="2.0.0")
    a = ap.parse_args()
    m = run(force=a.force, profile=a.profile)
    print(json.dumps({k: m[k] for k in ("document_status", "outputs", "tokenizer", "code_hash")}, indent=1))
