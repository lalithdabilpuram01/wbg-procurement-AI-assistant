"""Deterministic, non-sensitive synthetic embeddings and the NumPy reference ranking."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DIMENSIONS = 768                       # matches the retrieval model gte-base-en-v1.5
SEED = 20261026
ITEM_COUNT = 500
QUERY_SOURCES = (3, 77, 150, 299, 444)  # each query is a noisy copy of one stored item
QUERY_NOISE = 0.1


@dataclass(frozen=True)
class Item:
    """One synthetic stored row: id, label and a unit-length embedding."""
    item_id: int
    label: str
    embedding: np.ndarray               # float32, unit length


def unit_rows(matrix: np.ndarray) -> np.ndarray:
    return (matrix / np.linalg.norm(matrix, axis=1, keepdims=True)).astype(np.float32)


def items(count: int = ITEM_COUNT, seed: int = SEED) -> list[Item]:
    vectors = unit_rows(np.random.default_rng(seed).normal(size=(count, DIMENSIONS)))
    return [Item(item_id=i + 1, label=f"synthetic-{i + 1:04d}", embedding=vectors[i]) for i in range(count)]


def queries(stored: list[Item], seed: int = SEED + 1) -> list[tuple[int, np.ndarray]]:
    """(source item id, query vector) pairs; the source item should rank first."""
    rng = np.random.default_rng(seed)
    by_id = {item.item_id: item for item in stored}
    pairs = []
    for source in QUERY_SOURCES:
        noisy = by_id[source].embedding + QUERY_NOISE * rng.normal(size=DIMENSIONS)
        pairs.append((source, unit_rows(noisy[None, :])[0]))
    return pairs


def cosine_distance_ranking(stored: list[Item], query: np.ndarray, k: int) -> list[tuple[int, float]]:
    """Exact top-k by cosine distance (1 - cosine similarity), as pgvector's `<=>` operator defines it."""
    matrix = np.stack([item.embedding for item in stored]).astype(np.float64)
    q = query.astype(np.float64)
    similarity = matrix @ q / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(q))
    distance = 1.0 - similarity
    order = np.argsort(distance, kind="stable")[:k]
    return [(stored[i].item_id, float(distance[i])) for i in order]
