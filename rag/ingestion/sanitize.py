"""Text sanitisation before any embedding or LLM use: e-mail addresses, phone numbers and personal names next to staff
role labels. Role titles are kept. Counts of each replacement are returned; personal values are never stored.

Names are masked only where the document marks them as people:
  * within 90 characters after a staff-role label ('Task Team Leader:', 'Prepared by', 'Country Director' ...);
  * in a table row (cells joined by ' | ') that contains a role label;
  * in every row of a staff table, i.e. a table whose header row has a 'Name' column next to a role / title / unit column.
A name is a run of 2-4 capitalised words (initials, particles and hyphenated parts allowed) that is not made only of
role, institution or place words. Phone numbers need a '+' international prefix or a Tel / Phone / Fax / Mobile label,
so report numbers, amounts and document ids are never masked.
"""
from __future__ import annotations

import re

CAPTURE: list | None = None   # when a list, every mask is appended as {"type", "rule", "original"} (restricted use only)


def _cap(kind: str, rule: str, original: str) -> None:
    if CAPTURE is not None:
        CAPTURE.append({"type": kind, "rule": rule, "original": original})

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_INTL = re.compile(r"\+\d{1,3}[\s.-]?(?:\(?\d{1,4}\)?[\s.-]?){2,4}\d{2,4}\b")
PHONE_LABELLED = re.compile(r"(?P<label>\b(?:Telephone|Téléphone|Tel|Tél|Phone|Fax|Mobile|Cell)\.?\s*(?:No\.?)?[\s:.|]*)"
                            r"(?P<num>\(?\+?\d[\d\s()./-]{6,}\d)", re.I)
PHONE = re.compile(f"{PHONE_INTL.pattern}|{PHONE_LABELLED.pattern.replace('(?P<label>', '(?:').replace('(?P<num>', '(?:')}", re.I)
LABEL = re.compile(
    r"(?:Co-?Task Team Leaders?|Task Team Leaders?(?:\(s\))?|\bTTLs?\b|Team Leaders?(?:\(s\))?|Prepared by|Reviewed by|ICR Review Coordinator|"
    r"Practice Managers?|(?:Global )?Practice Directors?|Country Directors?|Country Managers?|Regional Vice Presidents?|Regional Directors?|Senior Global Practice Directors?|"
    r"Global Directors?|Practice Leaders?|Program Leaders?|Peer Reviewers?|ICR Main Contributors?|Contributing Authors?|Task Managers?|"
    r"Contact [Pp]erson|Team Composition|Bank Staff|Non Bank Staff|Senior Director|Vice President)\b", re.I)
PERSONNEL = LABEL  # used by callers to flag sections that carry personal-data indicators
STAFF_HEADER = re.compile(r"\bName\b", re.I)
UNIT_CODE_CELL = re.compile(r"(?:^|\|)\s*[A-Z]{2,4}\d{2}\s*(?=\||$)")   # World Bank unit codes: GTI06, GGO24, GEN06
ROLE_CELL = re.compile(r"(?:^|\|)\s*(?:Co-?)?(?:Task )?(?:Team Leader|Team Member|Team Lead|Member|Peer Reviewer|Counsel|TTL)s?\s*(?=\||$)", re.I)
STAFF_TABLE_LABEL = re.compile(r"\b(Bank Staff|Non Bank Staff|Team Composition|Task Team Members)\b", re.I)
STAFF_HEADER_ROLE = re.compile(r"\b(Role|Title|Unit|Responsibilit|Specializ|Specialis|Position)", re.I)
NAME = re.compile(r"\b(?:(?:Mr|Ms|Mrs|Dr|Prof)\.?\s+[A-Z][a-zA-Z'’\-]+(?:\s+[A-Z][a-zA-Z'’\-]+){0,2}|"
                  r"(?:(?:Mr|Ms|Mrs|Dr|Prof)\.?\s+)?(?:(?:[A-Z]\.\s*){1,3}[A-Z][a-zA-Z'’\-]+(?:\s+[A-Z][a-zA-Z'’\-]+)?|"
                  r"[A-Z][a-zA-Z'’\-]+(?:\s+(?:[A-Z]\.?(?=\s)|de|da|del|van|von|bin|al|[A-Z][a-zA-Z'’\-]+)){1,3}))\b")
