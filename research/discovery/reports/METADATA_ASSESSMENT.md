# Metadata Feasibility Assessment

Run date 2026-10-20. Availability percentages are measured values from `profiling/` (see
[SCHEMA_AND_DATA_PROFILING.md](SCHEMA_AND_DATA_PROFILING.md)).

**Availability codes**
- **P (Provided):** published directly by the source
- **D-R (Derived, reliable):** deterministic rule, verified on samples
- **D-U (Derived, uncertain):** heuristic or probabilistic, needs validation
- **M (Missing):** not obtainable from public sources

**Quality status:** `good` (≥95% populated and consistent), `fair` (usable with caveats), `poor` (sparse or inconsistent),
`n/a` (platform-generated).

---

## A. Business metadata

| Field | Provided? | Availability (measured) | Uncertainty created |
|---|---|---|---|
| Project ID | P (all sources) | 100% valid in DS00005, DS00979, IEG, statements; 100% in WDS (8.25% multi-valued) | Multi-project documents need M:N |
| Contract identifier | P (`WB Contract Number`) | 100%; FY2020+ only | No identifier for FY2017–19 |
| Procurement package | **D-R** (`project_id` + `normalize_ref(ref)`) | 100% of contracts have a reference; 19% of contracts share a package | Reference is free text entered by borrowers |
| Procurement category | P | 100% (4 values) in DS00005; `procurement_group` 97.7% in notices | Notice codes (CS/GO/CW/NC) need a mapping to DS00005 labels |
| Procurement method | P | 100% in DS00005 (18 labels); notices 99.25% (17 codes, incl. legacy ICB/NCB) | Label vs code mapping; double-space label |
| Review type (prior/post) | P | 99.94% | — |
| Country | P (WB code) / D-R (ISO2) | 96.6% coded; 12.4% regional or empty | Regional projects have no single country |
| Region | P | 100%; taxonomies differ by dataset and over time | Needs a region dimension with history |
| Sector | P (v3 `major_sectors` with %, codes) | v3 `major_sector_name` 100% for IEG-joined projects | Contract-level sector is **not** available (GP is project-level, 48% multi-valued) |
| Global Practice | P (DS00005, IEG) | DS00005 89.7%; IEG 64.0% | Taxonomy changed over time |
| Supplier | P (`Supplier ID`, name, registration country) | 100% / 22% hashed | Identity ambiguity (580 multi-name IDs) |
| Implementing agency | P (v3 `impagency`) / D-U (notice `contact_organization`) | **29.8%** / 23.6% | Free text, multi-valued, low agreement (17.3% ≥90 similarity) |
| Contract amount (USD) | P | 100% | JV split; committed, not final |
| Contract amount (local currency) | D-R from award notice text | 85.1% of award notices | Only where the notice matches (J6) |
| Contract signing date | P | 100% | — |
| Award notification date | D-R from award notice text | 51.4% (signature date 44.0% in other template) | Two template variants have different semantics |
| Contract duration | D-R from award notice text | 97.0% | Planned duration, not actual |
| Number of bidders | D-R from award notice text | 75.8% overall; INDV unusable (86% zero) | Post-award information (leakage for award-time models) |
| Loan(s) financing a contract | D-R from award notice text | 98.7% of award notices | Only via a matched notice |
| Tender publication date | P (notice) + D-R (link) | 19.9% of contracts link to a tender notice; 16.9% valid | Back-filled notices must be excluded |
| Bid submission deadline | P (API notice) | IFB 94.6%, REOI 93.9% | DS00979 deadlines are placeholders |
| Project approval / effectiveness / closing | P (v3 milestones, statements) | Approval 97.7%; effectiveness 53.2%; closing 56.1% | Pipeline projects have future dates |
| Project status | P | 100% (v3) | Changes over time; no history except via snapshots |
| IEG outcome rating | P (DS00053) | 98.8% rated of 12,629 | Latest evaluation only |
| ISR procurement rating | P (v3 `isr_ratings`) | 25.1% of projects; latest ISR only | History needs ISR document parsing |

## B. Document metadata

