"""Retrieval MVP over the SANITISED pilot_v2 outputs (10 PDFs). Never reads `restricted/`.

Retrievable units
  chunk     narrative chunk from strategy A (section-aware) or C (child; parent kept for context)
  table     window of 3 consecutive cleaned table rows (header/value rows are often split across rows)
  activity  one procurement-plan activity row (typed fields + stage dates), with its evidence links attached
Retrievers
  bm25      rank_bm25 BM25Okapi over a lower-cased alphanumeric tokenizer (reference strings kept whole)
  dense     Alibaba-NLP/gte-base-en-v1.5 (local HF cache, offline), cosine on normalised embeddings
  hybrid    reciprocal-rank fusion (k = 60) of bm25 and dense rankings
Guarded pipeline ("exact" layer, applied before ranking)
  scope     a project ID or country named in the question that is not in the corpus -> insufficient evidence
  privacy   requests for personal contact details -> withheld (sanitised corpus)
  exact     STEP / legacy plan references and WB contract numbers are looked up in typed records first;
            a reference that is not found -> "no record" (insufficient evidence)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "rag" / "corpus_v3" / "processed" / "pilot_v2"
CACHE = Path(__file__).resolve().parent / "index_cache"
sys.path.insert(0, str(ROOT / "discovery"))
from wbdisc.normalize import normalize_ref  # noqa: E402

EMBED_MODEL = "Alibaba-NLP/gte-base-en-v1.5"
RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class ModelNotCachedError(RuntimeError):
    """A required model is not in the local Hugging Face cache. Downloading it needs explicit approval."""


def force_hugging_face_offline() -> None:
    """Put huggingface_hub into offline mode for this process, whatever the import order.

    The HF_HUB_OFFLINE environment variable is read only when huggingface_hub is first imported, and
    local_files_only alone still lets transformers check processor config files online. Setting the library's
    offline flags at runtime closes both gaps (verified with HTTP and sockets blocked).
    """
    import huggingface_hub.constants as hf_constants
    import transformers.utils.hub as transformers_hub

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    hf_constants.HF_HUB_OFFLINE = True
    # transformers copies the offline flag once at import (private `_is_offline_mode`); without this it still asks
    # huggingface_hub to look up processor config files online. Set only if this transformers version has it.
    if hasattr(transformers_hub, "_is_offline_mode"):
        transformers_hub._is_offline_mode = True


def load_cached_model(loader, model_id: str, **kwargs):
    """Load a sentence-transformers model from the local cache only: never checks or downloads online."""
    force_hugging_face_offline()
    try:
        return loader(model_id, local_files_only=True, device="cpu", **kwargs)
    except OSError as error:
        raise ModelNotCachedError(f"Model '{model_id}' is not in the local Hugging Face cache; "
                                  "downloading it requires approval") from error
RRF_K = 60
STEP_REF = re.compile(r"\b[A-Z]{2}-[A-Z0-9]{2,}-\d{5,7}-[A-Z]{2}-[A-Z]{2,}\b")
LEGACY_REF = re.compile(r"\b[A-Z]{1,2}[/-]\d{1,3}[/-](?:ICB|NCB|NS|LCB|QCBS|CQS|RFB|RFQ)[A-Z0-9/ .-]{3,40}?(?:EFY|EYY|\d{4})\b")
CONTRACT_NO = re.compile(r"\b(?:contract(?: number| no\.?)?\s*)(\d{6,8})\b", re.I)   # any number written as a contract number is checked
PROJECT_ID = re.compile(r"\bP\d{6}\b")
# title words too common to identify one project ("Ethiopia Expressway" -> "expressway" identifies P148850)
GENERIC_TITLE_WORDS = {"project", "program", "programme", "development", "support", "national", "improvement", "additional",
                       "financing", "phase", "second", "third", "sector", "services", "service", "system", "systems",
                       "management", "investment", "operation", "public", "rural", "urban", "and", "for", "the", "of", "in"}
PRIVACY = re.compile(r"\b(phone|telephone|e-?mail|contact details|mobile number|home address|personal address)\b|\bwho (is|was) the (task team leader|ttl|procurement specialist|coordinator)\b", re.I)
COUNTRIES = {"ethiopia": "ET", "sri lanka": "LK", "senegal": "SN", "indonesia": "ID", "argentina": "AR", "kenya": "KE", "india": "IN",
             "nigeria": "NG", "bangladesh": "BD", "brazil": "BR", "pakistan": "PK", "mozambique": "MZ", "nepal": "NP", "viet nam": "VN", "vietnam": "VN"}
TOKEN = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*")


def tok(s: str) -> list[str]:
    return TOKEN.findall(s.lower())


def jl(name: str, base: Path | None = None) -> list[dict]:
    with open((base or PILOT) / name) as fh:
        return [json.loads(x) for x in fh]


@dataclass
class Unit:
    unit_id: str
    unit_type: str
    document_id: str
    project_id: str
    page_start: int
    page_end: int
    text: str                     # text indexed (with a short contextual header)
    excerpt: str                  # sanitised text shown to the user
    section: str | None = None
    table_id: str | None = None
    activity_id: str | None = None
    parent_id: str | None = None
    meta: dict = field(default_factory=dict)


class Corpus:
    def __init__(self, strategy: str = "A", include_tables: bool = True, processed_dir: Path | None = None):
        self.strategy = strategy
        self.include_tables = include_tables
        self._base = Path(processed_dir) if processed_dir else None    # None = the default corpus (PILOT)
        self.processed_dir = self._base or PILOT
        self.docs = {d["document_id"]: d for d in jl("shared/documents.jsonl", self._base)}
        self.versions = {d["document_id"]: d for d in jl("shared/document_versions.jsonl", self._base)}
        self.projects = {p["project_id"]: p for p in jl("shared/projects.jsonl", self._base)}
        self.pages = {(p["document_id"], p["page"]): p for p in jl("shared/pages.jsonl", self._base)}
        self.plan_versions = {p["plan_version_id"]: p for p in jl("shared/plan_versions.jsonl", self._base)}
        self.links: dict[str, list[dict]] = {}
        for e in jl("shared/evidence_links.jsonl", self._base):
            self.links.setdefault(e["activity_id"], []).append(e)
        self.units: list[Unit] = []
        self._chunks(strategy)
        if include_tables:
            self._tables()
        self._activities()
        self.by_id = {u.unit_id: u for u in self.units}
        self.corpus_projects = set(self.projects) | {d["project_id"] for d in self.docs.values()}
        self.corpus_countries = {d["country_code"] for d in self.docs.values()}

    def project_name_index(self, min_owner_share: float = 0.8) -> dict[str, str]:
        """Title words that identify one project -> that project.

        A word qualifies when it is in exactly one corpus project's title (len >= 5, not generic) AND at least
        `min_owner_share` of the indexed units mentioning it belong to that project, so ordinary words ("economic",
        "transport") that appear across the corpus never narrow a search.
        """
        owners: dict[str, set[str]] = {}
        for pid, project in self.projects.items():
            for word in set(re.findall(r"[a-z]{5,}", (project.get("api", {}).get("project_title") or "").lower())):
                if word not in GENERIC_TITLE_WORDS:
                    owners.setdefault(word, set()).add(pid)
        candidates = {word: next(iter(pids)) for word, pids in owners.items() if len(pids) == 1}
        mentions: dict[str, list[int]] = {word: [0, 0] for word in candidates}          # [owner units, all units]
        for unit in self.units:
            words = set(re.findall(r"[a-z]{5,}", unit.text.lower())) & candidates.keys()
            for word in words:
                mentions[word][1] += 1
                mentions[word][0] += unit.project_id == candidates[word]
        return {word: pid for word, pid in candidates.items()
                if mentions[word][1] and mentions[word][0] / mentions[word][1] >= min_owner_share}

    def header(self, doc_id: str) -> str:
        d = self.docs[doc_id]
        return f"[{d['doc_type']} {d['project_id']} {d['country_code']}: {d['api'].get('title', '')}]"

    def _chunks(self, strategy: str) -> None:
        self.parents = {}
        if strategy == "C":
            self.parents = {p["chunk_id"]: p for p in jl("strategies/C/parents.jsonl", self._base)}
        for c in jl(f"strategies/{strategy}/chunks.jsonl", self._base):
            self.units.append(Unit(c["chunk_id"], "chunk", c["document_id"], c["project_id"], c["physical_page_start"], c["physical_page_end"],
                                   f"{self.header(c['document_id'])} {c['section_path']}\n{c['text']}", c["text"], section=c["section_path"],
                                   parent_id=c.get("parent_id"), meta={"section_id": c["section_id"], "printed_page_label_start": c.get("printed_page_label_start"),
                                                                       "sanitisation": c.get("sanitisation", {})}))

    def _tables(self) -> None:
        tables = {t["table_id"]: t for t in jl("shared/tables.jsonl", self._base)}
        rows: dict[str, list[dict]] = {}
        for r in jl("shared/table_rows.jsonl", self._base):
            if any(x for x in r["cells_clean"]):
                rows.setdefault(r["table_id"], []).append(r)
        for tid, rs in rows.items():
            rs.sort(key=lambda r: r["row_index"])
            t = tables.get(tid, {})
            for i in range(0, len(rs), 2):
                win = rs[max(0, i - 1): i + 2]
                body = " || ".join(" | ".join(x for x in r["cells_clean"] if x) for r in win)
                pages = [r["page"] for r in win]
                self.units.append(Unit(f"{tid}:w{win[0]['row_index']}-{win[-1]['row_index']}", "table", win[0]["document_id"],
                                       self.docs[win[0]["document_id"]]["project_id"], min(pages), max(pages),
                                       f"{self.header(win[0]['document_id'])} table in {t.get('section_path', '')}\n{body}", body,
                                       section=t.get("section_path"), table_id=tid, meta={"rows": [r["row_index"] for r in win]}))

    def _activities(self) -> None:
        for a in jl("shared/plan_activities.jsonl", self._base):
            stages = "; ".join(f"{k}: " + ", ".join(f"{kk} {vv}" for kk, vv in v.items()) for k, v in (a.get("stage_dates") or {}).items())
            text = (f"{a['row_text_for_embedding']} Market approach {a.get('market_approach_clean')}. Component {a.get('component_clean')}. "
                    f"Process status {a.get('process_status_clean')}. Stage dates: {stages}")
            self.units.append(Unit(f"act:{a['activity_id']}", "activity", a["document_id"], a["project_id"], a["pdf_page"], a["pdf_page"],
                                   f"{self.header(a['document_id'])} procurement plan activity\n{text}", text, section=a.get("section"),
                                   table_id=a.get("table_id"), activity_id=a["activity_id"],
                                   meta={"activity": {k: a.get(k) for k in ("activity_reference_printed", "activity_reference_normalized", "package_reference_core",
                                                                           "description_clean", "method_clean", "market_approach_clean", "review_type",
                                                                           "process_status_clean", "estimated_amount_usd", "actual_amount_usd",
                                                                           "signed_contract_planned", "signed_contract_actual", "stage_dates",
                                                                           "plan_stream_id", "plan_version_id", "uncertain_fields", "value_flags")}}))

    # ---------- citation record ----------
    def cite(self, u: Unit, rank: int, scores: dict) -> dict:
        d, v = self.docs[u.document_id], self.versions[u.document_id]
        pg = self.pages.get((u.document_id, u.page_start), {})
        proj = self.projects.get(u.project_id, {}).get("api", {})
        rec = {
            "rank": rank, "unit_id": u.unit_id, "unit_type": u.unit_type, "scores": scores,
            "project_id": u.project_id, "project_name": proj.get("project_title"), "country_code": d["country_code"],
            "document_id": u.document_id, "document_version_id": v["document_version_id"], "document_sha256": v["sha256"],
            "doc_type": d["doc_type"], "document_role": d["role"], "title": d["api"].get("title"),
            "document_date": d["api"].get("wds_document_date"), "disclosure_date": d["api"].get("wds_disclosure_date"),
            "physical_page_start": u.page_start, "physical_page_end": u.page_end,
            "printed_page_label": pg.get("printed_page_label"),
            "printed_page_label_status": "detected" if pg.get("printed_page_label") else "not detected (cite the physical page)",
            "section": u.section, "table_id": u.table_id, "excerpt": u.excerpt[:700],
            "source_url": d["api"].get("final_url"), "local_path": v["local_path"],
            "association": "document text (project-level context; not a contract-level match)",
        }
        if u.unit_type == "activity":
            a = u.meta["activity"]
            rec["activity"] = a
            rec["plan_stream_id"], rec["plan_version_id"] = a["plan_stream_id"], a["plan_version_id"]
            rec["association"] = "procurement-plan row (typed record)"
            rec["evidence_links"] = [{k: e.get(k) for k in ("evidence_status", "match_class", "wb_contract_number", "borrower_contract_reference",
                                                           "date_recorded_signing", "date_plan_actual_signing", "date_planned_signing",
                                                           "plan_actual_amount_usd", "contract_amount_usd", "disagreeing_fields", "cardinality",
                                                           "recorded_source_as_of_date")}
                                     for e in self.links.get(u.activity_id, []) if e.get("evidence_status") != "unmatched"]
            rec["evidence_link_note"] = ("Contract links are typed matches between this plan row and DS00005 contract records. "
                                         "'candidate' and 'conflicting' links are not verified; conflicting fields are shown, not resolved.")
        if u.parent_id:
            rec["parent_id"] = u.parent_id
            p = self.parents.get(u.parent_id)
            if p:
                rec["parent_pages"] = [p.get("physical_page_start"), p.get("physical_page_end")]
        return rec


EMBEDDING_CACHE_ENV = "RAG_EMBEDDING_CACHE"
EMBEDDING_CACHE_MODES = ("legacy", "unit")   # legacy = whole-index .npy (default); unit = opt-in verified per-unit cache


class Retriever:
    def __init__(self, corpus: Corpus, embedding_cache: str | None = None, resolve_project_names: bool = False,
                 dense_index=None, query_encoder=None, excluded_tables: frozenset[str] = frozenset()):
        """dense_index (opt-in): an external vector index with `nearest(query, scope_projects)`, e.g. the Lakebase
        store; it replaces the in-memory embedding matrix and needs `query_encoder` (question -> unit vector).
        excluded_tables (opt-in): table ids whose row slices are never returned (TableLens answers them with SQL)."""
        from rank_bm25 import BM25Okapi
        if (dense_index is None) != (query_encoder is None):
            raise ValueError("dense_index and query_encoder are given together")
        self.c = corpus
        self.dense_index, self._encode_query = dense_index, query_encoder
        self._position = {u.unit_id: i for i, u in enumerate(corpus.units)} if dense_index is not None else {}
        # opt-in (pilot_v3): a distinctive project-title word in the question narrows the search to that project
        self._project_names = corpus.project_name_index() if resolve_project_names else {}
        self.keep = np.array([not (u.unit_type == "table" and u.table_id in excluded_tables) for u in corpus.units], dtype=bool)
        self.bm25 = BM25Okapi([tok(u.text) for u in corpus.units])
        self._emb = None
        self._model = None
        self._rerank = None
        self.embedding_cache = embedding_cache or os.environ.get(EMBEDDING_CACHE_ENV) or "legacy"
        if self.embedding_cache not in EMBEDDING_CACHE_MODES:
            raise ValueError(f"embedding_cache must be one of {EMBEDDING_CACHE_MODES}, not {self.embedding_cache!r}")

    # ---------- dense ----------
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = load_cached_model(SentenceTransformer, EMBED_MODEL, trust_remote_code=True)
        return self._model

    def embeddings(self) -> np.ndarray:
        if self._emb is None and self.embedding_cache == "unit":
            # opt-in: every vector must come from the verified per-unit store; a miss raises, never re-encodes or falls back
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            from rag.embedding_cache.adapter import unit_cache_embeddings
            self._emb = unit_cache_embeddings(self.c)
        if self._emb is None and Path(getattr(self.c, "processed_dir", PILOT)).resolve() != PILOT.resolve():
            raise ValueError("the whole-index cache serves only the default corpus; use a registered RAG version "
                             "(rag.retrieval.versions) with embedding_cache='unit' for another processed_dir")
        if self._emb is None:
            CACHE.mkdir(exist_ok=True)
            import hashlib
            key = hashlib.sha256(("\n".join(u.unit_id + u.text for u in self.c.units) + EMBED_MODEL).encode()).hexdigest()[:16]
            f = CACHE / f"emb_{self.c.strategy}_{len(self.c.units)}_{key}.npy"
            if f.exists():
                self._emb = np.load(f)
            else:
                self._emb = self.model().encode([u.text for u in self.c.units], batch_size=16, normalize_embeddings=True, show_progress_bar=False)
                np.save(f, self._emb)
        return self._emb

    def rank_bm25(self, q: str, mask: np.ndarray) -> np.ndarray:
        s = self.bm25.get_scores(tok(q))
        s[~mask] = -np.inf
        return s

    def rank_dense(self, q: str, mask: np.ndarray, scope: set[str] | None = None) -> np.ndarray:
        if self.dense_index is not None:
            return self._rank_dense_index(q, mask, scope)
        qv = self.model().encode([q], normalize_embeddings=True)[0]
        s = self.embeddings() @ qv
        s[~mask] = -np.inf
        return s

    def _rank_dense_index(self, q: str, mask: np.ndarray, scope: set[str] | None) -> np.ndarray:
        """Scores for the external index's nearest neighbours (within the scope); every other unit ranks last."""
        s = np.full(len(self.c.units), -np.inf)
        for unit_id, score in self.dense_index.nearest(self._encode_query(q), scope):
            s[self._position[unit_id]] = score
        s[~mask] = -np.inf
        return s

    def reranker(self):
        if self._rerank is None:
            from sentence_transformers import CrossEncoder
            self._rerank = load_cached_model(CrossEncoder, RERANK_MODEL)
        return self._rerank

    # ---------- guarded pipeline ----------
    def guard(self, q: str) -> dict:
        out: dict = {"status": "ok", "scope_projects": None, "exact_units": [], "notes": []}
        if PRIVACY.search(q):
            return {**out, "status": "withheld_personal_data", "notes": ["Personal contact data is masked in the sanitised corpus and is not returned."]}
        pids = set(PROJECT_ID.findall(q))
        named = {code for name, code in COUNTRIES.items() if re.search(rf"\b{name}\b", q, re.I)}
        if pids - self.c.corpus_projects or (named and not named & self.c.corpus_countries):
            return {**out, "status": "insufficient_evidence", "notes": [f"Out of the pilot corpus scope (projects {sorted(pids - self.c.corpus_projects) or '-'}, countries {sorted(named - self.c.corpus_countries) or '-'})."]}
        scope = set(pids) or {p for p in self.c.corpus_projects if self.c.docs and any(d['project_id'] == p and d['country_code'] in named for d in self.c.docs.values())}
        if not pids and self._project_names:
            by_name = {pid for word, pid in self._project_names.items() if re.search(rf"\b{word}\b", q, re.I)}
            if by_name:
                scope = (scope & by_name) or by_name
                out["notes"].append(f"Project named in the question: {sorted(by_name)}.")
        out["scope_projects"] = scope or None
        refs = [normalize_ref(m) for m in STEP_REF.findall(q) + LEGACY_REF.findall(q)]
        nums = CONTRACT_NO.findall(q)
        if refs or nums:
            hits = []
            for u in self.c.units:
                if u.unit_type != "activity":
                    continue
                a = u.meta["activity"]
                keys = {a.get("activity_reference_normalized"), a.get("package_reference_core")}
                linked = {e.get("wb_contract_number") for e in self.c.links.get(u.activity_id, [])} | {normalize_ref(e.get("borrower_contract_reference") or "") for e in self.c.links.get(u.activity_id, [])}
                if any(r in keys or r in linked for r in refs) or any(n in linked for n in nums):
                    hits.append(u.unit_id)
            out["exact_units"] = hits
            if not hits:
                out["status"] = "insufficient_evidence"
                out["notes"].append(f"No procurement-plan or contract record found for {refs + nums}.")
        return out

    def search(self, q: str, method: str = "hybrid", k: int = 10, guarded: bool = True, rerank: bool = False) -> dict:
        t0 = time.perf_counter()
        g = self.guard(q) if guarded else {"status": "ok", "scope_projects": None, "exact_units": [], "notes": []}
        if g["status"] != "ok":
            return {"query": q, "status": g["status"], "notes": g["notes"], "results": [], "latency_ms": round(1000 * (time.perf_counter() - t0), 1)}
        mask = self.keep.copy()
        if g["scope_projects"]:
            mask &= np.array([u.project_id in g["scope_projects"] for u in self.c.units])
        if method == "bm25":
            s = self.rank_bm25(q, mask)
            order, scores = np.argsort(-s), {"bm25": s}
        elif method == "dense":
            s = self.rank_dense(q, mask, g["scope_projects"])
            order, scores = np.argsort(-s), {"dense": s}
        else:
            b, dn = self.rank_bm25(q, mask), self.rank_dense(q, mask, g["scope_projects"])
            rb, rd = np.empty(len(b)), np.empty(len(dn))
            rb[np.argsort(-b)] = np.arange(1, len(b) + 1)
            rd[np.argsort(-dn)] = np.arange(1, len(dn) + 1)
            fused = 1 / (RRF_K + rb) + 1 / (RRF_K + rd)
            fused[~mask] = -np.inf
            order, scores = np.argsort(-fused), {"rrf": fused, "bm25": b, "dense": dn}
        cand = [i for i in order if np.isfinite(list(scores.values())[0][i])][: (30 if rerank else k)]
        if rerank and cand:
            rs = self.reranker().predict([(q, self.c.units[i].excerpt[:1500]) for i in cand])
            cand = [cand[j] for j in np.argsort(-rs)]
        exact = [self.c.units.index(self.c.by_id[x]) for x in g["exact_units"]]
        final = exact + [i for i in cand if i not in exact]
        res = []
        for r, i in enumerate(final[:k], start=1):
            sc = {name: (round(float(v[i]), 4) if np.isfinite(v[i]) else None) for name, v in scores.items()}
            if i in exact:
                sc["exact_reference_match"] = True
            res.append(self.c.cite(self.c.units[i], r, sc))
        return {"query": q, "status": "ok", "notes": g["notes"], "results": res, "latency_ms": round(1000 * (time.perf_counter() - t0), 1)}
