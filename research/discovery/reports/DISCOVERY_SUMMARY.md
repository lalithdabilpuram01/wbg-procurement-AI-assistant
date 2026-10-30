# Discovery Summary: Phase 1 Public Data Feasibility

**World Bank Procurement Intelligence Platform** · Run date 2026-10-20 · Status: **complete; awaiting review before Phase 2**

Detailed reports:
- [Source inventory](DATA_SOURCE_INVENTORY.md)
- [Schemas & DQ](SCHEMA_AND_DATA_PROFILING.md)
- [Entities & joins](ENTITY_RELATIONSHIP_FEASIBILITY.md)
- [Metadata](METADATA_ASSESSMENT.md)
- [ML](ML_FEASIBILITY_REPORT.md)
- [RAG](RAG_DATA_FEASIBILITY.md)
- [Refresh](DATA_REFRESH_ASSESSMENT.md)
- [Pending decisions](../../docs/decisions/PENDING_DECISIONS.md)

**Limitation:** the technical-assessment document was not in the workspace (`MasterPrompt_01.md` is empty). Requirements were
taken from the Phase 1 brief.

## Executive summary

1. **The core is usable.**
   - Contract awards (DS00005, 295,058 rows, FY2020+), projects (v3 API, 28,193), procurement notices (423,546) and IEG
     ratings (12,629) are all public, current and joinable on the World Bank **Project ID** at ≥99.75%.
   - Documents (619,903 in v3) link to projects the same way.
2. **Two official endpoints are silently stale.** The commonly documented `search.worldbank.org/api/v2/projects` and
   `/v2/wds` still answer, but stopped updating around January 2025. The live website uses **v3**. Any design built on v2 would
   miss every project approved since December 2024.
3. **Contract history has a hole.**
   - Public contract data covers **FY2020 onward** only.
   - FY2001–2016 exists solely as a prior-review subset with a different schema.
   - **FY2017–2019 is absent.**
4. **Procurement process dates are not in the contract dataset.** Tender start dates come only from linking contracts to
   procurement notices through a free-text borrower reference. That works for 70% of contracts (award notices) but only 20%
   (tender notices). After removing back-filled notices, **16.9% of contracts have a defensible tender-duration label**,
   concentrated in advertised methods.
5. **Contract amendments are not observable in public data, and re-bidding is only indirectly observable.** Proxy labels
   would be fabricated, so none were built.
   - *Corrected 2026-10-21:* procurement plans show 30.8% of activities cancelled. 11.7% of those have a near-identical
     successor activity, which gives probabilistic candidate re-bidding labels that need validation.
   - Defensible substitutes exist: project outcome (feasible), competition level, the ISR procurement rating
     (cross-sectional only), and closing-date extensions (conditional).
6. **Evaluations are project-level.** In the sampled ICR / ICRR / ISR / PAD documents, no contract identifiers appear. RAG
   evidence must be scoped to the project by default.

## Verified usable datasets

| Dataset | Role | Key figures |
|---|---|---|
| DS00005 Contract Awards (Finances One) | Contract facts | 295,058 rows; 282,890 contracts; 3,155 projects; daily full snapshot |
| Projects API **v3** | Project dimension, milestones, latest ISR ratings, financing ledger | 28,193 projects |
| Procurement Notices API (v2, current) | Tender / award notices; bidders and prices in award text | 423,546 notices; 98.7% of award notices STEP-parseable |
| Documents API **v3** + PDF / TXT files | RAG corpus | 619,903 docs; born-digital text; page mapping reliable |
| DS00053 IEG ratings | Project-outcome labels | 12,478 rated; 26.35% unsatisfactory-range |
| DS00047 / DS00001 (+ DS00975/6 history) | Loan context; point-in-time disbursement | 9,538 + 11,486 loans; monthly since 2011 |
| WGI / CPIA (Indicators API) | Country context, as-of | WGI 1996–2025 (208 economies); CPIA 2005–2025 (IDA) |

## Most reliable join keys