| Field | Provided? | Availability | Uncertainty |
|---|---|---|---|
| Source document identifier | P (`id`; also `guid`, `repnb`, `chronical_docm_id`) | 100% (`repnb` 87.5%) | 5 `guid`/`chronical_docm_id` reuses in v2 cluster |
| Title | P (`display_title`) | 100% | Contains embedded newlines; needs whitespace normalisation |
| Document type | P (`docty`, `majdocty`) | 100% | Label noise (newlines); 100 types |
| Project ID(s) | P (`projectid`) | 100%; 8.25% comma lists | M:N |
| Country | P (`count`, `count_key`) | 99.8% | Names, not codes |
| Publication / document date | P (`docdt`, `disclosure_date`, `datestored`) | 100% | Future `docdt` observed (2026-12-31) |
| Language | P (`lang`, `available_in`) | 99.2% | Noisy labels; translations are separate records |
| Version | P (`versiontyp`) | 87.1% | Values like Final / Buff cover / Gray cover / Revised are document-stage, not revision numbers |
| Source URL | P (`pdfurl`, `txturl`, `url`) | 100% in v3 | `http://` URLs redirect 3 times to `documents1…`; must store canonical final URL |
| Page count | P in v2 (`no_of_pages`, 99.8%); **absent in v3** | — | D-R from the PDF |
| Page number (per chunk) | **D-R** | PDF page index always; WB `.txt` has one form-feed per page in **20/22** files | 2/22 `.txt` files have no form-feeds → use PDF |
| Printed page label | D-U | Front matter uses roman numerals; printed ≠ physical page | Store both physical index and label |
| Section / subsection | **D-U** | 0/22 PDFs have bookmarks; headings from layout (bold/size) and numbering (`I.`, `A.`, `ANNEX 1`), plus text TOC with dot leaders | Heading detection is template-dependent; French/Spanish ICRs not covered by English keyword rules |
| Section type (lessons, procurement, risk) | D-U | English keyword match found "lessons" headings in all English ICR/ICRR/PPAR samples; 0 in FR/ES ICRs | Needs multilingual taxonomy |
| Table indicator / table content | D-R (pdfplumber) | Tables found in 18/22 PDFs | Wrapped headers; merged cells |
| Scanned / OCR flag | D-R | Scanned pages in 5/22 (old PPARs 15/54 & 10/71; SAR 3/114; 2 PADs 1–2 pages) | OCR quality unknown (not tested) |
| Abstract / keywords | P | 55.3% / 79.4% | — |

## C. Technical metadata

All platform-generated. Phase 1 already produces several of these in [`samples/MANIFEST.jsonl`](../samples/MANIFEST.jsonl)
and [`evidence/access_log.jsonl`](../evidence/access_log.jsonl).

