"""Lesson cards from the paragraphs of a lessons section, tagged by rules (no model call).

A topic is tagged when the headline names it or the text mentions it at least twice (one passing word is not
enough). A paragraph is a lesson unless it is a lead-in ("The following lessons are derived from the ICR:"), a running header
or footer, a footnote or reference, a fragment (no sentence end), or too short to say anything. Paragraphs split by a page break are joined first. The card id is stable
(document + normalised text), so the same lesson keeps its review decision when the document is ingested again.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

SOURCES = {"ICR": "Implementation Completion Report", "ICRR": "IEG review of the completion report",
           "PAD": "Project Appraisal Document (lessons reflected in the design)"}
MIN_WORDS, MAX_HEADLINE, MAX_TEXT = 15, 240, 1500
NUMBERING = re.compile(r"^\s*(?:\(?\d{1,3}[.)]|\(?[a-z][.)]|[•\-–▪●*]|[ivx]{1,4}[.)])\s+", re.I)
LEAD_IN = re.compile(r"^(?:the following|this review|based on|in addition|ieg (?:adds|draws)|(?:the )?icr (?:contains?|contained|"
                     r"draws|offers?|offered|lists?|identifies|provides)|(?:two|three|four|five|several|key|main|other) "
                     r"(?:key |main |important )?lessons)\b", re.I)
ABOUT_LESSONS = re.compile(r"\blessons?\b[^.]*\b(?:following|as follows|below|paraphrased|summari[sz]ed|identified|derived|"
                           r"delineated|listed)\b|\b(?:following|below)\b[^.]*\blessons?\b", re.I)   # "...summarized below"
RUNNING = re.compile(r"\(P\d{6}\)|^page \d+|^\d+$", re.I)
SENTENCE_END = re.compile(r"[.!?:;)\"”]$")
FOOTNOTE = re.compile(r"^\d{1,3}\s*[A-Z(“\"]|\bet al\.?|\bibid(?:em)?\b|\bop\. cit\.")

TOPICS: dict[str, re.Pattern] = {   # topic -> wording that signals it
    "procurement": re.compile(r"\bprocure\w*|\bbidd?(?:ing|ers?|s)?\b|\btender\w*|contract award|procurement plan", re.I),
    "qualification": re.compile(r"qualif\w*|eligib\w*|prequalif\w*|contractor capacity|capacity of (?:the )?contractors?", re.I),
    "specifications": re.compile(r"specification\w*|feasibility|detailed design|engineering design|design (?:studies|documents)|"
                                 r"terms of reference|\bToRs?\b", re.I),
    "packaging": re.compile(r"packag\w*|\blots?\b|slicing", re.I),
    "timeline": re.compile(r"\bdelay\w*|\blate\b|timeline|schedul\w*|time overrun|extension|slow\w*", re.I),
    "cost": re.compile(r"cost overrun|cost increase|price escalation|\bcosts?\b|budget", re.I),
    "contractor_performance": re.compile(r"contractors?\b|supervision|supervising|quality of (?:the )?works?|works quality", re.I),
    "capacity": re.compile(r"capacit\w*|training|staff\w*|implementing agenc\w*|\bPIUs?\b|\bPMUs?\b|institution\w*", re.I),
    "safeguards": re.compile(r"resettlement|safeguard\w*|environmental|social (?:risk|impact)|grievance", re.I),
    "monitoring_evaluation": re.compile(r"\bM&E\b|monitoring|results framework|indicators?|evaluation|baseline", re.I),
    "stakeholders": re.compile(r"communit\w*|beneficiar\w*|stakeholder\w*|participat\w*|ownership|commitment|champions?", re.I),
    "financing": re.compile(r"financing instrument|counterpart fund\w*|disburs\w*|Program[- ]for[- ]Results|PforR|"
                            r"results-based financing", re.I),
    "design": re.compile(r"project design|too (?:complex|ambitious)|\bcomplex\w*|ambitious|phased|\bscope\b", re.I),
}
STAGES = {  # lifecycle stage -> the topics that make a lesson useful there
    "planning": {"design", "financing", "capacity", "stakeholders", "packaging"},
    "tender_design": {"procurement", "qualification", "specifications", "packaging"},
    "execution": {"timeline", "cost", "contractor_performance", "safeguards", "capacity"},
    "learning": {"monitoring_evaluation"},
}
CATEGORIES = {
    "Works": re.compile(r"civil works|\bworks?\b|construction|rehabilitation of (?:roads?|infrastructure)", re.I),
    "Goods": re.compile(r"\bgoods\b|equipment|supply contracts?", re.I),
    "Consultant Services": re.compile(r"consultan\w*|technical assistance|supervision firms?", re.I),
}


@dataclass(frozen=True)
class Paragraph:
    text: str
    page_start: int | None = None
    page_end: int | None = None


@dataclass(frozen=True)
class Source:
    document_id: str
    doc_type: str                    # ICR | ICRR | PAD
    project_id: str | None
    country: str | None = None
    sector: str | None = None
    section: str | None = None       # the lessons section heading
    section_id: str | None = None    # its Knowledge Graph section (ingested documents)


@dataclass(frozen=True)
class LessonCard:
    card_id: str
    project_id: str | None
    country: str | None
    sector: str | None
    document_id: str
    doc_type: str
    section: str | None
    page_start: int | None
    page_end: int | None
    headline: str
    text: str
    topics: tuple[str, ...]
    stages: tuple[str, ...]
    categories: tuple[str, ...]
    status: str = "proposed"         # proposed | approved | rejected
    decided_by: str | None = None
    note: str | None = None
    origin: str = field(default="ingest", compare=False)
    section_id: str | None = field(default=None, compare=False)

    @property
    def source_label(self) -> str:
        return SOURCES.get(self.doc_type, self.doc_type)

    @property
    def citation(self) -> str:
        pages = (f", p. {self.page_start}" + (f"-{self.page_end}" if self.page_end and self.page_end != self.page_start else "")
                 if self.page_start else "")
        return f"{self.doc_type} {self.document_id}{pages} ({self.project_id or 'no project'})"


def cards(source: Source, paragraphs: list[Paragraph], origin: str = "ingest") -> list[LessonCard]:
    """The lessons of one section, in reading order."""
    out, seen = [], set()
    for p in _joined(paragraphs):
        text = " ".join(p.text.split())
        if FOOTNOTE.search(text):
            continue
        text = NUMBERING.sub("", text)
        if not _is_lesson(text) or card_id(source.document_id, text) in seen:
            continue
        seen.add(card_id(source.document_id, text))
        title = headline(text)
        topics = tuple(t for t, pattern in TOPICS.items() if pattern.search(title) or len(pattern.findall(text)) >= 2)
        out.append(LessonCard(
            card_id=card_id(source.document_id, text), project_id=source.project_id, country=source.country, sector=source.sector,
            document_id=source.document_id, doc_type=source.doc_type, section=source.section, page_start=p.page_start,
            page_end=p.page_end, headline=title, text=text[:MAX_TEXT], topics=topics,
            stages=tuple(s for s, wanted in STAGES.items() if wanted & set(topics)),
            categories=tuple(c for c, pattern in CATEGORIES.items() if pattern.search(text)), origin=origin,
            section_id=source.section_id))
    return out


def card_id(document_id: str, text: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", text.lower())[:400]
    return hashlib.sha256(f"{document_id}\x00{key}".encode()).hexdigest()[:20]


def headline(text: str) -> str:
    """The first sentence; a short title ("Cost overruns.") is joined to the sentence after it."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text, maxsplit=2)
    first = parts[0]
    if len(first.split()) <= 6 and len(parts) > 1:
        first = f"{first.rstrip('.')}: {parts[1]}"
    return first if len(first) <= MAX_HEADLINE else first[:MAX_HEADLINE].rsplit(" ", 1)[0] + "…"


def _is_lesson(text: str) -> bool:
    words = len(text.split())
    if words < MIN_WORDS or text.endswith(":") or LEAD_IN.search(text) or not SENTENCE_END.search(text):
        return False
    if words < 70 and ABOUT_LESSONS.search(text):                       # a lead-in that introduces the lessons
        return False
    return not (RUNNING.search(text) and words < 25)


def _joined(paragraphs: list[Paragraph]) -> list[Paragraph]:
    """A paragraph cut by a page break (no sentence end, next one starts in lower case) is joined to the next."""
    out: list[Paragraph] = []
    for p in paragraphs:
        text = p.text.strip()
        if not text:
            continue
        if out and not SENTENCE_END.search(out[-1].text) and text[:1].islower():
            last = out[-1]
            out[-1] = Paragraph(f"{last.text} {text}", last.page_start, p.page_end or last.page_end)
        else:
            out.append(Paragraph(text, p.page_start, p.page_end))
    return out
