# Data Refresh & Incremental-Ingestion Assessment

Run date 2026-10-20. Probes: [`scripts/p04_refresh_probe.py`](../scripts/p04_refresh_probe.py) →
[`profiling/refresh_probe.json`](../profiling/refresh_probe.json); lag measurements in
[`profiling/publication_lag.json`](../profiling/publication_lag.json). **No synchronisation was implemented.**

## 1. Capability matrix (observed)

| Capability | DS00005 contracts | procnotices API | WDS v3 documents | Projects v3 | IBRD/IDA statements | IEG DS00053 | WGI / CPIA |
|---|---|---|---|---|---|---|---|
| Record-level last-modified timestamp | **No** | **No** | `last_modified_date`, **19.2% populated** | `proj_last_upd_date` (v2: 59.9% populated) | No | No | No (series-level `lastupdated`) |
| Dataset-level update stamp | `As of Date` (all rows identical, daily); page `dateModified` | — | — | — | `End of Period` (monthly) | `As of Date` (10/18/2026) | Source `lastupdated` (WGI 2026-09-25, CPIA 2026-08-03, WDI 2026-10-08) |
| Stable IDs | `WB Contract Number`; line key = contract × supplier | `id` (OP…) | `id`, `guid` (5 reuses in v2 cluster) | P-number | Loan / credit number | P-number (1 duplicate) | ISO2 + year |
| Version indicator | None | `notice_status` (Revised 117 / Cancelled 4 / Draft 11 of 30,977); award text `Notice Version No` (15.7% > 0) | `versiontyp` (Final / Buff / Gray / Revised…) | None | Monthly snapshot itself | None (latest evaluation only) | None |
| Server-side incremental filter | **Yes:** `filter=contract_signing_date>='YYYY-MM-DD'` (true date comparison, verified). But no "added since" filter. | **No:** `strdate`/`enddate` silently ignored; date equality → HTTP 400 | **Yes:** `strdate`/`enddate` on `docdt` (Jan 2021 → 2,897 docs) | Not tested beyond sort | API `filter` by loan / project | Bulk only (small) | `date=` range |
| Sort usable as change cursor | Default order not by date | Default = newest `submission_date`/`noticedate` first (verified) | `srt=last_modified_date desc` returns **nulls first** (28/30 missing) → unusable; `srt=docdt` works | `srt=p2a_updated_date` sorted in v2 | — | — | — |
| Pagination | `top`/`skip`, stable across repeated calls (verified); 1,000/page | `rows` ≤ 1,000, **`os` ≤ 100,000** | Same 100,000 cap | Same | `top`/`skip` | — | `page`/`per_page` |
| Full-snapshot channel | Bulk CSV 109 MB (daily) | DS00979 bulk (no refs / text) | — | `all.xlsx` (exists; not downloaded) | Bulk CSV (latest, history) | Bulk CSV | API (small) |
| Historical snapshots | **No** (overwrite) | No | No | No | **Yes**: monthly since Apr 2011 (DS00975/6) | No | Full time series |
| Deleted / corrected records detectable | Only by snapshot diff | Only by diff; `Cancelled` status for a few | Only by diff | Only by diff (378 v2 IDs absent from v3) | Monthly snapshots preserve history | Only by diff | Revisions overwrite past values |

## 2. Observed change dynamics

| Phenomenon | Evidence | Consequence |
|---|---|---|
| **Late-arriving contracts** | DS00005 rows per signing month: Mar 2026 2,733; Apr 2,645; May 2,228; Jun 2,337; Jul 1,951; Aug 1,314; Sep 925; Oct (to 13th) 138. Newest signing date 2026-10-13 vs snapshot 2026-10-20. | Incremental pulls keyed on signing date need a rolling look-back of ≥ 6 months (the precise horizon is not yet measured), or full daily snapshot diffs |
| **Late award notices** | Award event → award-notice publication: median **127 days**, p75 338, p90 633, p95 999; 5,025 of 22,591 published > 1 year late | Notice-derived labels and features must use `knowledge_available_date`; there is no safe "since last run" window |
| **Back-filled tender notices** | IFB / REOI published on or after the contract signing date, with deadline = publication date: 670 of 4,452 matched candidates (15%) | Ingestion must keep publication date distinct from event semantics; DQ rule to flag |
| **Notice revisions** | `notice_status = Revised` 117; award notice versions up to 28 | Upsert by `id`; keep version history; latest-wins for display, all versions for provenance |
| **Silent index staleness** | v2 projects / wds frozen around Jan 2025 while still answering HTTP 200 | Freshness monitors: max(approval date), max(datestored) and record counts per source |
| **Record removal** | 378 project IDs present in v2 are absent from v3 (mostly unapproved pipeline) | Tombstoning via snapshot comparison |
| **Future-dated metadata** | WDS `docdt` = 2026-12-31 for documents disclosed 2025-01-01 | Don't use `docdt` as a watermark; use `datestored` / `disclosure_date` + diff |
| **URL indirection** | `http://documents.worldbank.org/...pdf` → 302 → 302 → 301 → `https://documents1.worldbank.org/...` | Store the WDS `id`/`guid` as identity, not the URL; re-resolve on fetch |
| **No content versioning on files** | No `ETag`; `Last-Modified` header equals request time (not content time) | Detect file changes by SHA-256 of downloaded content |
| **Monthly vs daily cadence** | DS00005 as of 2026-10-20; statements as of 2026-09-30; IEG as of 2026-10-18 | Joins must record the snapshot date of each side |

