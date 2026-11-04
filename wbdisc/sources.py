"""Thin clients for the public World Bank APIs used in discovery.

Response shapes were verified empirically on 2026-10-20 (see evidence/):
  * search.worldbank.org/api/v2/{projects,procnotices,wds}: `rows` (<=1000 observed),
    `os` offset, `fl` field list, `<field>=` filters, `srt`/`order` sort.
    projects/wds return a dict keyed by id; procnotices returns a list.
  * Finances One apiservice: `datasetId`, `resourceId`, `type=json`, `top`, `skip`,
    `select`, `filter` (SQL-like, e.g. project_id='P151416'; OData `eq` is rejected).
  * api.worldbank.org/v2 indicators: `[meta, rows]` paged by `page`/`per_page`.
"""
from __future__ import annotations

from typing import Any, Iterator

from .config import SETTINGS
from .http import fetch_json

SEARCH_COLLECTION_KEY = {"projects": "projects", "procnotices": "procnotices", "wds": "documents"}


def _endpoint_url(endpoint: str) -> tuple[str, str]:
    """'projects' -> v2 (default); 'v3/projects' -> v3. Returns (url, collection key).

    v2 projects and v2 wds indexes were observed to be frozen at ~Jan 2025 (2026-10-20);
    v3 exists for projects and wds only (v3/procnotices returns 404).
    """
    version, _, name = endpoint.rpartition("/")
    base = SETTINGS.search_api_base.rsplit("/", 1)[0] + "/" + version if version else SETTINGS.search_api_base
    return f"{base}/{name}", SEARCH_COLLECTION_KEY[name]


def search_page(endpoint: str, params: dict[str, Any], *, purpose: str = "") -> tuple[int, list[dict[str, Any]]]:
    """Return (total, records) for one page of a search.worldbank.org endpoint."""
    q = {"format": "json", **params}
    url, key = _endpoint_url(endpoint)
    data = fetch_json(url, q, purpose=purpose)
    if "error" in data and "total" not in data:
        raise ValueError(f"search API error for {endpoint}: {str(data)[:300]}")
    total = int(data.get("total", 0))
    payload = data.get(key, {})
    if isinstance(payload, dict):
        records = [v for k, v in payload.items() if k != "facets" and isinstance(v, dict)]
    else:
        records = list(payload)
    return total, records


def search_all(endpoint: str, params: dict[str, Any], *, page_size: int = 1000, max_records: int | None = None,
               purpose: str = "") -> Iterator[dict[str, Any]]:
    """Paginate with `os` offsets until `total` is reached or a page comes back empty."""
    offset, seen = 0, 0
    while True:
        total, recs = search_page(endpoint, {**params, "rows": page_size, "os": offset}, purpose=purpose)
        if not recs:
            return
        for r in recs:
            yield r
            seen += 1
            if max_records and seen >= max_records:
                return
        offset += len(recs)
        if offset >= total:
            return


def search_facets(endpoint: str, fields: list[str], params: dict[str, Any] | None = None) -> dict[str, dict[str, int]]:
    """Return facet counts ({field: {value: count}}) from a Search API endpoint without fetching any records."""
    q = {"format": "json", "rows": 0, "fct": ",".join(fields), "fct_limit": 500, **(params or {})}
    url, key = _endpoint_url(endpoint)
    data = fetch_json(url, q, purpose="facets")
    facets = data.get("facets") or data.get(key, {}).get("facets", {})
    out: dict[str, dict[str, int]] = {}
    for f, vals in (facets or {}).items():
        items = vals.values() if isinstance(vals, dict) else vals
        out[f] = {v["name"]: int(v["count"]) for v in items}
    return out


def fone_page(dataset_id: str, resource_id: str, *, top: int = 1000, skip: int = 0, filter_: str | None = None,
              select: str | None = None, purpose: str = "") -> tuple[int, list[dict[str, Any]]]:
    """Fetch one page of a Finances One dataset; returns (total record count, records on this page)."""
    params: dict[str, Any] = {"datasetId": dataset_id, "resourceId": resource_id, "type": "json", "top": top, "skip": skip}
    if filter_:
        params["filter"] = filter_
    if select:
        params["select"] = select
    data = fetch_json(SETTINGS.fone_api_base, params, purpose=purpose)
    if not isinstance(data, dict) or "data" not in data:
        raise ValueError(f"Unexpected Finances One payload for {dataset_id}: {str(data)[:200]}")
    return int(data.get("count", 0)), data["data"]


def fone_all(dataset_id: str, resource_id: str, *, filter_: str | None = None, select: str | None = None,
             page_size: int = 1000, purpose: str = "") -> Iterator[dict[str, Any]]:
    """Yield every record of a Finances One dataset, page by page."""
    skip = 0
    while True:
        total, recs = fone_page(dataset_id, resource_id, top=page_size, skip=skip, filter_=filter_, select=select,
                                purpose=purpose)
        yield from recs
        skip += len(recs)
        if not recs or skip >= total:
            return


def indicator_all(indicator: str, *, source: int | None = None, countries: str = "all",
                  date: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch every page of a World Bank indicator; returns (metadata of the last page, all rows)."""
    page, rows, meta = 1, [], {}
    while True:
        params: dict[str, Any] = {"format": "json", "per_page": 20000, "page": page}
        if source:
            params["source"] = source
        if date:
            params["date"] = date
        data = fetch_json(f"{SETTINGS.indicators_api_base}/country/{countries}/indicator/{indicator}", params,
                          purpose=f"indicator {indicator}")
        if not isinstance(data, list) or len(data) < 2:
            raise ValueError(f"Indicator API error for {indicator}: {str(data)[:300]}")
        meta = data[0]
        rows.extend(data[1] or [])
        if page >= int(meta.get("pages", 1)):
            return meta, rows
        page += 1
