"""Reference records that incoming documents are resolved against: the registered projects and the contract snapshot
(the same verified inputs the corpus extraction uses), loaded once per process."""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from rag.ingestion import config, contracts_snapshot, evidence

V3_VOCAB = config.OUTPUT_V3 / "reference" / "vocab.json"


def normalise_reference(value: str | None) -> str | None:
    """A procurement reference without spacing or punctuation differences ("ET-ERA 123/CW" == "ETERA123CW")."""
    cleaned = re.sub(r"[^A-Z0-9]", "", (value or "").upper())
    return cleaned or None


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80]


@dataclass
class Reference:
    projects: dict[str, dict]                         # project id -> title, country, sector, approval year
    contracts: list[dict]
    codes: set[str]
    contract_index: dict                               # the extractor's own index (plan activity -> contract links)
    vocab: set[str]
    lower_vocab: set[str]
    contracts_by_number: dict[str, dict] = field(default_factory=dict)
    contracts_by_reference: dict[tuple[str, str], list[dict]] = field(default_factory=dict)
    countries: dict[str, str] = field(default_factory=dict)       # lower-case country name -> code

    def project(self, project_id: str | None) -> dict | None:
        return self.projects.get(project_id or "")


@lru_cache(maxsize=1)
def load() -> Reference:
    with config.PROJECT_MANIFEST.open(newline="") as fh:
        projects = {r["project_id"]: {"title": r["project_title"], "country_code": r["country_code"], "country_name": r["country_name"],
                                      "sector": r["sector"] or None, "approval_fy": r["approval_fy"] or None, "region": r["region"]}
                    for r in csv.DictReader(fh)}
    contracts, _ = contracts_snapshot.load(v3=True)
    codes = evidence.method_codes(contracts)
    vocab = json.loads(V3_VOCAB.read_text())
    ref = Reference(projects, contracts, codes, evidence.index_contracts(contracts, codes), set(vocab["words"]),
                    set(vocab["lower_words"]))
    for c in contracts:
        if c.get("wb_contract_number"):
            ref.contracts_by_number[str(c["wb_contract_number"])] = c
        norm = normalise_reference(c.get("borrower_contract_reference"))
        if norm:
            ref.contracts_by_reference.setdefault((c["project_id"], norm), []).append(c)
    for p in projects.values():
        if p["country_name"]:
            for name in country_names(p["country_name"]):
                ref.countries[name] = p["country_code"]
    return ref


SHORT_NAMES = {"argentine republic": "argentina"}


def country_names(official: str) -> set[str]:
    """The official name and the name documents actually use ("Federal Democratic Republic of Ethiopia" -> "ethiopia")."""
    name = official.lower()
    return {name, name.split(" of ")[-1], SHORT_NAMES.get(name, name)}