| Field | How obtained | Phase 1 status |
|---|---|---|
| Source system | Constant per extractor (`finances_one.DS00005`, `search_api.v3.projects`, …) | Implemented in MANIFEST `source` |
| Source endpoint + request parameters | Recorded per request | Implemented (access log; `_extract` on every raw record) |
| Source snapshot date | DS00005/DS00053/statements `As of Date` / `End of Period` (P); others = extraction time | Available |
| Extraction timestamp | Recorded | Implemented |
| File checksum (SHA-256) | Computed | Implemented |
| Record count | Computed | Implemented |
| HTTP status / attempts | Recorded | Implemented |
| Parsing status / parser name+version | Recorded per document | Partially: `document_parsing.json` captures errors; versioning to add |
| Record ingestion timestamp | Platform | Phase 2 |
| Record content hash | Platform (hash of canonicalised source record) | Phase 2: needed for change detection (no source-side modified timestamps) |
| DQ validation status | Platform rules (this report's issue register) | Phase 2 |
| Transformation / normalisation version | Platform (e.g. `normalize_ref` v1, country crosswalk v1) | Phase 2 |

## D. Evidence & provenance metadata

| Field | How obtained | Feasibility |
|---|---|---|
| Source record ID | P (contract number, notice id, project id, document id) | Good |
| Source document ID | P (WDS `id` + `guid`) | Good |
| Page (physical) + printed label | D-R / D-U | Good / fair |
| Section path | D-U | Fair: needs layout parser + review |
| Evidence extraction method | Platform enum (`structured_field`, `regex_award_notice_v1`, `pdf_table_v1`, `layout_section_v1`, `ocr`) | Feasible |
| **Evidence scope** | Platform enum: `contract`, `package`, `project`, `country`, `portfolio` | **Required** to prevent presenting project-level lessons as contract-specific |
| Entity-link class + confidence | Platform: E / EN / C / P / U + score | Feasible; classes defined in the ER report |
| Evidence observation date | Document `docdt` / notice date / snapshot date | Good (beware future `docdt`) |
| Knowledge-available-at date | Earliest date the platform could have known the fact (publication date, *not* event date) | Required for leakage control. Award notices publish a median 127 days after the event. |
| Last verification timestamp | Platform | Feasible |
| Superseded indicator | Platform, via snapshot diff / notice version / `versiontyp` | Fair: no source-side supersession flags except `notice_status=Revised` and notice version numbers |

---

## Proposed metadata dictionary

`quality_status`: good / fair / poor / n/a. `intended_usage`: F = filtering, J = joining, ML = feature or label,
C = citation, P = provenance, D = display.

| field_name | description | data_type | source | availability | derivation_rule | quality_status | intended_usage |
|---|---|---|---|---|---|---|---|
| project_id | World Bank project P-number | string(7) | all | P | uppercase; split comma lists | good | F,J,C |
| contract_id | WB Contract Number | string | DS00005 | P | — | good | J,P |
| contract_supplier_line_id | contract × supplier row key | string | DS00005 | D-R | `wb_contract_number` + `\|` + `supplier_id` | good | J |
| package_key | derived procurement package | string | DS00005, notices, PP | D-R | `project_id` + `\|` + `normalize_ref(reference)` | fair | J |
| borrower_reference_raw | reference as published | string | DS00005, notices | P | none (keep raw) | fair | P,D |
| notice_id | procurement notice ID | string | procnotices | P | DS00979 numeric → `OP` + zfill(8) | good | J,P |
| notice_type | IFB / REOI / Prequal / GPN / Contract Award | enum | procnotices | P | — | good | F |
| notice_status | Published / Revised / Cancelled / Draft | enum | procnotices | P | — | good | F |
| notice_version_no | award notice revision number | int | award notice text | D-R | `Notice Version No:` regex | good | P |
| procurement_category | Goods / Works / Consultant / Non-consulting | enum | DS00005; notices (`procurement_group`) | P | CS→Consultant, GO→Goods, CW→Works, NC→Non-consulting (**mapping to validate**) | good | F,ML |
| procurement_method | selection method | enum | DS00005; notices | P | label↔code mapping; collapse whitespace | good | F,ML |
| review_type | Prior / Post | enum | DS00005 | P | — | good | F,ML |
| country_wb_code | WB country code | string | DS00005, projects | P | — | fair | J |
| country_iso2 | ISO 3166-1 alpha-2 | string | derived | D-R | `countries.to_iso2()`; null if regional | good | F,J |
| is_regional_operation | multi-country flag | bool | derived | D-R | `countries.is_regional()` or empty code | good | F,ML |
| region | WB region (current taxonomy) | enum | projects v3 | P | map legacy labels | fair | F |
| sector_codes | sectors with % | array<struct> | projects v3 | P | — | good | F,ML |
| global_practice | GP labels | array<string> | DS00005, IEG | P | split `;` | fair | F |
| supplier_id | STEP supplier ID | string | DS00005, award text | P | flag 16-hex as anonymised | fair | J |
| supplier_is_anonymised | individual consultant hash | bool | derived | D-R | regex `^[0-9a-f]{16}$` | good | F |
| supplier_name_key | normalised name for candidate grouping | string | derived | D-U | `normalize_supplier_name()` | poor | J (candidate only) |
| supplier_country_iso2 | supplier registration country | string | DS00005 | P/D-R | treat `null` literal as missing | good | F,ML |
| implementing_agency_text | as published | string | projects v3 | P | none | poor | D |
| amount_usd_committed | contract (JV share) amount | decimal | DS00005 | P | parse; never default 0 | good | ML,D |
| amount_local_signed | signed price, local currency | decimal + ccy | award text | D-R | regex | fair | ML,D |
| contract_signing_date | signing date | date | DS00005 | P | MM/DD/YYYY (bulk) or DD-Mon-YYYY (API) | good | F,ML |
| tender_published_date | first valid tender notice date | date | notices | D-R | earliest IFB/REOI/Prequal by package; validity rule | fair | ML |
| bid_deadline_date | submission deadline | datetime | notices API | P | ignore when ≤ notice date | fair | ML |
| award_notification_date | award decision notice date | date | award text | D-R | regex | fair | ML |
| contract_duration_planned | planned duration | int + unit | award text | D-R | regex | good | ML |
| bidder_count | distinct bidders by supplier ID | int | award text | D-R | union of awarded/evaluated/rejected IDs; null for INDV | fair | ML (post-award only) |
| project_approval_date | Board approval | date | v3 | P | — | good | F,ML |
| project_effectiveness_date | effectiveness | date | v3 milestones / statements | P | — | fair | ML |
| project_closing_date_current | current closing date | date | v3 / statements | P | — | fair | ML (leakage-prone) |
| project_status | Active / Closed / Dropped / Pipeline | enum | v3 | P | — | good | F |
| ieg_outcome | IEG outcome rating | enum(6)+Not Rated | DS00053 | P | `Not Rated` → missing | good | ML label |
| isr_procurement_rating_latest | latest ISR procurement rating | enum | v3 | P | — | fair | ML label (cross-sectional) |
| wgi_*_est (as-of) | governance estimate for country, year t−1 | float | Indicators API | P | as-of join on ISO2, year | good | ML |
| cpia_* (as-of) | CPIA rating, year t−1 | float | Indicators API | P | IDA countries only | fair | ML |
| doc_id | WDS document entity ID | string | WDS v3 | P | — | good | C,P |
| doc_guid | URL path ID | string | WDS v3 | P | — | good | P |
| doc_report_number | report number | string | WDS v3 | P | — | fair | C,D |
| doc_type | document type | enum | WDS v3 | P | `clean_label()` | good | F |
| doc_major_type | major document type | enum | WDS v3 | P | — | good | F |
| doc_date | document date | date | WDS v3 | P | flag future dates | fair | F,C |
| doc_disclosure_date | public disclosure date | date | WDS v3 | P | — | good | F (knowledge-available-at) |
| doc_language | language | enum | WDS v3 | P | canonicalise synonyms | fair | F |
| doc_version_type | Final / Buff cover / … | enum | WDS v3 | P | — | fair | F |
| doc_pdf_url_canonical | final HTTPS URL | string | WDS v3 + redirect | D-R | follow redirects once, store final | good | C |
| doc_page_count | pages | int | PDF | D-R | PyMuPDF | good | D |
| doc_is_scanned_page | per page | bool | PDF | D-R | <80 chars text and image present | fair | P |
| chunk_page_start / end | physical page span | int | parser | D-R | from PDF page index | good | C |
| chunk_page_label | printed page label | string | parser | D-U | page header/footer detection | fair | C |
| section_path | e.g. `V > A` / "Lessons and Recommendations" | string | parser | D-U | layout + numbering + TOC | fair | F,C |
| section_type | lessons / procurement / risk / results / fiduciary | enum | parser | D-U | multilingual heading taxonomy | poor→fair | F |
| evidence_scope | contract / package / project / country | enum | platform | D-R | from source type + link class | n/a | F,C |
| link_class | E / EN / C / P / U | enum | platform | D-R | join rule that created the link | n/a | P |
| link_confidence | 0–1 | float | platform | D-R/D-U | 1.0 for E/EN/C; score for P | n/a | F,P |
| knowledge_available_date | when the fact became public | date | platform | D-R | publication / disclosure / snapshot date | n/a | ML (leakage), F |
| source_system | extractor ID | string | platform | D-R | constant | n/a | P |
| source_snapshot_date | `As of Date` / `End of Period` / extraction time | date | source / platform | P/D-R | — | good | P |
| extracted_at | extraction timestamp | timestamp | platform | D-R | — | n/a | P |
| raw_sha256 | checksum of raw payload | string | platform | D-R | — | n/a | P |
| record_content_hash | hash of canonicalised record | string | platform | D-R | for change detection | n/a | P |
| parser_name / parser_version | parser identity | string | platform | D-R | — | n/a | P |
| dq_status | pass / warn / fail + rule IDs | enum | platform | D-R | issue register rules | n/a | F,P |
| superseded_by | newer record/doc ID | string | platform | D-U | snapshot diff, notice version, Revised status | n/a | F,P |

---

## Storage recommendation: relational vs chunk/vector metadata

The goal is to keep entity truth in one place, and to copy onto chunks only what the retriever must filter on before
similarity search.

| Keep **normalised (relational)** | **Denormalise onto every chunk / vector entry** | Keep **only by reference** (chunk → doc_id) |
|---|---|---|
| Project, contract, contract-supplier line, package, notice, supplier, loan, country-indicator-year, evaluation, milestones | `doc_id`, `chunk_id`, `parent_section_id` | Full document metadata (abstract, keywords, authors, trust funds) |
| All entity links with `link_class`/`link_confidence` (link tables) | `project_ids` (array; M:N) | Supplier and contract details |
| Supplier name variants and candidate clusters | `country_iso2` (array; null/`REGIONAL` flag) | Financial time series |
| Document ↔ project M:N table | `region`, `sector_codes` (top-level only), `global_practice` | Evaluation ratings (join at answer time) |
| Document versions / supersession | `doc_type`, `doc_major_type`, `doc_language` | Notice / award text (structured extraction lives relationally) |
| Technical + provenance metadata (MANIFEST, parser runs, DQ results) | `doc_date`, `doc_disclosure_date` (as sortable dates / FY ints) | |
| | `section_path`, `section_type`, `page_start`, `page_end`, `page_label` | |
| | `evidence_scope`, `is_table`, `is_ocr`, `parser_version`, `superseded` (bool) | |

Rationale:
- **Cardinality:**
  - Chunk filter fields are low-to-medium cardinality (≈100 doc types, ≈200 countries, ≈10 regions, ≈20 GPs/sectors, ≈100
    languages). They suit vector-index pre-filters.
  - Supplier, contract and package identifiers are high-cardinality and many-to-many. Duplicating them onto chunks would
    bloat the index and go stale.
- **Filtering efficiency:**
  - The required filters (country, sector, document type, publication period) must be applied *inside* the vector search to
    avoid post-filter recall loss. Each one is therefore copied to the chunk.
  - Project IDs are arrays, because 8.25% of documents cover several projects.
- **Traceability:**
  - Every chunk carries `doc_id` + physical page span + `section_path` + `parser_version`, enough to rebuild a citation.
  - Relational provenance then resolves the canonical URL, the checksum and the extraction run.
- **Hierarchy:**
  - Each chunk is linked to its parent section and section path, not only to the document.
  - This enables section-level retrieval and child-to-parent context expansion.
  - Sections are stored relationally (`document_section` table) and referenced by ID from chunks.
- **Staleness:**
  - Project status, ratings and closing dates change. They stay relational and are joined at answer time, not frozen into
    chunk metadata.
  - Only document-intrinsic attributes, plus slowly changing project attributes (country, sector), go on chunks.
  - Those chunk attributes are refreshed when the document-project link changes.

---

## Alignment update (2026-10-21)

Additional metadata required by the technical assessment, with measured availability
([`profiling/assessment_gap_probes.json`](../profiling/assessment_gap_probes.json)):

| field_name | description | data_type | source | availability | derivation_rule | quality_status | intended_usage |
|---|---|---|---|---|---|---|---|
| development_objective | project development objective text | text | v3 `pdo` | P (57.6%; 95.7% FY2020+ cluster) | — | fair | F (semantic), D |
| es_category_legacy | legacy environmental assessment category (A/B/C/F/…) | enum | v3 `envassesmentcategorycode` | P (55.6%) | — | fair | F, ML |
| es_risk_rating_esf | ESF overall E&S risk (L/M/S/H) | enum | v3 `esrc_ovrl_risk_rate` | P (51.5% of cluster) | add to population pull | fair | F, ML |
| cofinancier_types | co-financing structure | array<enum> | v3 `financers.projectfinancialtype` | P (64.5%; 26.0% with external co-financiers) | — | fair | F, ML |
| major_sector_codes | structured sector codes + % | array<struct> | v3 `major_sectors` | P (82.9%) | **do not split `major_sector_name`** | good | F, J |
| global_practice_canonical | GP mapped to a canonical list | enum | DS00005, IEG | D-U | curated map (D-21) | poor→fair | F, J |
| evaluation_type | ISR / ICR / ICRR / PPAR / PCR / EVM | enum | v3 ratings, DS00053, WDS doctype | P | — | good | F, conflict resolution |
| evaluation_date | date of the rating / evaluation | date | ISR `Archived on`, `evaldate`, `Evaluation FY` | P / D-R | ISR parser | fair | conflict resolution |
| superseded_by_evaluation | later evaluation of the same project | id | platform | D-R | precedence policy (D-18) | n/a | F, P |
| isr_seq_no | ISR sequence number | int | ISR text | D-R (25/27 parsed) | `wbdisc/isr.py` | fair | J (de-duplication), P |
| fiduciary_risk_rating | SORT fiduciary risk per ISR | enum | ISR text | D-R | `wbdisc/isr.py` | fair | ML (proxy), F |
| market_approach | Open / Limited / Direct × National / International | enum | procurement-plan tables | D-R (99.9% of parsed activities) | header merge + multilingual map | fair | F, ML |
| estimated_amount_usd | planned package amount | decimal | procurement-plan tables | D-R (89.3%) | strip injected spaces | fair | ML (planning-time feature) |
| plan_activity_status | normalised process status | enum | procurement-plan tables | D-R (100%; 2.7% unclassified) | `normalize_plan_status()` | fair | ML label (candidate) |
| evidence_scope | adds `sector` and `country` values for CPF / SCD / thematic evaluations | enum | platform | D-R | — | n/a | F, C |

**Storage note:** the evaluation and precedence fields (`evaluation_type`, `evaluation_date`, `superseded_by_evaluation`)
belong in relational tables. Chunks carry only `evaluation_type` and `doc_date` as filters. The current precedence-resolved
value is joined at answer time, so a later PPAR doesn't require re-indexing earlier chunks.