| Key | Class | Measured |
|---|---|---|
| Project ID (P-number) | Exact | 100% contract → project; 100% IEG → project; 99.75% notice → project |
| WB Contract Number × Supplier ID | Exact (unique row key) | 0 duplicates in 295,058 rows |
| Project + normalised borrower reference (package key) | Exact-after-normalisation | 70.2% contract → award notice; 19.9% → tender notice |
| + STEP Supplier ID in award text | Composite | 85.7% agreement; 41% of cluster contracts uniquely confirmed |
| Loan number (separator-normalised) | Exact-after-normalisation | Format verified across statements, notices, v3 |
| WB country code → ISO2 (curated 5-alias table) | Exact-after-normalisation | 100% of mappable rows; 12.4% regional / empty |

Not reliable:
- implementing agency (text; 17% high-similarity agreement)
- supplier identity beyond the STEP ID (580 multi-name IDs)
- fuzzy country / description matching (demonstrably wrong matches)

## Primary data-quality concerns

1. Stale v2 endpoints that look healthy.
2. The FY2017–2019 contract gap; DS01004 is prior-review only, without supplier IDs.
3. Free-text references shared across lots / frameworks (19% of contracts).
4. Back-filled tender notices (15% of matched candidates).
5. Late publication: award notices a median 127 days after award; recent contract months visibly incomplete (Jul–Oct 2026 well below Mar–Jun; exact horizon not yet measured).
6. Anonymised individual consultants (22% of rows); supplier IDs with multiple, sometimes unrelated, names.
7. Country codes: legacy non-ISO codes, empty codes for Côte d'Ivoire and Somalia, regional codes.
8. JV equal-split amounts (row grain ≠ contract).
9. No record-level change timestamps on contracts or notices.
10. Personal contact data inside notice text.

## Feasible vs unsupported ML outcomes

| Target | Classification |
|---|---|
| Project outcome risk (IEG MU or worse) | **Feasible with verified data** |
| Tender completion time (advertised methods) | **Conditionally feasible** |
| Low competition / single bidder | **Conditionally feasible** |
| ISR procurement-rating risk | **Conditionally feasible, cross-sectional only**. *Corrected 2026-10-21:* the procurement rating is not in public ISR documents; history requires snapshotting the API. |
| Closing-date extension | **Conditionally feasible** (needs ~1.1 GB history) |
| Contract amendment probability | **Not supported by verified public data** |
| Re-bidding risk | **Not supported as a verified label**; conditionally feasible with probabilistic plan-based labels (*corrected 2026-10-21*) |

## RAG feasibility

| Capability | Verdict |
|---|---|
| Lessons-learned retrieval filtered by country / sector / doc type / period | **Feasible** |
| Page-level citation | **Feasible** (PDF page index; `.txt` form-feeds in 20/22) |
| Section-level citation | **Feasible with heuristics**: no PDF bookmarks in 22/22; per-type templates, TOC and numbering work |
| Procurement plans | Valuable **structured** source (planned / actual dates, status, method). Extract to tables; don't chunk. |
| Contract-specific evidence from evaluations | **Generally not supported** |
| Technical-specification comparison | **Not supported** by the sources examined |

## Recommended scope for an initial enterprise release

1. **Data foundation (Phase 2a):**
   - Bronze snapshots with content-hash diffing for DS00005, procnotices, projects v3, WDS v3 (in-scope projects), DS00053,
     statements and WGI/CPIA.
   - Silver entities: Project, Loan, Package, Notice, Contract, Contract-Supplier line, Supplier (STEP-ID based), Country +
     indicator-year, Evaluation, Milestone, Document.
   - Every link typed E / EN / C / P with its method.
2. **Analytics:** historical benchmarking of methods, categories, amounts, competition (bidder counts) and observed tender
   durations (empirical quantiles), with explicit coverage indicators.
3. **RAG v1 (English first):** ICR / ICRR / PPAR lessons + PAD procurement / risk sections + ISR narratives. Hierarchical,
   section-bounded chunks, page citations, `evidence_scope = project`.
4. **ML v1:** IEG-outcome context-risk model and tender-duration baselines (quantiles → survival), both with leakage-controlled,
   temporally split evaluation.
5. **Deferred:** amendments, re-bidding, technical specifications, supplier entity resolution beyond the STEP ID, FY2017–19
   reconstruction.

---

## Implementation handoff

### A. Verified findings
- **Endpoints:** every source in the inventory was reached and its schema inspected. Exact URLs, parameters and HTTP results
  are recorded in `evidence/access_log.jsonl`.