NAME_FRAGMENT = re.compile(r"\[PERSON\]([ \t]*\n?[ \t]*)([A-Z][a-zA-Z'’\-]+)([ \t]*(?:[,/|\n]|$))")
TITLE_NAME = re.compile(r"\b(?:Mr|Ms|Mrs|Mme|Dr)\.?\s+[A-Z][a-zA-Z'’\-]+(?:\s+[A-Z][a-zA-Z'’\-]+){0,2}")
STAFF_TEXT_HEADER = re.compile(r"^\s*Name\s+(Role|Title|Unit|Responsibility|Position)\b", re.I)
STOP = {w.lower() for w in """World Bank Team Leader Leaders Task Specialist Senior Lead Principal Procurement Financial Management Country
Director Practice Manager Regional Vice President Global Transport Social Environmental Environment Unit Title Role Name Staff Non
Consultant Counsel Operations Officer Program Programme Assistant Economist Engineer Legal Sector Ministry Government Responsibility
Specialization Specialisation Prepared Reviewed Coordinator Group Review ICR IEG Contributor Main Peer Reviewer Analyst Administrative
Development Infrastructure Urban Roads Road Highway Agriculture Health Education Water Energy Safeguards Safeguard Gender Disaster Risk
Resilience Finance Monitoring Evaluation Supervision Lending Contact Agency Implementing Borrower Responsible Email Telephone Phone
Fax Mobile Address Project Projects Authority Department Office Secretary General Division Africa Asia South East Eastern Western Southern
Central Region Republic Democratic Socialist Federal Sri Lanka Ethiopia Senegal Indonesia Argentina Washington Practice Quality Assurance
Communications Communication Resettlement Land Acquisition Fiduciary Integrity Corporate Climate Change Private Public Partnerships
Information Technology Data Results Strategy Policy Governance Institutional Capacity Building Component Components Annex Table Total
Amount Date Dates Original Revised Actual Approval Closing Board Credit IDA IBRD Trust Fund Grant Loan Financing Cost Costs US Million
Expressway Corridor National Specialization(s) Name(s) Co Mission Missions Staff Weeks Composition Senior Junior Director(s) Manager(s)
Operation Operations Unit(s) Network Leadership Learning Sustainable Poverty Equity Macroeconomics Trade Investment Competitiveness
Jobs Fragility Conflict Violence Infrastructure Digital Urban Rural Land GP Global Practice Africa AFR SAR SAS ECA LCR MNA EAP
Partial Full Assessment Instrument Fragile Category Environmental Investment Adaptable Specific Sectoral Member Members Team
Leader Leaders Lead Engineer Engineers Officer Officers Adviser Advisor Expert Experts""".split()}


def _is_name(m: re.Match) -> bool:
    words = [w.strip(".'’,") for w in m.group(0).split()]
    initials = bool(re.match(r"^(?:[A-Z]\.|(?:Mr|Ms|Mrs|Dr|Prof)\.?\s)", m.group(0)))   # initials or a courtesy title
    core = [w for w in words if not re.fullmatch(r"(Mr|Ms|Mrs|Dr|Prof)|[A-Z]|de|da|del|van|von|bin|al", w)]
    if not core or all(w.isupper() and len(w) > 1 for w in core):
        return False  # all-caps runs are headings / acronyms in these documents, not names
    need = 1 if initials else 2
    return len(core) >= need and sum(w.lower() not in STOP for w in core) >= need


def _mask_names(s: str, start: int, end: int, counts: dict, rule: str = "person_name") -> str:
    out, last = [], 0
    for m in NAME.finditer(s):
        if m.start() >= start and m.start() < end and _is_name(m):
            toks = list(re.finditer(r"\S+", m.group(0)))
            while len(toks) > 2 and toks[-1].group(0).strip(".'’:").lower() in STOP:
                toks.pop()  # a following role word ('... Practice Director') is not part of the name
            stop = m.start() + toks[-1].end()
            out.append(s[last:m.start()])
            out.append("[PERSON]")
            _cap("person_name", rule, s[m.start():stop])
            counts["person_name"] += 1
            if rule != "person_name":
                counts[rule] = counts.get(rule, 0) + 1
            last = stop
    out.append(s[last:])
    return "".join(out)


