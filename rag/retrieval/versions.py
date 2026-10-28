"""Selectable RAG versions: which processed corpus is indexed and where its embeddings come from (Step 6E).

    from rag.retrieval.versions import build_retriever
    build_retriever()                         # default: pilot_v2 + whole-index cache, exactly as Retriever(Corpus("C"))
    build_retriever("pilot_v2_1")             # opt-in: extraction 2.1.0 corpus + its registered per-unit release store
    build_retriever("pilot_v3")               # the approved 35-project corpus; bge-m3 vectors served from Lakebase pgvector
    build_retriever("pilot_v3_gte_local")     # evaluation only: same corpus, English model, local per-unit store
    build_retriever("pilot_v3_tablelens")     # pilot_v3, but the 10 TableLens PDFs' tables are answered by SQL
    build_table_lens("pilot_v3_tablelens")    # the TableLens side of that pipeline (None for every other version)

A non-default version is validated when the retriever is built (corpus version, indexed-text fingerprint, store
hashes, model configuration and compatibility attestation; see rag/embedding_cache/adapter.py) and any mismatch
raises; there is no fallback to another corpus or cache. Strategy B is not offered.

Each entry is a retrieval pipeline that the application selects by name per request (`rag_release`). A new pipeline,
such as TableLens (table cards -> exact cells), is added as one more entry without changing the callers.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    from . import core
except ImportError:              # imported as a top-level module (rag/retrieval on sys.path), like evaluate.py / demo.py
    import core  # type: ignore[no-redef]

DEFAULT_VERSION = "pilot_v2"


@dataclass(frozen=True)
class RagVersion:
    name: str
    processed_dir: Path | None       # None = the default corpus (core.PILOT)
    embedding_cache: str | None      # None = the Retriever's own default (legacy whole-index cache)
    strategies: tuple[str, ...]
    description: str
    resolve_project_names: bool = False   # a distinctive project-title word in the question scopes the search
    vector_store: str | None = None       # "lakebase" = dense vectors served from the wbg_rag pgvector store
    lakebase_release: str | None = None   # the stored vector release, when shared with another version
    table_lens: str | None = None         # TableLens release: its tables are answered by SQL, their row slices excluded


VERSIONS = {
    "pilot_v2": RagVersion("pilot_v2", None, None, ("A", "C"),
                           "extraction 2.0.0 corpus with the whole-index embedding cache (current default)"),
    "pilot_v2_1": RagVersion("pilot_v2_1", core.ROOT / "rag" / "corpus_v3" / "processed" / "pilot_v2_1", "unit", ("C",),
                             "extraction 2.1.0 corpus (stacked table values corrected) with its validated per-unit store"),
    "pilot_v3": RagVersion("pilot_v3", core.ROOT / "rag" / "corpus_v3" / "processed" / "pilot_v3", None, ("C",),
                           "the approved 35-project scope (237 documents), extraction 2.1.0, bge-m3 vectors in Lakebase",
                           resolve_project_names=True, vector_store="lakebase"),
    "pilot_v3_gte_local": RagVersion("pilot_v3_gte_local", core.ROOT / "rag" / "corpus_v3" / "processed" / "pilot_v3",
                                     "unit", ("C",), "evaluation only: pilot_v3 with the English model's local store",
                                     resolve_project_names=True),
    "pilot_v3_tablelens": RagVersion("pilot_v3_tablelens", core.ROOT / "rag" / "corpus_v3" / "processed" / "pilot_v3", None,
                                     ("C",), "pilot_v3 plus TableLens: tables of the 10 pilot PDFs answered by SQL over "
                                     "Lakebase (table description -> schema registry -> one validated SELECT)",
                                     resolve_project_names=True, vector_store="lakebase", lakebase_release="pilot_v3",
                                     table_lens="tablelens_pilot"),
}


class UnknownRagVersionError(ValueError):
    """The requested version or strategy is not offered."""


def get_version(name: str = DEFAULT_VERSION) -> RagVersion:
    if name not in VERSIONS:
        raise UnknownRagVersionError(f"unknown RAG version {name!r}; available: {sorted(VERSIONS)}")
    return VERSIONS[name]


def build_corpus(version: str = DEFAULT_VERSION, strategy: str = "C", include_tables: bool = True) -> "core.Corpus":
    spec = get_version(version)
    if strategy not in spec.strategies:
        raise UnknownRagVersionError(f"strategy {strategy!r} is not enabled for {version}; enabled: {spec.strategies}")
    if spec.processed_dir is not None and not include_tables:
        raise UnknownRagVersionError(f"{version} is released for its indexed scope with tables only")
    return core.Corpus(strategy, include_tables=include_tables, processed_dir=spec.processed_dir)


def build_retriever(version: str = DEFAULT_VERSION, strategy: str = "C", include_tables: bool = True) -> "core.Retriever":
    """A Retriever for the version. Non-default versions load and verify their embeddings now (fail fast)."""
    spec = get_version(version)
    corpus = build_corpus(version, strategy, include_tables)
    if spec.vector_store == "lakebase":
        return _lakebase_retriever(spec, corpus)
    retriever = core.Retriever(corpus, embedding_cache=spec.embedding_cache, resolve_project_names=spec.resolve_project_names)
    if spec.embedding_cache == "unit":
        retriever.embeddings()
    return retriever


def _lakebase_retriever(spec: RagVersion, corpus: "core.Corpus") -> "core.Retriever":
    """Verifies the stored release against this corpus and the local model before the first search (fail fast)."""
    from rag.embedding_cache.adapter import indexed_text_fingerprint
    from rag.vector_store import encoder
    from rag.vector_store.index import LakebaseVectorIndex
    from rag.vector_store.lakebase import open_pool
    model = encoder.load_model()
    index = LakebaseVectorIndex(open_pool(read_only=True), spec.lakebase_release or spec.name)
    index.verify(indexed_text_fingerprint(corpus.units), len(corpus.units), model.model_id, model.revision)
    lens = build_table_lens(spec.name)
    return core.Retriever(corpus, resolve_project_names=spec.resolve_project_names, dense_index=index,
                          query_encoder=model.encode_query, excluded_tables=lens.part_ids if lens else frozenset())


def build_table_lens(version: str):
    """The version's TableLens (registry loaded and release verified now), or None when it has none."""
    spec = get_version(version)
    if spec.table_lens is None:
        return None
    from rag.tablelens.query import TableLens
    from rag.vector_store import encoder
    from rag.vector_store.lakebase import open_pool
    return TableLens(open_pool(read_only=True), encoder.load_model().encode_query, spec.table_lens)
