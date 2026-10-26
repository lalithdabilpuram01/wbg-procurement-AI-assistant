"""Configuration for discovery scripts.

All tunables come from environment variables (optionally loaded from a .env file
at the repo root). No credentials are required: every source used in Phase 1 is
public and unauthenticated.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DISCOVERY_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = DISCOVERY_DIR.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines); never overrides existing env vars."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(REPO_ROOT / ".env")


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "y"}


@dataclass(frozen=True)
class Settings:
    # Endpoints (overridable so a mirror or proxy can be used)
    search_api_base: str = os.environ.get("WB_SEARCH_API_BASE", "https://search.worldbank.org/api/v2")
    fone_api_base: str = os.environ.get(
        "WB_FONE_API_BASE", "https://datacatalogapi.worldbank.org/dexapps/fone/api/apiservice"
    )
    fone_files_base: str = os.environ.get("WB_FONE_FILES_BASE", "https://financesonefiles.worldbank.org/f-one")
    indicators_api_base: str = os.environ.get("WB_INDICATORS_API_BASE", "https://api.worldbank.org/v2")

    # HTTP behaviour
    timeout_s: float = _env_float("WB_HTTP_TIMEOUT_S", 90)
    max_retries: int = _env_int("WB_HTTP_MAX_RETRIES", 5)
    backoff_base_s: float = _env_float("WB_HTTP_BACKOFF_BASE_S", 2.0)
    polite_delay_s: float = _env_float("WB_HTTP_POLITE_DELAY_S", 0.5)
    user_agent: str = os.environ.get(
        "WB_HTTP_USER_AGENT", "procurement-intelligence-discovery/0.1 (public-data feasibility study)"
    )

    # Sampling
    seed: int = _env_int("WB_SAMPLE_SEED", 20261020)
    contract_sample_per_fy: int = _env_int("WB_CONTRACT_SAMPLE_PER_FY", 125)
    cluster_projects: int = _env_int("WB_CLUSTER_PROJECTS", 300)
    doc_meta_per_type: int = _env_int("WB_DOC_META_PER_TYPE", 150)
    pdfs_per_type: int = _env_int("WB_PDFS_PER_TYPE", 3)

    # Bulk downloads go to data_cache/ (git-ignored). Disable to run API-sample-only.
    allow_bulk: bool = _env_bool("WB_ALLOW_BULK_DOWNLOADS", True)

    # Paths
    samples_dir: Path = DISCOVERY_DIR / "samples"
    cache_dir: Path = DISCOVERY_DIR / "data_cache"
    evidence_dir: Path = DISCOVERY_DIR / "evidence"
    profiling_dir: Path = DISCOVERY_DIR / "profiling"
    schemas_dir: Path = DISCOVERY_DIR / "schemas"
    reports_dir: Path = DISCOVERY_DIR / "reports"


SETTINGS = Settings()
