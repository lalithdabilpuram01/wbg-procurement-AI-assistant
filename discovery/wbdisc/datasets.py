"""Loaders for the raw samples/caches produced by scripts/s0*.py.

All CSVs are read with dtype=str and keep_default_na=False so that empty strings,
'NA', 'N/A', etc. are preserved exactly as published (no implicit NaN coercion).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import SETTINGS
from .raw_store import read_jsonl

C = SETTINGS.cache_dir
S = SETTINGS.samples_dir


def read_csv_raw(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def contracts() -> pd.DataFrame:
    return read_csv_raw(C / "ds00005_contract_awards.csv")


def contracts_legacy() -> pd.DataFrame:
    return read_csv_raw(C / "ds01004_contract_awards_legacy.csv")


def contracts_api_sample() -> list[dict]:
    return read_jsonl(S / "contract_awards" / "ds00005_api_sample.jsonl")


def projects() -> list[dict]:
    """v3 projects population (current). See projects_v2() for the frozen v2 index."""
    return read_jsonl(C / "projects_v3_population.jsonl.gz")


def projects_v2() -> list[dict]:
    return read_jsonl(C / "projects_population.jsonl.gz")


def projects_v3_cluster() -> list[dict]:
    return read_jsonl(S / "projects" / "projects_v3_cluster_allfields.jsonl.gz")


def projects_allfields() -> list[dict]:
    return read_jsonl(S / "projects" / "projects_allfields_sample.jsonl.gz")


def notices_cluster() -> list[dict]:
    return read_jsonl(S / "procnotices" / "procnotices_cluster_sample.jsonl.gz")


def notices_global() -> list[dict]:
    return read_jsonl(S / "procnotices" / "procnotices_global_sample.jsonl.gz")


def notices_bulk() -> pd.DataFrame:
    return read_csv_raw(C / "ds00979_procurement_notice.csv")


def cluster_projects() -> list[str]:
    return (S / "procnotices" / "cluster_projects.txt").read_text().split()


def docs_by_type(version: str = "v3") -> list[dict]:
    name = "wds_v3_meta_sample_by_type" if version == "v3" else "wds_meta_sample_by_type"
    return read_jsonl(S / "documents" / f"{name}.jsonl.gz")


def docs_cluster(version: str = "v3") -> list[dict]:
    name = "wds_v3_cluster_meta" if version == "v3" else "wds_cluster_meta"
    return read_jsonl(S / "documents" / f"{name}.jsonl.gz")


def ieg() -> pd.DataFrame:
    return read_csv_raw(S / "ieg" / "ds00053_ieg_ratings_full.csv")


def ibrd_latest() -> pd.DataFrame:
    return read_csv_raw(C / "ds00047_ibrd_latest.csv")


def ida_latest() -> pd.DataFrame:
    return read_csv_raw(C / "ds00001_ida_latest.csv")


def finance_history(ds: str) -> list[dict]:
    return read_jsonl(S / "finance" / f"{ds.lower()}_history_sample.jsonl.gz")


def indicator(code: str) -> list[dict]:
    return read_jsonl(S / "indicators" / f"{code}.jsonl.gz")
