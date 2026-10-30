"""Offline checks of the Lakebase PoC environment: Python version, imports, vector maths and pgvector encoding.

No database connection, no network. The 768-dimension case matches the retrieval embeddings (gte-base-en-v1.5).
"""
from __future__ import annotations

import sys

import numpy as np
import psycopg
import pytest
from pgvector import HalfVector, Vector
from pgvector.psycopg import register_vector

EMBEDDING_DIMENSIONS = 768


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def test_python_is_3_12():
    assert sys.version_info[:2] == (3, 12)


def test_psycopg_binary_and_pgvector_adapter_are_available():
    assert psycopg.pq.__impl__ == "binary"            # bundled libpq, no system PostgreSQL client needed
    assert psycopg.pq.version() > 0
    assert callable(register_vector)


@pytest.mark.parametrize("a, b, expected", [
    ([1.0, 0.0, 0.0], [1.0, 0.0, 0.0], 1.0),           # identical direction
    ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], 0.0),           # orthogonal
    ([1.0, 2.0, 3.0], [-1.0, -2.0, -3.0], -1.0),       # opposite
    ([1.0, 2.0, 3.0], [2.0, 4.0, 6.0], 1.0),           # scaling does not change the angle
])
def test_cosine_similarity_known_values(a, b, expected):
    assert cosine_similarity(np.array(a), np.array(b)) == pytest.approx(expected, abs=1e-12)


def test_ranking_of_normalised_embeddings_matches_cosine_similarity():
    """On unit vectors, the inner product (pgvector `<#>`, negated) ranks exactly like cosine similarity (`<=>`)."""
    rng = np.random.default_rng(20261026)
    corpus = rng.normal(size=(50, EMBEDDING_DIMENSIONS)).astype(np.float32)
    corpus /= np.linalg.norm(corpus, axis=1, keepdims=True)
    query = corpus[7] + 0.05 * rng.normal(size=EMBEDDING_DIMENSIONS).astype(np.float32)
    query /= np.linalg.norm(query)
    by_cosine = np.argsort([-cosine_similarity(query, row) for row in corpus])
    by_inner_product = np.argsort(-(corpus @ query))
    assert by_cosine[0] == 7
    assert list(by_cosine[:10]) == list(by_inner_product[:10])


def test_pgvector_text_encoding_round_trips_without_a_database():
    values = np.linspace(-1.0, 1.0, EMBEDDING_DIMENSIONS, dtype=np.float32)
    restored = Vector.from_text(Vector(values).to_text()).to_numpy()
    assert restored.shape == (EMBEDDING_DIMENSIONS,)
    np.testing.assert_allclose(restored, values, rtol=1e-6)
    half = HalfVector.from_text(HalfVector(values).to_text()).to_numpy()
    np.testing.assert_allclose(half, values, atol=1e-3)          # halfvec: 16-bit precision
