"""Loads the existing RAG answering modules, which are scripts importing each other by path (not a package).

The paths are added once; the modules are imported exactly as `rag/answering/pipeline.py` does itself.
"""
from __future__ import annotations

import importlib
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType

PROJECT = Path(__file__).resolve().parents[2]
ANSWERING = PROJECT / "rag" / "answering"
RETRIEVAL = PROJECT / "rag" / "retrieval"


@lru_cache(maxsize=None)
def answering(module: str) -> ModuleType:
    """One of rag/answering's modules: pipeline, generator, schemas, validate, evidence, query."""
    for path in (ANSWERING, RETRIEVAL):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    return importlib.import_module(module)
