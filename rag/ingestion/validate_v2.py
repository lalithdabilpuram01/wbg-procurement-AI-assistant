"""Validation of pilot_v2 (writes pilot_v2/validation/metrics_v2.json). Independent checks only; no personal values output.

  python -m rag.ingestion.validate_v2

  * strategies A / B / C: counts, token distributions, boundary types, sentence-boundary preservation, section-boundary
    violations (source spans against section start anchors), page violations (B), parent/child integrity (C),
    paragraph coverage, citation accuracy (source spans re-read from restricted page text, and an independent PyMuPDF probe)
  * the 14 unresolved v1 probes, re-classified
  * tables: kinds, continuations, header handling, word repair (joins re-derived from the original cells), row values
    against plan activities
  * procurement-reference recall, evidence reconciliation with v1, privacy review, before/after against v1 metrics
"""
from __future__ import annotations

import collections
import json
import re
import statistics
from pathlib import Path

import fitz  # type: ignore[import-untyped]

from . import config, privacy_review
from .cell_clean import clean_cell
from .sanitize import TOKEN

V1_DIAG = Path("/private/tmp")   # not used for outputs; v1 probe diagnosis is recomputed below from pilot_v1


def _alnum(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def _dist(v):
    v = sorted(v)
    return {"n": len(v), "min": v[0], "p10": v[len(v) // 10], "median": statistics.median(v), "p90": v[int(0.9 * (len(v) - 1))], "max": v[-1]} if v else {}


def main(out: Path | None = None) -> dict:
    out = out or config.OUTPUT_V2
    ld = lambda p: [json.loads(x) for x in (out / p).read_text().splitlines()]  # noqa: E731
    docs = {d["document_id"]: d for d in ld("shared/documents.jsonl")}
    vers = {v["document_id"]: v for v in ld("shared/document_versions.jsonl")}
    secs = ld("shared/sections.jsonl")
    paras = ld("shared/paragraphs.jsonl")
    page_text = {(p["document_id"], p["page"]): p["raw_text"] for p in ld("restricted/page_text.jsonl")}
    pdf_text = {}
    for d, v in vers.items():
        with fitz.open(config.REPO / v["local_path"]) as doc:
            for i, pg in enumerate(doc, 1):
                pdf_text[(d, i)] = pg.get_text()
    m: dict = {}
    anchors = collections.defaultdict(list)
    for s in sorted(secs, key=lambda s: (s["document_id"], s["order"])):
        anchors[s["document_id"]].append((s["start_anchor"]["page"], s["start_anchor"]["offset"], s["section_id"]))
    sec_by = {s["section_id"]: s for s in secs}

    def section_of(d: str, page: int, off: int) -> str | None:
        cur = None
        for pg, o, sid in anchors[d]:
            if (pg, o) <= (page, off):
                cur = sid
        return cur

    def verify_spans(c: dict) -> bool:
        src = "".join(page_text[(c["document_id"], s["page"])][s["start"]:s["end"]] for s in c["source_spans"])
        flat = _alnum(src)
        pos = 0
        for seg in TOKEN.split(c["text"]):
            a = _alnum(seg)
            if not a:
                continue
            k = flat.find(a, pos)
            if k < 0:
                return False
            pos = k + len(a)
        return True

    def probe(c: dict) -> str:
        segs = [s for part in TOKEN.split(c["text"]) for s in part.split("\n\n") if len(_alnum(s)) >= 20]   # one paragraph, no mask
        if not segs:
            return "no_probe"
        p = _alnum(segs[0])[:40]
        pages = range(c["physical_page_start"], c["physical_page_end"] + 1)
        cited = _alnum(" ".join(pdf_text[(c["document_id"], pg)] for pg in pages))
        if p in cited:
            return "found_on_cited_pages"
        others = [pg for pg in range(1, docs[c["document_id"]]["pdf"]["page_count"] + 1) if pg not in pages and p in _alnum(pdf_text[(c["document_id"], pg)])]
        return "found_on_other_page" if others else "not_found_verbatim"

    para_cover = collections.defaultdict(int)
    for p in paras:
        if sec_by[p["section_id"]]["section_type"].startswith("plan_table_"):
            continue
        for s in p["source_spans"]:
            para_cover[p["document_id"]] += s["end"] - s["start"]
    strat = {}
    for S in ("A", "B", "C"):
        ch = ld(f"strategies/{S}/chunks.jsonl")
        res = {"chunks": len(ch), "tokens": _dist([c["token_count"] for c in ch]),
               "boundary_end": dict(collections.Counter(c["boundary_end"] for c in ch)),
               "boundary_start": dict(collections.Counter(c["boundary_start"] for c in ch))}
        ok_end = sum(c["boundary_end"] in ("sentence", "paragraph", "section_end") for c in ch)
        res["ends_at_sentence_paragraph_or_section"] = round(ok_end / len(ch), 3)
        res["mid_sentence_ends"] = sum(c["boundary_end"] in ("line", "forced") for c in ch)
        res["regex_terminal_punctuation_end"] = round(sum(bool(re.search(r"[.!?:;”\"’)\]]\s*$", c["text"])) for c in ch) / len(ch), 3)
        viol = 0
        for c in ch:
            sids = {section_of(c["document_id"], s["page"], s["start"]) for s in c["source_spans"]}
            viol += len(sids) != 1 or c["section_id"] not in sids
        res["section_boundary_violations"] = viol
        res["chunks_spanning_pages"] = sum(len({s["page"] for s in c["source_spans"]}) > 1 for c in ch)
        res["citation_spans_verified"] = [sum(verify_spans(c) for c in ch), len(ch)]
        pr = collections.Counter(probe(c) for c in ch)
        res["citation_probe_vs_pdf"] = dict(pr)
        covered = collections.defaultdict(set)
        for c in ch:
            for s in c["source_spans"]:
                covered[(c["document_id"], s["page"])].update(range(s["start"], s["end"]))
        cov = sum(len(v) for v in covered.values())
        tot = 0
        for p in paras:
            if sec_by[p["section_id"]]["section_type"].startswith("plan_table_"):
                continue
            for s in p["source_spans"]:
                raw = page_text[(p["document_id"], s["page"])]
                cv = covered.get((p["document_id"], s["page"]), set())
                tot += sum(1 for i in range(s["start"], s["end"]) if i not in cv and raw[i].isalnum())
        res["paragraph_alnum_characters_not_covered"] = tot      # letters / digits of narrative paragraphs missing from every chunk
        res["over_size"] = {"A": sum(c["token_count"] > 650 for c in ch), "B": sum(c["token_count"] > 500 for c in ch),
                            "C": sum(c["token_count"] > 400 + c["overlap_tokens_with_previous"] for c in ch)}[S]
        res["under_size"] = {"A": sum(c["token_count"] < 80 for c in ch), "B": sum(c["token_count"] < 300 for c in ch), "C": sum(c["token_count"] < 250 for c in ch)}[S]
        res["under_size_whole_section"] = sum(1 for c in ch if (c["token_count"] < {"A": 80, "B": 300, "C": 250}[S]) and
                                              sum(x["section_id"] == c["section_id"] for x in ch) == 1)
        if S == "C":
            parents = {p["chunk_id"]: p for p in ld("strategies/C/parents.jsonl")}
            res["parents"] = {"count": len(parents), "tokens": _dist([p["token_count"] for p in parents.values()]),
                              "over_1500": sum(p["token_count"] > 1500 for p in parents.values()),
                              "under_800": sum(p["token_count"] < 800 for p in parents.values()),
                              "under_800_whole_top_level_section": sum(p["token_count"] < 800 and p["boundary_start"] == "section_start" and p["boundary_end"] == "section_end"
                                                                      for p in parents.values()),
                              "multi_subsection": sum(len(p["section_ids"]) > 1 for p in parents.values()),
                              "cross_top_level_section": sum(len({sec_by[s]["section_path"].split(" > ")[0] for s in p["section_ids"]}) > 1 for p in parents.values()),
                              "citation_spans_verified": sum(verify_spans(p) for p in parents.values())}
            integ = collections.Counter()
            for c in ch:
                p = parents.get(c["parent_id"])
                integ["children"] += 1
                integ["parent_exists"] += p is not None
                if p:
                    pspan = {(s["page"], i) for s in p["source_spans"] for i in range(s["start"], s["end"])}
                    integ["child_inside_parent"] += all((s["page"], i) in pspan for s in c["source_spans"] for i in range(s["start"], s["end"]))
                    integ["child_section_in_parent"] += c["section_id"] in p["section_ids"]
            res["parent_child_integrity"] = dict(integ)
            res["overlap"] = {"children_with_overlap": sum(c["overlap_tokens_with_previous"] > 0 for c in ch),
                              "overlap_tokens": _dist([c["overlap_tokens_with_previous"] for c in ch if c["overlap_tokens_with_previous"] > 0])}
        strat[S] = res
    m["strategies"] = strat

    # the 14 v1 probes: re-classified against v1 chunks and the PDF
    v1 = [json.loads(x) for x in (config.OUTPUT / "chunks.jsonl").read_text().splitlines()]
    v1_res = collections.Counter()
    detail = []
    for c in v1:
        if c["chunk_type"] == "table_row":
            continue
        body = c["text"].split("\n", 1)[1] if c.get("heading_prefixed") and "\n" in c["text"] else c["text"]
        if c["chunk_type"] == "table":
            cells = [x for x in re.split(r"\s*\|\s*|\n", body) if len(_alnum(x)) >= 12 and "[" not in x]
            pr = _alnum(cells[0])[:30] if cells else ""
        else:
            words = [w for w in re.split(r"\s+", body) if w and "[" not in w and "|" not in w]
            pr = _alnum(" ".join(words[:8]))[:40]
        if len(pr) < 12:
            continue
        pages = range(c["physical_page_start"], c["physical_page_end"] + 1)
        span = _alnum(" ".join(pdf_text[(c["document_id"], pg)] for pg in pages))
        if pr in span:
            continue
        masked_gap = bool(re.search(r"\[(PERSON|EMAIL|PHONE)\]", " ".join(body.split()[:12])))
        half = (pr[:len(pr) // 2] in span, pr[len(pr) // 2:] in span)
        other = any(pr in _alnum(pdf_text[(c["document_id"], pg)]) for pg in range(1, docs[c["document_id"]]["pdf"]["page_count"] + 1) if pg not in pages)
        cause = ("wrong_page" if other else "probe_skipped_masked_tokens" if masked_gap else
                 "reading_order_or_join (halves on cited page)" if any(half) else "text_normalisation")
        v1_res[cause] += 1
        detail.append({"v1_chunk_id": c["chunk_id"], "pages": [c["physical_page_start"], c["physical_page_end"]], "cause": cause})
    v2_on_same_pages = collections.Counter()
    a_chunks = ld("strategies/A/chunks.jsonl")
    for dct in detail:
        d = dct["v1_chunk_id"].split(":")[0]
        cand = [c for c in a_chunks if c["document_id"] == d and c["physical_page_start"] <= dct["pages"][1] and c["physical_page_end"] >= dct["pages"][0]]
        dct["v2_A_chunks_on_pages"] = len(cand)
        dct["v2_A_spans_verified"] = sum(verify_spans(c) for c in cand)
        v2_on_same_pages["verified" if cand and dct["v2_A_spans_verified"] == len(cand) else "not_all_verified"] += 1
    m["v1_unresolved_probes"] = {"count": len(detail), "causes": dict(v1_res), "v2_A_chunks_on_the_same_pages": dict(v2_on_same_pages), "detail": detail}

    # tables
    tabs = ld("shared/tables.jsonl")
    rows = ld("shared/table_rows.jsonl")
    orig = {(o["table_id"], o["row_index"]): o["cells_original"] for o in ld("restricted/table_cells_original.jsonl")}
    vocab = set(json.loads((out / "reference" / "vocab.json").read_text())["words"])
    tab_by = {t["table_id"]: t for t in tabs}
    cont = [t for t in tabs if t["continuation_of"]]
    cont_checks = collections.Counter()
    for t in cont:
        p = tab_by[t["continuation_of"]]
        cont_checks["continuations"] += 1
        cont_checks["same_column_count"] += t["n_columns"] == p["n_columns"]
        first = next((r for r in rows if r["table_id"] == t["table_id"] and r["row_index"] == 1), None)
        hdr = [c for r in p["header_clean"] for c in r]
        cont_checks["first_row_kept_as_data"] += bool(first) and not first["is_header"]
        cont_checks["first_row_differs_from_parent_header"] += bool(first) and first["cells_clean"] != (p["header_clean"][0] if p["header_clean"] else None)
        if t["kind"] == "step_plan":
            cont_checks["step_continuation_first_row_has_reference"] += bool(first) and bool(re.search(r"\d{5,7}-(CW|GO|CS|NC|TA)-", re.sub(r"\s+", "", first["cells_clean"][0])))
            cont_checks["step_continuations"] += 1
        _ = hdr
    joins = collections.Counter()
    frag_before = frag_after = 0
    for (tid, ri), cells in orig.items():
        if tab_by[tid]["kind"] == "staff":
            continue          # no inspection of staff-table text
        for c in cells:
            if not c or "\n" not in c:
                continue
            parts = [x.strip() for x in c.split("\n") if x.strip()]
            for a, b in zip(parts, parts[1:]):
                single, _n = clean_cell(a + "\n" + b, vocab)
                closed = _n > 0
                at, bt = re.findall(r"\S+$", a)[0], re.findall(r"^\S+", b)[0]
                if not closed:
                    joins["kept_as_space"] += 1
                    continue
                joined = re.sub(r"[^A-Za-zÀ-ÿ]", "", at + bt).lower()
                kind = ("number_or_date" if re.search(r"\d", at + bt) else "hyphenated" if a.endswith("-") else
                        "known_word" if joined in vocab else "fragment_pair")
                joins[kind] += 1
            raw_tokens = re.findall(r"[A-Za-zÀ-ÿ]+", c.replace("\n", " "))
            cl_tokens = re.findall(r"[A-Za-zÀ-ÿ]+", clean_cell(c, vocab)[0])
            frag_before += sum(1 for w in raw_tokens if w.lower() not in vocab and len(w) >= 2)
            frag_after += sum(1 for w in cl_tokens if w.lower() not in vocab and len(w) >= 2)
    acts = ld("shared/plan_activities.jsonl")
    row_by = {(r["table_id"], r["row_index"]): r for r in rows}
    val = collections.Counter()
    for a in acts:
        r = row_by.get((a["table_id"], a["row_index"]))
        if not r:
            val["activity_without_table_row"] += 1
            continue
        val["activities_with_table_row"] += 1
        flat = re.sub(r"[\s,]", "", " ".join(r["cells_clean"]))
        if a["actual_amount_usd"] is not None:
            val["actual_amount_in_row"] += f"{a['actual_amount_usd']:.2f}" in flat
            val["actual_amount_checked"] += 1
        if a["signed_contract_actual"]:
            val["signed_actual_in_row"] += a["signed_contract_actual"] in flat
            val["signed_actual_checked"] += 1
        if a["package_reference_core"]:
            val["reference_in_row"] += a["package_reference_core"].split("-")[-3] in flat
            val["reference_checked"] += 1
    descs = ld("shared/table_descriptions.jsonl")
    m["tables"] = {"tables": len(tabs), "by_kind": dict(collections.Counter(t["kind"] for t in tabs)), "continuation_groups": len(descs),
                   "continuation_tables": len(cont), "header_inherited": sum(t["header_inherited"] for t in tabs),
                   "header_repeated_on_continuation": sum(bool(t["continuation_of"]) and t["header_rows"] > 0 for t in tabs),
                   "continuation_checks": dict(cont_checks), "rows": len(rows), "header_rows": sum(r["is_header"] for r in rows),
                   "word_breaks_closed": sum(r["word_breaks_closed"] for r in rows), "line_break_decisions_non_staff_tables": dict(joins),
                   "out_of_vocabulary_tokens_non_staff_tables": {"before_repair": frag_before, "after_repair": frag_after},
                   "rows_per_table": _dist([t["n_rows_total"] for t in tabs]), "description_tokens": _dist([d["token_count"] for d in descs]),
                   "plan_rows_vs_activities": dict(val)}

    # recall, reconciliation
    rec = {}
    for pv in ld("shared/plan_versions.jsonl"):
        d = pv["document_id"]
        in_text = {(pg, mm.group(1), mm.group(2)) for (dd, pg), t in pdf_text.items() if dd == d
                   for mm in re.finditer(r"-(\d{5,7})-(CW|GO|CS|NC|TA)-", re.sub(r"\s+", "", t))}
        got = {(a["pdf_page"], *re.search(r"-(\d{5,7})-(CW|GO|CS|NC|TA)-", a["package_reference_core"]).groups()) for a in acts
               if a["document_id"] == d and a["package_reference_core"]}
        rec[d] = {"in_page_text": len(in_text), "extracted": len(got), "recall": round(len(in_text & got) / len(in_text), 3) if in_text else None}
    m["reference_recall"] = rec
    links = ld("shared/evidence_links.jsonl")
    v1_links = [json.loads(x) for x in (config.OUTPUT / "evidence_links.jsonl").read_text().splitlines()]
    v1_acts = [json.loads(x) for x in (config.OUTPUT / "plan_activities.jsonl").read_text().splitlines()]
    m["reconciliation"] = {
        "activities": {"v1": len(v1_acts), "v2": len(acts), "same_ids": {a["activity_id"] for a in v1_acts} == {a["activity_id"] for a in acts}},
        "evidence_links": {"v1": len(v1_links), "v2": len(links),
                           "same_status_per_link": sum(1 for a, b in zip(sorted(v1_links, key=lambda x: x["link_id"]), sorted(links, key=lambda x: x["link_id"]))
                                                       if a["link_id"] == b["link_id"] and a["evidence_status"] == b["evidence_status"])},
        "status_counts": dict(collections.Counter(l["evidence_status"] for l in links)),
        "unique_contracts_linked": len({l["wb_contract_number"] for l in links if l["wb_contract_number"]}),
        "activities_with_contract": len({l["activity_id"] for l in links if l["wb_contract_number"]}),
        "lot_links": sum(1 for l in links if l["cardinality"] and l["cardinality"] != "one_row_one_contract"),
        "activities_linked_to_table_rows": sum(1 for a in acts if a["table_id"])}

    # privacy and before / after
    m["privacy"] = privacy_review.run(out)
    v1m = json.loads((config.OUTPUT / "validation" / "metrics.json").read_text())
    a = strat["A"]
    m["before_after"] = {
        "text_chunk_regex_sentence_end": {"v1": v1m["chunk_quality"]["text_chunks_end_at_sentence_or_paragraph"], "v2_A": a["regex_terminal_punctuation_end"]},
        "text_chunks_ending_at_sentence_paragraph_or_section": {"v1": None, "v2_A": a["ends_at_sentence_paragraph_or_section"]},
        "mid_sentence_chunk_ends": {"v1": None, "v2_A": a["mid_sentence_ends"]},
        "citation_chunks_located": {"v1": v1m["citation_page_accuracy"]["chunks_text_found_on_cited_pages"],
                                    "v2_A_spans_verified": a["citation_spans_verified"], "v2_A_probe": a["citation_probe_vs_pdf"]},
        "split_tables_header_handling": {"v1": "first row of every continuation table used as header",
                                         "v2": f"{m['tables']['header_inherited']} continuation tables inherit their real header"},
        "table_cell_word_breaks": {"v1": "not repaired", "v2_closed": m["tables"]["word_breaks_closed"]},
        "privacy_ground_truth_residual": {"v1": {"staff_table": v1m["sanitisation"]["staff_table_names_remaining_in_chunks"],
                                                 "label_adjacent": v1m["sanitisation"]["label_adjacent_names_remaining_in_chunks"]},
                                          "v2": {k: v["ground_truth_names_present"] for k, v in m["privacy"]["possible_misses"].items()}},
        "privacy_false_positive_masks": {"v1": "not measured (48 label-window masks; 2 of 18 sampled were non-names)",
                                         "v2": m["privacy"]["person_masks_by_class"]},
        "section_detection": {"v1": "100% (10/10 documents)", "v2": "same detector, 234 sections"},
        "reference_recall": {"v1": {d: v["recall"] for d, v in v1m["reference_recall"].items()}, "v2": {d: v["recall"] for d, v in rec.items()}}}
    m["privacy"].pop("all_false_positive_strings", None)
    (out / "validation").mkdir(exist_ok=True)
    (out / "validation" / "metrics_v2.json").write_text(json.dumps(m, indent=1, sort_keys=True, default=str))
    return m


if __name__ == "__main__":
    r = main()
    print(json.dumps({k: v for k, v in r.items() if k not in ("v1_unresolved_probes",)}, indent=1, default=str)[:8000])