- **Freshness:**
  - v2 projects / wds are frozen (no approvals after 2024-12-20; latest update stamp 2025-01-20).
  - v3 projects (28,193) and v3 wds (619,903) are current.
  - procnotices v2 is current (notices dated 2026-10-19).
- **API constraints:**
  - The search APIs cap offsets at 100,000 (Azure Search `$skip`).
  - procnotices ignores `strdate`/`enddate`.
  - Finances One returns HTTP 403 when `filter` is combined with `select`.
  - WGI codes are `GOV_WGI_*`.
- **DS00005:**
  - 295,058 rows, signing dates 2019-07-01 → 2026-10-13, no duplicates.
  - Contract × supplier is unique; 8,864 multi-supplier contracts (79.9% equal splits).
  - 22.07% anonymised individual consultants; 580 supplier IDs with multiple names.
  - 9,948 empty borrower codes, incl. Côte d'Ivoire and Somalia.
- **Join rates:** all match rates in the "Most reliable join keys" table above and in ER §4.
- **Award notices:**
  - 98.7% follow the STEP template.
  - Award-event → publication lag: median 127 days.
  - Single-bidder share: RFB 23.1%, RFQ 30.3%, QCBS 12.4%.
- **Tender duration:** 3,782 valid labels in the cluster. Medians: RFB 149, QCBS 371, RFQ 66 days.
- **Procurement plans:** 38/40 STEP plan PDFs parsed into 1,137 activities. Actual signing date 45.5%; cancelled
  **30.8%** (*corrected 2026-10-21* from 18.2%, a header-parsing artefact); activity → contract match 33.3%.
- **IEG:** 12,478 rated; 26.35% negative. As-of WGI available for 83.7% of FY1998+ projects.
- **Documents:** 22 PDFs analysed. 0 with bookmarks; scanned pages only in old PPARs / SAR; tables in 18/22.

### B. Unverified assumptions
- **Contract and notice data:**
  - Population-level tender-duration coverage (≈17% → ≈48k labels) extrapolated from a 276-project cluster.
  - Reason 24% of FY2020+ award notices have no DS00005 row.
  - Whether the 20% of multi-supplier contracts with unequal amounts are non-JV multi-awards.
  - Whether the 640 DS00005 projects without IBRD/IDA loans are trust-fund-financed.
  - Whether FY2017–19 can be partly reconstructed from award-notice text.
- **Procurement plans:** the semantics of plan status "Cancelled" (failed tender vs dropped activity).
- **Projects and evaluations:**
  - Whether v2 is officially deprecated.
  - Whether DS01695 (30,206 rows) contains full evaluation history.
- **Data quality not measured:** OCR quality on scanned pages; match rate of award-notice loan IDs to statements.
- **Missing requirements source:** requirements from the technical assessment (document unavailable).

### C. Architecture implications
1. **Pin and monitor source versions** (v3), with freshness checks per source. An endpoint can fail silently.
2. **Model a derived Procurement Package** between Project and Contract. Keep Contract and Contract-Supplier line as separate
   grains.
3. **Store every entity link with its class and method** (E / EN / C / P / U). Never collapse probabilistic links into facts.
4. **Make time first-class:** `event_date` vs `knowledge_available_date` on every fact. Platform-side snapshots are needed
   because most sources overwrite history.
5. **Use snapshot diffing** (content hashes) as the primary change-detection mechanism, rather than API incremental filters.
6. **For RAG:** hierarchical, section-bounded chunking; structured extraction (not chunking) for procurement plans, ISR rating
   blocks and award notices. Denormalise only low-cardinality filter fields onto chunks, and mandate `evidence_scope`.
7. **Scope ML to project-level outcome risk and observed durations.** The data does not support amendment models.
   Re-bidding needs validated, probabilistic plan-based labels first (*updated 2026-10-21*).
8. **Use a curated reference-data layer** (country crosswalk, method / category code maps, region taxonomy), versioned and
   tested.

