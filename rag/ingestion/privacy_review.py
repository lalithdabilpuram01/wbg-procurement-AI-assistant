"""Privacy masking review (no personal values are printed or written outside restricted/).

Reads restricted/mask_events.jsonl and the restricted original text, then:
  * classifies every person-name mask with corpus evidence (never by printing it):
      ground_truth_person   the masked string is a staff-table 'Name' cell, a unit-code staff row name, or a name right
                            after a role label in the original page text
      likely_false_positive every word of the masked string also occurs in lower case in the corpus prose (common words,
                            e.g. procurement terminology), and it is not a ground-truth name
      likely_person         otherwise (capitalised words never used in lower case - typical of personal names)
  * draws a deterministic, rule-stratified sample of 50 masks and writes it with the originals to
    restricted/privacy_review_sample.csv for an authorised human reviewer (decision column left empty);
  * counts possible missed personal information in the sanitised outputs: ground-truth names still present, titles
    followed by a capitalised word, and capitalised name-like runs on lines with staff roles.
Only counts, rule names, ids and strings classified as common-word false positives are returned.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import json
import re
from pathlib import Path

from .sanitize import LABEL, NAME, STOP, _is_name

ROLE_LINE = re.compile(r"\b(Specialist|Consultant|Engineer|Economist|Officer|Counsel|Analyst|Leader|Manager|Director|Coordinator|Advisor|Adviser)\b")
TITLE = re.compile(r"\b(?:Mr|Ms|Mrs|Dr)\.?\s+[A-Z][a-z]+")


def _alnum(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def run(out: Path) -> dict:
    ld = lambda p: [json.loads(x) for x in (out / p).read_text().splitlines()]  # noqa: E731
    events = [e for e in ld("restricted/mask_events.jsonl")]
    page_text = ld("restricted/page_text.jsonl")
    cells = ld("restricted/table_cells_original.jsonl")
    # lower-case vocabulary of the corpus prose: words that occur in lower case somewhere
    lower_vocab = set()
    for p in page_text:
        lower_vocab.update(w for w in re.findall(r"\b[a-z][a-z'’\-]{1,}\b", p["raw_text"]))
    # ground-truth personal names (from originals; kept in memory only)
    gt = set()
    for c in cells:
        vals = [re.sub(r"\s+", " ", v or "").strip() for v in c["cells_original"]]
        if any(re.fullmatch(r"[A-Z]{2,4}\d{2}", v) for v in vals) and vals and len(vals[0].split()) >= 2 and re.match(r"^[A-Z][a-z]", vals[0]):
            gt.add(_alnum(vals[0]))
    for p in page_text:
        for lm in LABEL.finditer(p["raw_text"]):
            window = re.sub(r"\s*\n\s*", " | ", p["raw_text"][lm.end():lm.end() + 90])
            for nm in NAME.finditer(window):
                words = [w.strip(".,'’:()").lower() for w in nm.group(0).split()]
                if _is_name(nm) and not all(w in lower_vocab or w in STOP for w in words if len(w) > 1):
                    gt.add(_alnum(nm.group(0)))   # proper nouns only: 'Note: SW - Staff-Weeks' after 'TTL' is not a name
                break
    staff_header_names = set()
    by_table = collections.defaultdict(list)
    for c in cells:
        by_table[c["table_id"]].append(c)
    for rows in by_table.values():
        hdr = next((r for r in rows[:3] if any((v or "").strip().lower() == "name" for v in r["cells_original"])), None)
        if hdr and any(re.search(r"role|title|unit|specializ|responsib|position", (v or ""), re.I) for v in hdr["cells_original"]):
            col = [(v or "").strip().lower() for v in hdr["cells_original"]].index("name")
            for r in rows[rows.index(hdr) + 1:]:
                v = re.sub(r"\s+", " ", (r["cells_original"][col] if col < len(r["cells_original"]) else "") or "").strip()
                if len(v.split()) >= 2 and re.match(r"^[A-Z]", v):
                    staff_header_names.add(_alnum(v))
    gt |= staff_header_names

    def classify(e: dict) -> str:
        if e["type"] != "person_name":
            return "contact_data"
        a = _alnum(e["original"])
        if a in gt or any(a and a in g for g in gt):
            return "ground_truth_person"
        words = [w.strip(".,'’:()").lower() for w in e["original"].split()]
        words = [w for w in words if w and not re.fullmatch(r"[a-z]", w)]
        if words and all(w in lower_vocab or w in STOP for w in words):
            return "likely_false_positive"
        return "likely_person"
    for e in events:
        e["classification"] = classify(e)
    persons = [e for e in events if e["type"] == "person_name"]
    summary = {"mask_events": len(events), "by_type": dict(collections.Counter(e["type"] for e in events)),
               "person_masks_by_rule_and_class": {r: dict(collections.Counter(e["classification"] for e in persons if e["rule"] == r))
                                                  for r in sorted({e["rule"] for e in persons})},
               "person_masks_by_class": dict(collections.Counter(e["classification"] for e in persons))}
    # stratified deterministic sample of 50
    strata = collections.defaultdict(list)
    for e in sorted(events, key=lambda e: hashlib.sha256(e["event_id"].encode()).hexdigest()):
        strata[(e["type"], e["rule"])].append(e)
    sample, i = [], 0
    while len(sample) < 50 and any(strata.values()):
        for k in sorted(strata):
            if strata[k] and len(sample) < 50:
                sample.append(strata[k].pop(0))
        i += 1
    with (out / "restricted" / "privacy_review_sample.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["event_id", "document_id", "source", "type", "rule", "original_masked_text", "automatic_classification", "reviewer_decision"])
        for e in sample:
            w.writerow([e["event_id"], e["document_id"], e["source"], e["type"], e["rule"], e["original"], e["classification"], ""])
    summary["review_sample"] = {"size": len(sample), "by_rule": dict(collections.Counter(e["rule"] for e in sample)),
                                "by_classification": dict(collections.Counter(e["classification"] for e in sample)),
                                "false_positive_strings": sorted({e["original"] for e in sample if e["classification"] == "likely_false_positive"})}
    summary["all_false_positive_strings"] = sorted({e["original"] for e in persons if e["classification"] == "likely_false_positive"})
    # possible misses in the sanitised outputs
    files = ["shared/paragraphs.jsonl", "shared/table_rows.jsonl", "shared/table_descriptions.jsonl", "shared/plan_activities.jsonl",
             "strategies/A/chunks.jsonl", "strategies/B/chunks.jsonl", "strategies/C/chunks.jsonl", "strategies/C/parents.jsonl"]
    misses = {}
    for f in files:
        recs = ld(f)
        texts = [r.get("text") or r.get("description") or r.get("row_text_for_embedding") or " | ".join(r.get("cells_clean") or []) for r in recs]
        flat = _alnum(" ".join(texts))
        role_runs = 0
        for t in texts:
            for line in t.split("\n"):
                if ROLE_LINE.search(line):
                    for m in NAME.finditer(line):
                        ws = [w.strip(".,'’:()").lower() for w in m.group(0).split()]
                        if len(ws) >= 2 and all(w not in lower_vocab and w not in STOP for w in ws if len(w) > 1):
                            role_runs += 1
        misses[f] = {"ground_truth_names_present": sum(1 for g in gt if len(g) >= 8 and g in flat),
                     "title_plus_capitalised_word": sum(len(TITLE.findall(t)) for t in texts),
                     "name_like_runs_on_staff_role_lines": role_runs}
    summary["possible_misses"] = misses
    summary["ground_truth_names"] = len(gt)
    return summary


if __name__ == "__main__":
    from . import config
    print(json.dumps(run(config.OUTPUT_V2), indent=1))
