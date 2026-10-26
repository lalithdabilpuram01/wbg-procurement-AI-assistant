"""Selectable RAG versions: which processed corpus is indexed and where its embeddings come from (Step 6E).

    from rag.retrieval.versions import build_retriever
    build_retriever()                         # default: pilot_v2 + whole-index cache, exactly as Retriever(Corpus("C"))
    build_retriever("pilot_v2_1")             # opt-in: extraction 2.1.0 corpus + its registered per-unit release store

A non-default version is validated when the retriever is built (corpus version, indexed-text fingerprint, store
hashes, model configuration and compatibility attestation; see rag/embedding_cache/adapter.py) and any mismatch
raises; there is no fallback to another corpus or cache. Strategy B is not offered.
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


VERSIONS = {
    "pilot_v2": RagVersion("pilot_v2", None, None, ("A", "C"),
                           "extraction 2.0.0 corpus with the whole-index embedding cache (current default)"),
    "pilot_v2_1": RagVersion("pilot_v2_1", core.ROOT / "rag" / "corpus_v3" / "processed" / "pilot_v2_1", "unit", ("C",),
                             "extraction 2.1.0 corpus (stacked table values corrected) with its validated per-unit store"),
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
    retriever = core.Retriever(build_corpus(version, strategy, include_tables), embedding_cache=spec.embedding_cache)
    if spec.embedding_cache == "unit":
        retriever.embeddings()
    return retriever