### D. Critical risks
| Risk | Severity | Mitigation |
|---|---|---|
| Silent staleness of official endpoints | High | Freshness monitors; v3 pinning; alerting |
| FY2017–19 contract gap breaks trend analysis | High | Disclose in UI; do not interpolate |
| Tender-duration labels are biased to advertised methods and late-publication artefacts | High | Validity rules; scope models; survival methods; coverage flags |
| Project-level evidence presented as contract-specific | High | `evidence_scope` enforcement; reference-in-text rule |
| Leakage from snapshot-only sources (today's ratings, closing dates) | High | `knowledge_available_date`; platform snapshots; temporal CV |
| Supplier identity errors (multi-name IDs, anonymised individuals) | Medium | STEP-ID identity; candidate clusters only with review |
| Personal data in notice text | Medium | Mask before indexing; access controls |
| Free-text references (shared by lots, reused) | Medium | Package entity; supplier-ID composite |
| API limits (100k offset, WAF 403 on filter+select) | Low–Medium | Partitioned crawls; bulk channels |

### E. Recommended next steps
1. **Architect review** of this package and the decisions in `docs/decisions/PENDING_DECISIONS.md`. This is required before
   any Phase 2 work.
2. **Finalise the canonical domain model** (Project, Loan, Package, Notice, Contract, Contract-Supplier line, Supplier,
   Country-year, Evaluation, Milestone, Document, Section, Lesson) and the link-class table. The ER report is the input.
3. **Run the population-level notice join** (partitioned procnotices crawl, ≈425 calls) to replace the cluster-based tender
   coverage estimate with a measured one.
4. **Pilot STEP procurement-plan extraction** on ~500 plans: header normalisation, multilingual status map, de-duplication
   across plan versions. This decides whether it is worth upgrading the tender-duration and cancellation targets.
5. **Pilot hierarchical parsing** on ~200 ICR / ICRR / PAD documents, with a hand-labelled validation set for section
   boundaries and page citations (EN + FR + ES).
6. **Define DQ rules as code** from the issue register (Profiling §9), plus freshness / completeness monitors.
7. If approved: ingest the DS00975/6 history (≈1.1 GB) for point-in-time loan features.

### F. Decisions requiring architect approval
D-01 … D-17 in [`docs/decisions/PENDING_DECISIONS.md`](../../docs/decisions/PENDING_DECISIONS.md). The most consequential:
- **D-01 / D-02:** adopt v3 endpoints.
- **D-03:** contract history scope and the FY2017–19 gap.
- **D-05:** a derived Package entity.
- **D-06:** supplier identity policy.
- **D-09:** evidence-scope policy.
- **D-10:** initial ML targets.
- **D-11:** procurement-plan extraction at scale.
- **D-14:** personal-data handling.
- **D-15:** storage location / any cloud resources.

**No Phase 2 work has started.**

---

## Alignment addendum (2026-10-21)

The technical assessment became available after Phase 1 (found at `procurement-intelligence/assessment.md`). The full
review, with a 53-requirement traceability matrix, is
[`docs/requirements/ASSESSMENT_ALIGNMENT_REPORT.md`](../../docs/requirements/ASSESSMENT_ALIGNMENT_REPORT.md).

**Coverage:** 15 Covered, 30 Partially Covered, 8 Missing. Of the Missing items, 5 are design deliverables not yet started
(model specification, retrieval pipeline, rollout plan, KPIs and validation metrics), and 3 depend on data that is not
public (contract amendments, technical specifications, supplier performance).

**Most important new findings:**
1. Public ISR documents do **not** contain the Procurement component rating. Only the API's latest value exists, so
   history requires platform snapshots.
2. Official evidence conflicts often and in a structured way:
   - 782 projects have a later PPAR rating differing from the earlier ICR Review rating.
   - 13.1% of projects were satisfactory in their last ISR but unsatisfactory per IEG.
   - An evidence-precedence policy (D-18) is needed for assessment Module 3.
3. Procurement plans are richer than Phase 1 reported:
   - Phase 1 misread split headers. Corrected: cancelled 30.8%; market approach 99.9%; estimated amount 89.3%.
   - They are the main public source for market-approach options, planned-vs-actual dates and re-procurement signals.
4. Assessment-named data is available:
   - E&S risk classification (two schemes)
   - co-financing structure (26% of projects)
   - development objectives
   - the Open Knowledge Repository (open API; overlaps WDS)
   - the Procurement Regulations
5. About half of PADs exceed 100 pages, and Global Practice / sector taxonomies share no code. The assessment's example
   contract → lesson link via sector and time is a **scored contextual link**, not an identity link.

**New decisions:** D-17 (revised), D-18 to D-25 in `docs/decisions/PENDING_DECISIONS.md`. **No Phase 2 work has started.**