SEP = re.compile(r"(?:\(s\)|[^\w\n]|\band\b)*")   # punctuation, cell bars, '(s)', 'and' between a label and its names


def _mask_name_list(s: str, pos: int, counts: dict) -> tuple[str, bool]:
    """Mask the consecutive names that follow a role label at pos ('Country Director: A B', 'Contributors: A B, C D and E F').
    Stops at the first token run that is not a name. Returns (text, reached_end_of_line_without_a_name)."""
    found = False
    for _ in range(12):
        m0 = SEP.match(s, pos)
        pos = m0.end()
        if pos >= len(s):
            return s, not found
        m = NAME.match(s, pos)
        if not m or not _is_name(m):
            one = re.match(r"[A-Z][a-zA-Z'’\-]+(?=\s*(?:,|;|\band\b|$))", s[pos:])   # a single-word name inside the list
            if found and one and one.group(0).lower() not in STOP:
                _cap("person_name", "rule_label_name_list", one.group(0))
                s = s[:pos] + "[PERSON]" + s[pos + one.end():]
                counts["person_name"] += 1
                counts["rule_label_name_list"] = counts.get("rule_label_name_list", 0) + 1
                pos += len("[PERSON]")
                continue
            return s, False
        toks = list(re.finditer(r"\S+", m.group(0)))
        while len(toks) > 2 and toks[-1].group(0).strip(".'’:").lower() in STOP:
            toks.pop()
        end = m.start() + toks[-1].end()
        _cap("person_name", "rule_label_name_list", s[m.start():end])
        s = s[:m.start()] + "[PERSON]" + s[end:]
        counts["person_name"] += 1
        counts["rule_label_name_list"] = counts.get("rule_label_name_list", 0) + 1
        pos = m.start() + len("[PERSON]")
        found = True
    return s, False


