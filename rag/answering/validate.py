"""Stage 6: deterministic answer validation (plus a local NLI entailment check per claim).

Nothing the generator says reaches the user unless it passes:
  ids        every cited evidence ID exists, is not quarantined and its page check is not 'unverified'
  numbers    every number / date in a claim occurs in the cited evidence (format-normalised; 'million' scaled)
  refs       every STEP / legacy reference and WB contract number in a claim occurs in the cited typed evidence
  quotes     quoted strings (>= 15 chars) occur in the cited evidence
  privacy    no e-mail / phone pattern, no mask token
  dump       a claim that is a pasted evidence block (header, typed-record text or > 350 chars) is not an answer
  support    claim entailed by a cited evidence window (roberta-large-mnli, local) OR >= 85% of its content words occur
             in the cited evidence (needed for table rows, which NLI reads poorly)
Unsupported claims are removed (kept in removed_claims). The final answer text is rebuilt from supported claims only.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
D_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
D_DMY = re.compile(r"\b(\d{1,2})[- ]([A-Za-z]{3})[a-z]*[- ,]+(\d{4})\b")
D_MDY = re.compile(r"\b([A-Za-z]{3})[a-z]*\.? (\d{1,2}),? (\d{4})\b")
STEP = re.compile(r"\b[A-Z]{2}-[A-Z0-9]{2,}-\d{5,7}-[A-Z]{2}-[A-Z]{2,}\b")
LEGACY = re.compile(r"\b[A-Z]{1,2}[/-]\d{1,3}[/-](?:ICB|NCB|NS|LCB|QCBS|CQS|RFB|RFQ)[A-Z0-9/.-]{3,40}?(?:EFY|EYY|\d{4})\b")
CONTRACT = re.compile(r"\b1\d{6}\b")
PID = re.compile(r"\bP\d{6}\b")
NUM = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.])\d+(?:\.\d+)?(?![\w])")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"\+\d[\d\s().-]{7,}\d|\(\d{2,4}\)\s*\d{3,4}[\s-]\d{3,4}")
MASK = re.compile(r"\[(PERSON|NAME|EMAIL|PHONE|CONTACT)[^\]]*\]", re.I)
QUOTE = re.compile(r"[\"“]([^\"”]{15,})[\"”]")
STOP = set("the a an of and or to in on for by with at from is was were be been are as that this its it which project projects".split())


def dates(s: str) -> set[str]:
    """Return every date in the text as YYYY-MM-DD, whether written '2020-03-05', '5 Mar 2020' or 'Mar 5, 2020'."""
    out = {f"{y}-{m}-{d}" for y, m, d in D_ISO.findall(s)}
    for d, mon, y in D_DMY.findall(s):
        if mon.lower()[:3] in MONTHS:
            out.add(f"{y}-{MONTHS[mon.lower()[:3]]:02d}-{int(d):02d}")
    for mon, d, y in D_MDY.findall(s):
        if mon.lower()[:3] in MONTHS:
            out.add(f"{y}-{MONTHS[mon.lower()[:3]]:02d}-{int(d):02d}")
    return out


def strip_ids(s: str) -> str:
    """Blank out dates and project or procurement references so their digits are not read as amounts."""
    for rx in (D_ISO, D_DMY, D_MDY, STEP, LEGACY, PID):
        s = rx.sub(" ", s)
    return s


def numbers(s: str, scale: bool = True) -> set[float]:
    """Return the numbers in the text (dates and references removed); with scale, '2.5 million' also adds 2500000."""
    s2 = strip_ids(s)
    out = set()
    for m in NUM.finditer(s2):
        v = float(m.group().replace(",", ""))
        if v < 10 and "." not in m.group():
            continue          # small integers (list markers, counts like 'two') are not checked numerically
        out.add(round(v, 2))
        tail = s2[m.end(): m.end() + 10].lower()
        if scale and "million" in tail:
            out.add(round(v * 1e6, 2))
        if scale and "billion" in tail:
            out.add(round(v * 1e9, 2))
    return out


def content_words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP and len(w) > 2}


class NLI:
    """Local entailment checker (roberta-large-mnli) that tests whether cited evidence supports a claim."""
    def __init__(self, model: str = "FacebookAI/roberta-large-mnli"):
        """Load the model from the local cache (on the Apple GPU when available) and find its entailment label."""
        # Default to offline mode so the model is read from the local Hugging Face cache, not downloaded.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        import torch
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model)
        self.dev = "mps" if torch.backends.mps.is_available() else "cpu"
        self.m = AutoModelForSequenceClassification.from_pretrained(model).eval().to(self.dev)
        self.ent = [i for i, l in self.m.config.id2label.items() if l.upper().startswith("ENTAIL")][0]

    def entail(self, premises: list[str], hypothesis: str) -> float:
        """Return the highest entailment probability of the hypothesis over all premises (0.0 when there are none)."""
        if not premises:
            return 0.0
        with self.torch.no_grad():
            x = self.tok(premises, [hypothesis] * len(premises), return_tensors="pt", truncation=True, max_length=320, padding=True).to(self.dev)
            p = self.m(**x).logits.softmax(-1)[:, self.ent]
        return float(p.max())


def windows(text: str, n: int = 2, cap: int = 14) -> list[str]:
    sents = [s.strip() for s in re.split(r"(?<=[.;!?])\s+|\n+| \|\| ", text) if len(s.strip()) > 3]
    return [" ".join(sents[i:i + n]) for i in range(0, max(1, len(sents)), 1)][:cap] or [text[:600]]


def check_claim(claim: dict, ev: dict, nli: NLI | None, mode: str = "text") -> dict:
    """mode 'text': generated prose (all checks). mode 'typed': a deterministic restatement of typed-record fields; support means
    every number, date and reference equals a field of the cited typed record (lexical / NLI checks do not apply to templates)."""
    ids = claim["evidence_ids"]
    checks: dict = {}
    valid = [i for i in ids if i in ev and not ev[i].quarantined and ev[i].page_check in ("verified", "verified_with_text_exception", "not_checkable")]
    checks["ids_valid"] = bool(ids) and len(valid) == len(ids)
    cited = " ".join(ev[i].text + " " + ev[i].context for i in valid)
    t = claim["text"]
    cn, en = numbers(t), numbers(cited)
    checks["unsupported_numbers"] = sorted(v for v in cn if v not in en)
    cd, ed = dates(t), dates(cited)
    checks["unsupported_dates"] = sorted(cd - ed)
    refs = set(STEP.findall(t)) | set(LEGACY.findall(t)) | set(CONTRACT.findall(strip_ids(t)))
    checks["unsupported_refs"] = sorted(r for r in refs if re.sub(r"[^A-Z0-9]", "", r.upper()) not in re.sub(r"[^A-Z0-9]", "", cited.upper()))
    checks["unsupported_quotes"] = [q for q in QUOTE.findall(t) if re.sub(r"\W", "", q.lower()) not in re.sub(r"\W", "", cited.lower())]
    checks["privacy_violation"] = bool(EMAIL.search(t) or PHONE.search(t) or MASK.search(t))
    cw = content_words(t)
    checks["content_word_recall"] = round(len(cw & content_words(cited)) / max(1, len(cw)), 3)
    checks["nli_entailment"] = round(nli.entail([w for i in valid for w in windows(ev[i].text)], t), 3) if (nli and valid) else None
    checks["too_short"] = len(cw) < 3
    checks["evidence_dump"] = len(t) > 350 or bool(re.match(r"\s*\[E\d+\]", t)) or "(typed record)" in t
    lexical_or_nli = checks["content_word_recall"] >= 0.85 or (checks["nli_entailment"] or 0) >= 0.5
    if mode == "typed":
        checks["mode"] = "typed_record_fields"
        lexical_or_nli = all(ev[i].record["unit_type"] == "activity" for i in valid)
        checks["evidence_dump"] = checks["too_short"] = False
    checks["supported"] = (checks["ids_valid"] and not checks["unsupported_numbers"] and not checks["unsupported_dates"] and not checks["unsupported_refs"]
                           and not checks["unsupported_quotes"] and not checks["privacy_violation"] and not checks["evidence_dump"] and not checks["too_short"] and lexical_or_nli)
    return checks


def typed_conflicts(items) -> list[dict]:
    """List every field where a plan row and its linked DS00005 contract record disagree, with both values."""
    out = []
    for e in items:
        for link in e.record.get("evidence_links", []) or []:
            if link.get("disagreeing_fields"):
                for f in link["disagreeing_fields"]:
                    plan_v = link.get("plan_actual_amount_usd") if "amount" in f else link.get("date_plan_actual_signing")
                    rec_v = link.get("contract_amount_usd") if "amount" in f else link.get("date_recorded_signing")
                    out.append({"field": f, "values": [{"value": plan_v, "source": f"procurement plan row ({e.eid}, {e.record['plan_version_id']})", "evidence_id": e.eid},
                                                      {"value": rec_v, "source": f"DS00005 contract record WB {link['wb_contract_number']} (as of {link.get('recorded_source_as_of_date')})", "evidence_id": e.eid}]})
    return out


def link_status_notes(supported_claims: list[dict], items) -> list[str]:
    """Warn when a supported claim names a contract whose link to the plan row is only 'candidate' or 'conflicting'."""
    notes = []
    text = " ".join(c["text"] for c in supported_claims)
    for e in items:
        for link in e.record.get("evidence_links", []) or []:
            if link.get("wb_contract_number") and link["wb_contract_number"] in text and link["evidence_status"] in ("candidate", "conflicting"):
                notes.append(f"Contract link to WB {link['wb_contract_number']} is '{link['evidence_status']}' ({link['match_class']} reference match); "
                             f"it is not a verified match.")
    return sorted(set(notes))
