"""RAG model loading is cache-only: no online checks, no downloads, a clear error when a model is missing.

Run (from procurement-intelligence/):  python -m pytest rag/retrieval/test_model_loading_offline.py -q -p no:cacheprovider
"""
from __future__ import annotations

import socket

import pytest
from sentence_transformers import CrossEncoder, SentenceTransformer

from rag.retrieval import core


@pytest.fixture
def connection_attempts(monkeypatch) -> list[str]:
    """Records and refuses every outgoing socket connection (covers requests, httpx and urllib)."""
    attempts: list[str] = []

    def refuse(self, address):
        attempts.append(str(address))
        raise ConnectionRefusedError("network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    return attempts


def test_embedding_model_loads_from_cache_without_network(connection_attempts):
    model = core.load_cached_model(SentenceTransformer, core.EMBED_MODEL, trust_remote_code=True)
    assert model.get_sentence_embedding_dimension() == 768 and connection_attempts == []


def test_reranker_loads_from_cache_without_network(connection_attempts):
    core.load_cached_model(CrossEncoder, core.RERANK_MODEL)
    assert connection_attempts == []


def test_retriever_model_method_uses_the_cache_only_loader(connection_attempts):
    retriever = core.Retriever.__new__(core.Retriever)   # no corpus needed to load the model
    retriever._model = None
    assert retriever.model().get_sentence_embedding_dimension() == 768 and connection_attempts == []


def test_missing_model_gives_a_controlled_error(connection_attempts):
    with pytest.raises(core.ModelNotCachedError, match="requires approval"):
        core.load_cached_model(SentenceTransformer, "Alibaba-NLP/model-that-is-not-cached")
    assert connection_attempts == []