def sanitize(text: str, personnel_context: bool | None = None, staff_rows: bool = True) -> tuple[str, dict]:
    """Return (sanitised text, counts). personnel_context=True masks names on every line (used only for sections whose
    heading is itself a staff heading); False never masks names; None applies the label / staff-table rules."""
    counts = {"email": 0, "phone": 0, "person_name": 0}

    def sub(rx, token, key, s):
        def rep(m):
            counts[key] += 1
            _cap(key, key, m.group(0))
            return token
        return rx.sub(rep, s)

    text = sub(EMAIL, "[EMAIL]", "email", text)

    def phone_rep(m):                      # keep the label ('Telephone |'), mask the number
        counts["phone"] += 1
        _cap("phone", "phone_labelled", m.group("num"))
        return m.group("label") + "[PHONE]"
    text = PHONE_LABELLED.sub(phone_rep, text)
    text = sub(PHONE_INTL, "[PHONE]", "phone", text)
    def title_rep(m):                      # a courtesy title followed by a capitalised name is a person anywhere
        counts["person_name"] += 1
        counts["rule_title"] = counts.get("rule_title", 0) + 1
        _cap("person_name", "rule_title", m.group(0))
        return "[PERSON]"
    if personnel_context is not False:
        text = TITLE_NAME.sub(title_rep, text)
    if personnel_context is False:
        return text, counts
    lines = text.split("\n")
    staff_table = staff_text = False
    staff_text_left = 0
    rows_after_label = 0
    carry = 0                             # characters of a label window that run into the following lines
    out = []
    for ln in lines:
        is_row = " | " in ln
        if staff_rows and is_row and STAFF_TABLE_LABEL.search(ln):
            staff_table = True           # a staff table continues across split chunks under its 'Bank Staff' label row
        if staff_rows and is_row and STAFF_HEADER.search(ln) and STAFF_HEADER_ROLE.search(ln):
            staff_table = True           # header of a staff table: mask names in the rows that follow
            out.append(ln)
            continue
        if staff_rows and STAFF_TEXT_HEADER.match(ln):  # staff list laid out as text: 'Name Role' then one person per line
            staff_text, staff_text_left = True, 60
            out.append(ln)
            continue
        if not is_row and ln.strip() and ln.strip().isupper() and len(ln.strip()) > 3:
            staff_text = False           # next all-caps heading ends a text staff list
        if is_row and not re.search(r"\w", ln):
            out.append(ln)                    # empty spacer row: keeps the label-row state for the value row below
            continue
        label_row = is_row and LABEL.search(ln) is not None
        if staff_rows and is_row and (UNIT_CODE_CELL.search(ln) or ROLE_CELL.search(ln)):
            staff_table = True           # a staff row (unit-code or role cell) even when the table's header is on another page
        if personnel_context or (staff_table and ln.strip()) or (is_row and (label_row or rows_after_label > 0)) or (staff_text and staff_text_left > 0):
            rule = ("rule_staff_table" if staff_table else "rule_staff_text_list" if staff_text else
                    "rule_personnel_section" if personnel_context else "rule_label_row")
            out.append(_mask_names(ln, 0, len(ln), counts, rule))   # whole line (data-sheet value rows follow their label row)
            staff_text_left -= 1 if staff_text else 0
            rows_after_label = 2 if label_row else rows_after_label - 1
            carry = 0
            continue
        rows_after_label = 0
        s = ln
        if carry and s.strip():               # a label ended the previous line: its names start this line
            s, _ = _mask_name_list(s, 0, counts)
            carry = 0
        elif carry:
            carry -= 1                        # blank line between a label and its names
        for lm in reversed(list(LABEL.finditer(s))):   # right to left keeps earlier offsets valid
            s, pending = _mask_name_list(s, lm.end(), counts)
            if pending:
                carry = 2
        out.append(s)
    text = "\n".join(out)

    def frag(m):  # surname wrapped onto the next line or left after a masked name: '[PERSON] Xxxx,' / '[PERSON]\nXxxx'
        if m.group(2).lower() in STOP:
            return m.group(0)
        counts["name_fragment"] = counts.get("name_fragment", 0) + 1
        _cap("person_name", "name_fragment", m.group(2))
        return "[PERSON]" + m.group(1) + "[PERSON]" + m.group(3)   # keeps line structure: '[PERSON]\nSurname' -> '[PERSON]\n[PERSON]'
    text = NAME_FRAGMENT.sub(frag, text)
    return text, counts


def residual_risk(text: str) -> dict:
    """Detectors re-run on sanitised text: e-mails and phone patterns (independent name checks are done in validation
    against the 'Name' columns of staff tables)."""
    return {"email": len(EMAIL.findall(text)), "phone": len(PHONE.findall(text))}


TOKEN = re.compile(r"\[(?:PERSON|EMAIL|PHONE)\]")


def mask_intervals(raw: str, clean: str) -> list[tuple[int, int, str]] | None:
    """Character ranges of raw that sanitize() replaced to obtain clean, as (start, end, token). Returns None when clean
    is not raw with token substitutions (the caller then falls back to sanitising the substring itself)."""
    parts, toks = TOKEN.split(clean), TOKEN.findall(clean)
    if not raw.startswith(parts[0]):
        return None
    out, pos = [], len(parts[0])
    for i, (tok, lit) in enumerate(zip(toks, parts[1:])):
        last = i == len(toks) - 1
        if last:                      # the final literal must be the end of raw
            nxt = len(raw) - len(lit) if raw.endswith(lit) and len(raw) - len(lit) >= pos else -1
        else:                         # names / numbers never contain the literal that follows them
            nxt = raw.find(lit, pos) if lit else pos
        if nxt < 0:
            return None
        out.append((pos, nxt, tok))
        pos = nxt + len(lit)
    if pos != len(raw):
        return None
    return [(a, b, t) for a, b, t in out if b > a]


def apply_masks(raw: str, a: int, b: int, masks: list[tuple[int, int, str]]) -> str:
    """raw[a:b] with every overlapping masked range replaced by its token."""
    out, pos = [], a
    for s, e, tok in sorted(masks):
        if e <= a or s >= b:
            continue
        s2, e2 = max(s, a), min(e, b)
        out.append(raw[pos:s2])
        out.append(tok)
        pos = e2
    out.append(raw[pos:b])
    return "".join(out)