## 3. Per-source refresh strategy (feasibility, not implementation)

| Source | Recommended pattern | Feasible? | Expected difficulty |
|---|---|---|---|
| DS00005 | Daily full bulk download (109 MB), then **diff** on the line key (contract × supplier) using a content hash | **Yes** | Low compute, moderate storage. Detects inserts, updates and deletes. API alternative: signing-date window with a long look-back (misses corrections to older rows) |
| procnotices | Two-tier: (a) daily "newest-first" pages until a run of already-seen IDs is reached (default sort is newest first); (b) periodic full re-crawl **partitioned** (by `notice_type_exact` × `project_ctry_name_exact`, all partitions < 100,000) to catch revisions and deletions | **Yes**, with partitioning | 423k records ≈ 425 pages of 1,000 at full re-crawl; revisions are not flagged with timestamps, so detection needs content hashes |
| WDS v3 | `strdate`/`enddate` window on `docdt` with look-back, plus periodic per-project re-crawl for projects in scope; file content hashes | **Partially** | `docdt` is not an ingestion date (future dates exist); `last_modified_date` sparse; 100k offset cap requires per-type / per-project partitioning |
| Projects v3 | Daily / weekly full crawl (28k records ≈ 29 pages) + diff | **Yes** | Small; nested arrays (milestones, ratings) need deterministic hashing |
| IBRD / IDA statements | Monthly latest snapshot; **historical** bulk once (~1.1 GB), then monthly append | **Yes** | Monthly cadence only |
| IEG DS00053 | Full reload (12.6k rows) on change of `As of Date`; keep prior versions (only the latest evaluation is retained at source) | **Yes** | The source overwrites earlier evaluations, so the platform must keep history itself |
| WGI / CPIA | Annual / on `lastupdated` change; store vintage | **Yes** | Revisions alter past values, so as-of features need vintage retention for strict reproducibility |

## 4. Expected difficulties

1. **Late-arriving records.** Contracts and award notices appear months to years after the event. Models and dashboards
   must not treat recent periods as complete. Recommend a completeness indicator per (source, month).
2. **Source corrections.** There are no record-level modified stamps for DS00005 or notices. Corrections appear only as value
   changes in the next snapshot, which requires content-hash diffing and SCD-style history.
3. **Duplicate ingestion.** IDs are stable within a channel, but:
   - DS00979 numeric IDs need the `OP` mapping.
   - WDS can return the same document under several projects (multi-project `projectid`).
   - Award notices for multi-lot packages share references.

   Recommend idempotent upserts on natural keys.
4. **Changed document URLs.** Redirect chains to a different host were observed. Persist WDS identity and checksum.
5. **Deleted or replaced documents.** No tombstones. Detect via periodic per-project crawls, and keep "last seen" timestamps.
6. **Revised project metadata.** Status, closing dates and ratings change. v3 shows only current values (one ISR rating),
   and the platform must snapshot them to build history. Monthly loan snapshots are the only native history found.
7. **Silent staleness of endpoints** (v2 example). Monitor freshness, and alert when a source's max date stops advancing.

## 5. Conclusion

Native incremental retrieval is **weak**: no record-level change timestamps on the two most important procurement sources.
**Periodic snapshot comparison is feasible** for every source examined. Snapshot sizes range from 12.6k rows (IEG) to 423k
notices, and the 109 MB contracts file is published daily. That approach is recommended as the primary change-detection
mechanism, with source-specific look-back windows for efficiency.

---

## Alignment update (2026-10-21)

Assessment Module 4 requires a closed loop in which new milestones, supervision notes and contract amendments update models
and indices. Additional evidence:

1. **Sources overwrite evaluation history.**
   - DS00053 keeps only the latest evaluation.
   - For 782 projects, its rating (later PPAR) differs from the earlier ICR Review / PCR rating still exposed in v3
     `ieg_ratings`.
   - v3 `isr_ratings` holds only the latest ISR, and component ratings (Procurement, FM) are **not** in public ISR
     documents.
   - **The platform must snapshot these values to build any history** (D-20).
2. **Supervision notes:**
   - ISRs (59,693) and Aide Memoires (2,815) are available.
   - ISR series contain re-uploaded duplicates (one sequence number up to 8×), so ingestion must be idempotent on
     (project, ISR sequence number).
3. **Contract amendments:** no public source was found (unchanged). They cannot drive the feedback loop.
4. **Procurement plans:** are re-published as new versions (143,335 documents). Plan-version diffs are the only public
   signal of changes in package status (e.g. cancellation → re-procurement).
