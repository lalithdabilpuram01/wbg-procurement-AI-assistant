# Module 1: unstructured document ingestion and knowledge representation (discovery and design)

- **Date:** 2026-10-24
- **Status:** read-only discovery and design. **No PDFs downloaded, no Databricks objects created or changed, no embeddings or indexes built, no 500-project subset created.** The 2,000-project cohort is unchanged.
- **Evidence:** `rag/module1_discovery/evidence/`. Scripts `m01`–`m08` are in the same folder.
  - Local analysis read only files already on disk.
  - API calls were metadata only: one facet request per cohort project, ID listings for the 53 projects with local PDFs, and 40 HTTP `HEAD` checks.
  - All network requests are logged in `discovery/evidence/access_log.jsonl`.

## Summary

1. **The local PDF corpus is too small for Module 1.**
   - 197 PDFs (6,313 pages, 199 MB) covering 53 projects, only **31 of them in the cohort**.
   - **177 are procurement plans**; 134 are version histories for just two projects.
   - Only 4 PADs, 3 ISRs, 5 ICRs, 3 ICR Reviews, 3 PPARs and 1 SAR exist locally.
2. **The official Documents & Reports API (`search.worldbank.org/api/v3/wds`) is the right scalable source.**
   - For all 2,000 cohort projects it lists **162,930 documents** (median 69 per project).
   - Every local PDF is in it.
   - Its PDF links are publicly accessible: 40/40 `HEAD` requests returned `200 application/pdf` with no authentication.
   - The projects.worldbank.org document pages are a JavaScript front end over the same API.
3. **500 projects is appropriate.**
   - **1,646** cohort projects have a PAD and at least one ISR, spread across every region.
   - **743** also have an ICR, but almost all are closed projects (735), which is a time and completion bias to manage.
4. **Main design choices:**
   - a template- and TOC-aware parser with page-level provenance;
   - section-bounded chunks of about 500 tokens;
   - tables as structured objects;
   - procurement plans as structured data, not RAG chunks;
   - a relational evidence model in Databricks (no separate knowledge graph);
   - a local hybrid (vector plus keyword) index with metadata pre-filtering.

## 1. Existing PDF inventory and coverage (`evidence/local_pdf_inventory.json`)

| Measure | Value |
|---|---|
| PDFs / pages / bytes | **197** / 6,313 / 199.2 MB |
| Folders | `discovery/project_deep_dive/raw/P163870/pp_pdf` (102 plan versions), `…/P178432/pp_pdf` (32), `discovery/samples/documents/files` (23), `discovery/samples/documents/pp_files` (40) |
| By type | Procurement Plan 177; PAD 4; ICR 4 + 1 Spanish (`ICRSP`); ISR 3; ICRR 3; PPAR 3; SAR 1; guidance 1 (Procurement Regulations for IPF Borrowers) |
| Projects | 53 unique; **31 in the 2,000 cohort**; 2 multi-project PPARs |
| Download metadata | 63 PDFs have a manifest entry (source URL, retrieval time, SHA-256; all checksums match). The 134 deep-dive plan PDFs are recorded in the deep-dive scripts' access log, not the sample manifest |
| Generation | Born-digital except: 6 files with likely-scanned pages (2 old PPARs 15/54 and 10/71; SAR 1990 3/114; PADs 2 and 1; the 4,184-page plan 18). Producers: Aspose (system-generated ISR, ICRR, plans), MS Word (ICRs), PDF-Tools (PADs) |
| Structure | **0/197 have PDF bookmarks**; no PDF page labels; page 1 carries the "Public Disclosure Authorized" watermark text (up to 4 times) |
| Language (stop-word guess) | en 112, fr 82 (mostly the P163870 plan series), es 2 |
| Existing scripts | `discovery/scripts/s04_documents.py` and `s04b_documents_v3.py` (WDS metadata and samples), `p03_document_parsing.py` (parsing study), `p05_procurement_plan_tables.py` (pdfplumber plan tables), `discovery/project_deep_dive/d01_fetch_two_projects.py`, `discovery/wbdisc/isr.py` and `procurement_plan.py` (tested parsers), `wbdisc/http.py` (logged client) |

**PDFs per project:** 48 projects have 1 PDF, 2 have 5, 1 has 6, one has 32 (P178432) and one has 102 (P163870). Coverage of the cohort is negligible (31/2,000 = 1.6%).

**Long documents:** 3 local files are ≥ 100 pages: the 1990 SAR (114), the IPF Procurement Regulations (142), and a **4,184-page, 101 MB procurement plan** (P163870, 2021-05-18).

## 2. Official World Bank document sources (API findings)

| Question | Finding |
|---|---|
| Endpoint and documentation | `https://search.worldbank.org/api/v3/wds` (documented at documents.worldbank.org/en/publication/documents-reports/api). Params: `format`, `qterm`, `fl`, `rows`, `os`, `fct`, `strdate`/`enddate`, `sort`/`order`, and field filters (`docty_exact`, `lang_exact`, `count_exact`, …; `^` = OR). No rate limit or max rows documented |
| Search by project | `projectid=<P>` works (verified). Facet counts per type: `rows=0&fct=docty_exact` returns counts nested under `documents.facets` (undocumented placement). Listing: `rows=500&os=n&fl=…` |
| Fields returned | `id`, `display_title`, `docty`, `majdocty`, `docdt`, `disclosure_date`, `datestored`, `last_modified_date` (19–26%), `lang`, `available_in`, `projectid`, `count`, `admreg`, `sectr`, `theme`, `repnb`, `guid`, `chronical_docm_id`, `entityids`, `versiontyp` (≈61%), `volnb` / `totvolnb`, `pdfurl` / `txturl` (99.6%), `url`, `trustfund`, `keywd`, `abstracts` (≈6%) and more. **No page count in v3** (v2 had `no_of_pages`) |
| Documents per project (2,000 cohort, `evidence/api_document_counts_by_project.jsonl`) | 162,930 total; median **69**, p10 27, p90 140, max 992; 0 projects with none |
| By type (documents / projects with ≥ 1) | Procurement Plan 83,333 / 1,999 (100%); ISR 21,615 / 1,971 (98.5%); Auditing Document 13,674 / 1,818; Project Paper 4,355 / 1,639 (82.0%); PID 2,818 / 1,497; ESRS 1,969 / 931; **PAD 1,805 / 1,651 (82.5%)**; **ICR 927 / 910 (45.5%)**; **ICRR 770 / 757 (37.9%)** |
| Public accessibility | 40 sampled `pdfurl` (`http://…`) and their `https://` forms: **40/40 HTTP 200 `application/pdf;charset=utf-8`**, no login. `http` takes 3 redirects and `https` 2, both ending at `https://documents1.worldbank.org/curated/en/<guid>/pdf/<slug>.pdf`. Store the final canonical URL. `HEAD` only; no bodies downloaded |
| Local vs API | All **197 local project–document pairs found** in the API listings of their 53 projects. Local files cover 1 of 55 documents (P131049) up to 102 of 164 (P163870, plans) |
| projects.worldbank.org | Project document pages are a single-page app whose HTML references `search.worldbank.org/api/v3/wds`. Same source; nothing extra to harvest |

**Duplicates, versions and volumes** (WDS v3 metadata for the 276 Phase 1 cluster projects, 22,337 records; `evidence/api_vs_local_and_access.json`):
- **Duplicates:** 1,171 groups share (project, type, date, title), with 1,786 extra records. 101 ISR sequence numbers appear more than once (re-uploads). 502 report numbers (`repnb`) and 9 GUIDs are shared by several records.
- **Versions:** `versiontyp` holds document *stages*, not revision numbers. Values: Final 10,718; Buff/Gray cover; Revised 140; Initial appraisal / concept; NULL 8,686.
- **Volumes:** 666 records are parts of multi-volume reports (`totvolnb` > 1), mainly Environmental Assessments 416, Resettlement Plans 183 and Indigenous Peoples Plans 44.

**Rule:** document family = (project, `docty`, `repnb` or normalised title). Parts = `volnb`/`totvolnb`. Prefer `Final`. Keep superseded versions flagged; never delete.

## 3. Representative PDFs for manual inspection

All paths are relative to `procurement-intelligence/`. Page numbers are physical pages.

| # | Project | Title (WDS) / type | Local path | Pages | Important sections (first page) and tables | Parsing challenges |
|---|---|---|---|---|---|---|
| 1 | P131049 (cohort) | *Mozambique – Climate Resilience: Transforming Hydro-Meteorological Services Project*, **PAD**, 2013-04-01, Buff cover | `discovery/samples/documents/files/P131049_PAD_17555469.pdf` | 94 | TOC p5–6; Strategic Context p11; PDO p16; Project Description p18; Key Risks p24; Appraisal Summary p25; Annexes 1–7 p32–72 (Annex 4 Implementation Arrangements incl. procurement p50; Operational Risk p69) | 31.9 MB (images); 2 scanned pages; `find_tables` found **0** tables though results-framework tables exist (no ruling lines); 255 noisy font-based heading candidates; WB `.txt` has no page breaks |
| 2 | P145502 (cohort) | *India – Technology Center Systems Program Project*, **PAD**, 2014-04-02 | `…/files/P145502_PAD_19368603.pdf` | 77 | TOC p4–5; Strategic Context p12; Key Risks p28; Appraisal Summary p29; Annex 1 Results Framework p36 … Annex 7 p77; 29 tables on 26 pages | PDO / Project Description headings not matched by the generic pattern (template variant); procurement mentions on p3, 11, 32, 33 need section disambiguation |
| 3 | P131049 | *… P131049 – Implementation Status and Results Report*, **ISR**, 2017-04-24 | `…/files/P131049_ISR_27391778.pdf` | 12 | Key Dates / PDO / Overall Ratings p1; Risks, Results p3; Disbursements p11; Restructuring History p12 | 8-pt system template; private-use glyph before ratings; running header "The World Bank"; component (procurement) ratings not public; three ISR layouts across years |
| 4 | P131049 | *Mozambique – Climate Resilience …*, **ICR** (ICR5203), 2020-06-15, Gray cover | `…/files/P131049_ICR_32160299.pdf` | 57 | TOC p5; Context & PDO p10; Outcome p13; Key Factors p23; Bank Performance p24; Procurement p26; **Lessons p28**; Annex 1 Results Framework p30 … Annex 6 p52; 34 tables | Section labels repeat in the data sheet and TOC; tables span pages; 4 watermark repeats on p1 |
| 5 | P131049 | *Mozambique – MZ-Hydro-Met PPCR*, **ICR Review (IEG)** ICRR0022203, 2021-02-02 | `…/files/P131049_ICRR_32800471.pdf` | 15 | Project Data p1; Relevance p4; Efficacy p5; Efficiency p8; Risk p10; Bank Performance / Lessons / Quality of ICR p14 | System-generated (Aspose) fixed labels; ratings in small tables; IEG precedence vs ICR |
| 6 | P145502 | *India – … Technology Center Systems Project – Procurement Plan* (STEP30706), 2020-03-03 | `…/files/P145502_PP_31837484.pdf` | 39 | General / Goods & Works p1; Selection of Consultants p4; STEP activity tables p29–39; 40 tables | 3-pt font; wrapped header cells (123 variants of 71 columns found earlier); planned vs actual columns. **Structured extraction, not chunking** |
| 7 | P127203 | **ICR (French)** | `…/files/P127203_ICRFR_33407968.pdf` | 87 | TOC p6; Contexte du projet p13; Performance de la Banque p52; Passation des marchés p51; Annexe 1 p58; 42 tables | English section rules fail ("Enseignements" etc.); bilingual lexicon needed; no WDS record cached locally |
| 8 | P039892 | **Staff Appraisal Report (1990)** | `…/files/P039892_SAR1990_696824.pdf` | 114 | TOC p3–4; I Country and Sector Background p6; Procurement p27–30; Financial Analysis p35; 18 annex references | 3 scanned pages (OCR); pre-template structure; in-text "Annex 9)" references create false annex starts; 0 tables detected |
| 9 (stress case) | P163870 (cohort) | **Procurement Plan**, 2021-05-18 (doc 33215505) | `discovery/project_deep_dive/raw/P163870/pp_pdf/2021-05-18_PP_33215505.pdf` | **4,184** | Sections I General p1, II Goods/Works p1, III Consultants p2, II again p2,097; 24 tables in 15 sampled pages | 101 MB; 18 scanned pages; needs streaming page-batch extraction with memory limits; profile on samples only |

## 4. Metadata architecture

### 4.1 Principles

- **Every field carries provenance:** `source_api`, `pdf_metadata`, `parsed`, `derived_rule`, `inferred_model` or `manual`.
- **Missing vs unknown:** value NULL plus `<field>_status`:
  - `present`;
  - `not_published` (source has no value);
  - `not_extracted` (not attempted or failed: unknown);
  - `not_applicable`.
- **Dates kept apart:**
  - `document_date` (`docdt`)
  - `disclosure_date`
  - `stored_in_repository_at` (`datestored`)
  - `source_last_modified_at`
  - `pdf_creation_date` (file metadata)
  - `retrieved_at` (download)
  - `ingested_at` (pipeline)
  - `valid_from` / `valid_to` for evidence (a rating holds until superseded)
- **Relationships:**
  - `verified` = source-asserted (WDS `projectid`) or exact identifier match in text.
  - `candidate` = inferred, with score and evidence.
  - Candidates are never presented as facts.

### 4.2 Schema by level

| Level | Fields (source) |
|---|---|
| **Project** (from Gold #1, unchanged) | `project_id`, `project_name`, `region_name`, `geography_type`, `wdi_country_code` (NULL regional), `major_sector` (Silver `projects`), `approval_fiscal_year`, `board_approval_date`, `project_status_snapshot` |
| **Document** | `document_id` (WDS `id`); `document_family_key`; `version_rank`; `superseded_by_document_id`; `project_ids` (array, `projectid`); `title` (`display_title`, whitespace-normalised); `document_type` (`docty`) and `major_type` (`majdocty`); `report_number` (`repnb`); `guid`, `chronical_docm_id`; `volume_no` / `total_volumes`; `stage` (`versiontyp`); `language` (`lang`, plus detected language); `country_published` (`count`); `document_date`, `disclosure_date`, `stored_at`, `source_last_modified_at`; `pdf_url_canonical` (final redirect), `txt_url`; `file_sha256`, `bytes`, `page_count`; `pdf_producer`, `pdf_creation_date`; `scanned_page_count`; `retrieved_at`, `ingested_at`; `source_api_record_sha256` |
| **Page** | `document_id`, `page_number` (physical), `printed_page_label` (D-U), `text_chars`, `is_ocr`, `ocr_confidence`, `has_tables` |
| **Section** | `section_id`; `document_id`; `parent_section_id`; `section_path` (e.g. `IV > B > Procurement`); `heading_text`; `heading_level`; `section_type` (narrative / table / annex / data_sheet / toc / results_framework / ratings); `semantic_tag` (lessons / procurement / risk / implementation / outcome …, multilingual); `start_page` / `end_page`; `detection_method` (template / toc / layout); `detection_confidence` |
| **Table** | `table_id`, `document_id`, `section_id`, `page_range`, `caption`, `n_rows`, `n_cols`, `header_cells`, `cells_json`, `extraction_tool`, `table_summary_text` |
| **Chunk** | `chunk_id`; `document_id`; `section_id`; `table_id` (if a table summary); `page_start` / `page_end`; `chunk_index`; `token_count` (cl100k); `text`; `text_sha256`; `chunking_strategy_version`; `embedding_model`; `embedding_model_version`; `embedding_dim`; `embedded_at`; `is_latest_version`; `language` |
| **Evidence link** | `link_id`; `subject_type/id` (chunk / section / document); `object_type/id` (project / contract / notice / financing_number); `relationship_type` (`document_of_project`, `mentions_contract_number`, `mentions_borrower_reference`, `mentions_notice_reference`, `semantic_candidate`); `evidence_text_span` and page; `match_method`; `confidence`; `verification_status` (verified / candidate / rejected / needs_review); `valid_from` / `valid_to`; `created_by_version` |

### 4.3 Sample metadata record (real PDF: P131049 ICR)

- **Source values** come from the cached WDS v3 record and the local file.
- **Derived values** are labelled, and the values that can't yet be known are set to null, each with a status.

```json
{
  "document": {
    "document_id": "32160299",
    "project_ids": ["P131049"],
    "title": "Mozambique - Climate Resilience : Transforming Hydro-Meteorological Service Project",
    "document_type": "Implementation Completion and Results Report",
    "major_type": "Project Documents",
    "report_number": "ICR5203",
    "guid": "367671592860765715",
    "chronical_docm_id": "090224b087ab06dd",
    "volume_no": 1, "total_volumes": 1,
    "stage": "Gray cover",
    "language": "English",
    "country_published": "Mozambique",
    "region_published": "Eastern and Southern Africa",
    "document_date": "2020-06-15",
    "disclosure_date": "2020-06-22T21:17:58Z",
    "stored_at": "2020-06-22T04:00:00Z",
    "source_last_modified_at": null, "source_last_modified_at_status": "not_published",
    "pdf_url_source": "http://documents.worldbank.org/curated/en/367671592860765715/pdf/Mozambique-Climate-Resilience-Transforming-Hydro-Meteorological-Service-Project.pdf",
    "txt_url_source": "http://documents.worldbank.org/curated/en/367671592860765715/text/Mozambique-Climate-Resilience-Transforming-Hydro-Meteorological-Service-Project.txt",
    "file_sha256": "8a04dd0d0056e26664fc35e84b4dba3c91fef5a3b0808bb8dfc6045d59cdef5b",
    "bytes": 1998805,
    "page_count": 57, "page_count_provenance": "pdf_metadata",
    "pdf_producer": "Microsoft® Word for Office 365 / PDF PT 4.7.24.2 (pdf-tools.com)",
    "pdf_creation_date": "D:20200618122234-04'00'",
    "scanned_page_count": 0, "has_outline": false,
    "retrieved_at": "2026-10-21T01:30:43+00:00",
    "ingested_at": null, "ingested_at_status": "not_extracted",
    "document_family_key": null, "version_rank": null, "_derivation_status": "to be derived at ingestion"
  },
  "section_example": {
    "section_id": null, "document_id": "32160299",
    "heading_text": "LESSONS AND RECOMMENDATIONS", "section_path": "V > Lessons and Recommendations",
    "section_type": "narrative", "semantic_tag": "lessons",
    "start_page": 28, "end_page": null, "end_page_status": "not_extracted",
    "detection_method": "template_label_search (m04)", "detection_confidence": null
  },
  "evidence_link_example": {
    "subject_type": "document", "subject_id": "32160299",
    "object_type": "project", "object_id": "P131049",
    "relationship_type": "document_of_project",
    "match_method": "source_api.projectid", "verification_status": "verified",
    "evidence_text_span": null, "confidence": 1.0
  }
}
```

Region, country, sector and approval FY for filtering come from the **project** record (Gold #1), not from the document's free-text `count` / `sectr`.

### 4.4 Metadata life cycle

1. **Extract:** WDS API record (raw JSON kept in Bronze) → PDF file metadata and checksum → parser outputs (pages, sections, tables, chunks), each with provenance.
2. **Validate:** blocking checks on:
   - unique `document_id`;
   - `file_sha256` equal to the stored download;
   - `page_count` equal to the parser's;
   - every chunk page inside the document's range;
   - every chunk inside one section;
   - projects in the cohort;
   - dates parseable, with document date ≤ retrieval date;
   - duplicate families resolved.

   Also reported, but not blocking: missing sections per template, OCR page share, non-English share.
3. **Store:** Databricks remains the governed source of truth.
   - Bronze: API records and the PDF manifest.
   - Volume: PDFs.
   - Silver: documents, document_projects, pages, sections, tables, chunks, evidence_links.
4. **Index:** the vector and keyword index is a derived local copy keyed by `chunk_id`, holding only the filter fields: `project_id(s)`, `wdi_country_code`, `region_name`, `major_sector`, `document_type`, `document_date`, `language`, `is_latest_version`, `section_type`, `semantic_tag`.
5. **Filter retrieval:** apply metadata pre-filters (project / country / region / sector / type / date range / latest version) *before* vector and keyword search.
6. **Pass to the LLM:** each retrieved chunk is wrapped in a header: `[chunk_id | title | type | document_date | section_path | pp. x–y | project_id]`. The model must cite `chunk_id`s and may not use unlisted sources.
7. **Display citations:**
   - Show document title, type, date, section path and page range.
   - Link to `pdf_url_canonical#page=x`.
   - A citation is shown only if its `chunk_id` was retrieved and any quoted span is found verbatim in the chunk text; otherwise it is flagged "unverified".

## 5. PDF parsing and chunking architecture

### 5.1 Tools compared (local evidence)

| Tool | Strengths | Weaknesses | Role |
|---|---|---|---|
| **PyMuPDF** 1.26.7 (installed) | Fast; per-span font, size and bold; page geometry; `find_tables`; image detection for OCR triage; handles the 4,184-page file page by page | AGPL licence (check for production); heuristic tables (found 0 in PAD #1) | **Primary:** text, layout, page provenance, OCR triage |
| **pdfplumber** 0.11.9 (installed) | Precise ruled-grid tables (38/40 procurement plans earlier) | Slow on long files | **Tables** on table-candidate pages, and procurement plans |
| **Docling** (not installed) | Layout and table models, reading order, structured export | Heavy (ML models), slower; untested here | **Challenger:** evaluate on 20 long PADs in the pilot (needs approval to install) |
| `unstructured` (installed) | Generic partitioning | Less control over provenance | Not recommended as primary |
| WB `.txt` renditions | Clean text; form-feeds per page in 20/22 | 2/22 lack page breaks; tables padded | Cross-check and fallback only |
| OCR (Tesseract not installed; Docling/EasyOCR alternative) | Needed for scanned pages | Rare in modern docs | Only pages with < 80 chars and an image; flag `is_ocr` |

### 5.2 Pipeline (design; not implemented)

1. **Ingest:**
   - read the API record;
   - download the PDF (after approval);
   - compute the SHA-256;
   - skip exact duplicates;
   - group into document families and volumes.
2. **Pages:** stream page by page and record text, geometry and image presence. OCR only the triaged pages. Strip the page-1 disclosure watermark and repeated running headers and footers (e.g. "The World Bank", "Independent Evaluation Group (IEG)", "La Banque mondiale").
3. **Sections:**
   1. a **per-type template** (PAD, ISR, ICR, ICRR, Project Paper) with a multilingual label lexicon (EN / FR / ES / PT);
   2. anchors from the in-document **dot-leader TOC** (found in PADs, ICRs and the SAR) and annex headings;
   3. a layout fallback (bold or larger-than-body numbered lines).

   Labels found in the TOC or data-sheet pages are ignored as anchors (`m04` shows labels such as "Procurement" recurring on p3 and p9). Each section records its method and confidence.
4. **Tables:** extract on candidate pages as cells, caption and page range, attached to their section. Index a short linearised **table summary chunk** linked to `table_id`; the full grid stays queryable. Procurement plans and ISR rating blocks go to **relational extraction** (existing `wbdisc` parsers), not semantic chunks.
5. **Annexes:** separate top-level sections (`section_type = annex`) with their own titles. Results-framework annexes are table-dominant.
6. **Chunks:** assemble paragraphs within one section. Measured paragraph blocks are small (p50 20–44 tokens, p90 126–337) and pages run 355–731 tokens at the median.
   - **Target 500 tokens (min 120, max 800)**, never crossing a section.
   - **Overlap: one trailing paragraph (≈ 10–15%).**
   - Each chunk keeps its `section_path`, so a parent section can be pulled in for context.
7. **Long documents (≥ 100 pages):** page-streaming with bounded memory and checkpointing per document. The 4,184-page plan goes through the procurement-plan path in page batches.
8. **Duplicates:**
   - file SHA-256 (exact);
   - normalised-text SimHash or MinHash per document (re-uploads, ISR repeats);
   - chunk `text_sha256` to drop boilerplate repeated across documents;
   - all versions kept, with `is_latest_version`.

## 6. Knowledge representation and entity linking

**Model:** Project → Document (M:N through `document_projects`) → Section (tree) → Chunk / Table, plus a typed **`evidence_links`** table connecting sections, chunks and documents to projects, contracts (`wb_contract_number`), notices (`notice_op_id`) and financing instruments.

| Link | Method | Status |
|---|---|---|
| Document → project | WDS `projectid` (source-asserted) | verified |
| Chunk → contract | Exact match of `wb_contract_number`, the normalised borrower reference or a notice reference in the chunk text, within the same project | verified only if exact; an earlier study found **0** contract numbers in ICR / ICRR / ISR / PAD text of 5 projects, so expect few |
| Chunk → notice | Exact `notice_op_id` / bid reference match | verified if exact |
| Section → contract / notice by meaning | Same project, plus category / method / description similarity, plus date window | **candidate** only (score and evidence); never shown as fact |
| Project → contracts / notices | Existing Gold tables (`project_id`) | verified (structured) |

**Edge cases:**
- **Several documents per project:** all kept, ordered by date and type; retrieval can be filtered to the latest version or a date range.
- **Changes over time:** `valid_from` / `valid_to` on evaluative evidence (each ISR rating holds until the next ISR; an ICR closes the series).
- **Conflicting ISR vs ICR / ICRR evidence:** retrieve all of them with type and date. The answer layer shows both, ordered by precedence: IEG ICRR > ICR > latest ISR (precedence policy D-18, pending).
- **Uncertain entities:** candidates stay in a review queue (`needs_review`) with their evidence.
- **Attribution:** every link stores `match_method`, `evidence_text_span` and page.

**Knowledge graph?** **Relational tables are sufficient for this PoC.**
- The relationships are few and typed, and contract-level text links are rare.
- Joins to the Gold tables are direct.
- The `evidence_links` table *is* an edge list, and a graph view (e.g. NetworkX) can be generated from it if the demo needs one.
- A dedicated graph database adds infrastructure without a material retrieval gain at this scale.

## 7. Embeddings and RAG design

| Component | Recommendation | Rationale and constraints |
|---|---|---|
| Embedding model | **`Alibaba-NLP/gte-base-en-v1.5`** (768-dim, 8,192-token context; already cached locally) for the pilot | English dominates (95.4% of cluster documents); runs locally on MPS. Evaluate a multilingual model (e.g. `multilingual-e5-base` or `bge-m3`, not cached; needs approval to download) for the FR / ES / PT minority. External embedding APIs would send text to a third party: a decision, though these documents are public |
| Vector store | **PostgreSQL 16 + pgvector** (local server already running; **pgvector not installed**). Alternative with no install: **LanceDB** 0.30 (installed) | Postgres gives metadata filters, `pg_trgm` / full-text keyword search, evidence joins and citations in one place. LanceDB is embedded and has vector plus full-text search, but its evidence tables would be separate. Databricks Free Edition vector search was **not verified** and is not relied on |
| Hybrid retrieval | BM25-style keyword search (Postgres `tsvector`, or LanceDB FTS) plus vector top-k, fused with **Reciprocal Rank Fusion**, after metadata pre-filtering | Exact terms (method names, report numbers, places) need keyword search |
| Reranking | Local cross-encoder **`cross-encoder/ms-marco-MiniLM-L-6-v2`** (cached) on the top 50, then the top 8–10 go to the LLM; evaluate `bge-reranker-base` | Fast on CPU / MPS |
| Version filtering | Default `is_latest_version = true` within document families; time-scoped questions use `document_date` ranges; evaluations return all of them with dates | Avoids stale or duplicate ISRs |
| Citation verification | Cited `chunk_id`s must be in the retrieved set; quoted spans must match verbatim; page ranges come from chunk metadata | §4.4 step 7 |
| Evaluation | A gold set from the pilot (D-23): 60–100 questions across PAD, ISR, ICR and ICRR, with labelled relevant chunks. Metrics: Recall@k, MRR, nDCG@10, citation precision, answer faithfulness, plus section-detection accuracy, table-extraction accuracy and OCR error on a labelled page sample | Assessment Module 4 (D-23 pending) |

**Scale estimate (pages from per-type medians, not measured):**

| Scope | Narrative pages | Approx. tokens | Chunks (≈ 500 tokens) | Embedding time (local) |
|---|---|---|---|---|
| Pilot (43 projects, ISRs capped at 4) | ≈ 7,100 | ≈ 4–5 M | ≈ 9 k | under 1 hour |
| 500 projects (ISRs capped at 4) | ≈ 87,700 | ≈ 55 M | ≈ 110 k | hours |

## 8. Selecting ~500 projects (method only; **not created**)

Document availability for all 2,000 cohort projects (`evidence/coverage_and_selection_design.json`):

| Tier | Rule | Projects |
|---|---|---|
| Core narrative | PAD and ≥ 1 ISR | **1,646** |
| Full life cycle | Core plus ICR | **743** (735 closed, 8 active) |
| Procurement evidence | ≥ 1 procurement plan | 1,999 |
| Eligible pool | Core plus procurement | **1,646** |

Coverage by stratum (projects / core-eligible / full life cycle):

| Region | Projects | Core-eligible | Full life cycle |
|---|---|---|---|
| East Asia & Pacific | 289 | 236 | 118 |
| Eastern & Southern Africa | 405 | 333 | 127 |
| Europe & Central Asia | 280 | 225 | 86 |
| Latin America & Caribbean | 246 | 210 | 99 |
| MENA, Afghanistan & Pakistan | 197 | 150 | 61 |
| South Asia | 184 | 162 | 90 |
| Western & Central Africa | 387 | 329 | 162 |
| Other | 12 | 1 | 0 |

Approval FY bands: ≤ 2014 225 / 213; 2015–17 334 / 291; 2018–20 451 / 206; 2021–23 396 / 31; 2024+ 240 / 2. Contract-volume quartiles: Q1 356; Q2 385; Q3 441; Q4 464 core-eligible.

**Is 500 appropriate?** Yes:
- It takes 30% of the eligible pool, leaving room for stratification in every region.
- A fully proportional 500 (simulated only, seeded) gives every main region 45–101 projects, 224 active / 276 closed, and all FY bands.
- The corpus (≈ 88k pages with capped ISRs) is manageable locally.

**Recommended method (reproducible):**
1. **Eligibility:** PAD, ≥ 1 ISR and ≥ 1 procurement plan.
2. **Strata:** region × status (and FY band).
3. **Allocation:** proportional, with a floor of 20 per region.
4. **Seeded sampling:** seed recorded, list versioned.
5. **ICR quota:** among closed projects, a quota for ICR availability, so lessons-learned questions are answerable without making the whole set completed and evaluated projects.
6. **Document cap:** latest PAD (+ AF / restructuring Project Papers), ISRs capped at the first, middle and last two, ICR, ICRR; procurement plans to structured extraction.

**Selection bias to disclose:**
- Requiring a PAD and an ISR excludes 17.7% of projects (a PAD alone excludes 17.5%), including 11 of the 12 "Other"-region projects.
- Requiring an ICR would bias towards **closed, older projects**: only 73 projects approved FY2021+ have an ICR (33 with PAD + ISR + ICR), against 578 approved FY ≤ 2017.
- ICRRs add IEG's selection bias (no ratings for recent approvals).
- Core-eligibility is lower for low contract volume (Q1 71% vs Q4 92%).
- The cohort itself is a stratified sample, not the WB population.

**Pilot (25–50 projects):** a **proposed** list of 43 projects (seeded; region × status, with up to 2 ICR projects per closed stratum) is in `evidence/coverage_and_selection_design.json → proposed_pilot`.
- All 8 regions are covered: 6 each, plus 1 "Other".
- 21 closed / 22 active.
- FY ≤ 2014: 4; 2015–17: 5; 2018–20: 18; 2021–23: 11; 2024+: 5.
- Contract quartiles: Q1 7, Q2 8, Q3 11, Q4 17.
- 14 have an ICR.

It is a candidate list for review only; nothing was created. Before approval, add the two local-PDF projects (P131049, P145502) as parser controls, plus at least 3 projects with French or Spanish documents.

## 9. Validation and evaluation strategy

| Stage | Checks |
|---|---|
| Discovery and download | Every downloaded PDF's SHA-256 recorded; API record kept; HTTP status logged; canonical URL stored; counts equal the planned list |
| Parsing | Page count equals the PDF; text chars per page; OCR pages flagged; template sections found per type (expected-section coverage); a manual check of section boundaries on 30 documents (target ≥ 90% correct start page); table extraction checked on 20 tables |
| Chunking | No chunk crosses a section; token bounds; page ranges valid; deduplication counts |
| Linking | Verified links only from source IDs or exact references; candidate precision on a labelled sample; 0 links outside the project |
| Retrieval | D-23 gold set: Recall@10, MRR, nDCG@10 for vector, keyword and hybrid; citation precision; faithfulness |
| Governance | No Databricks change outside the approved new objects; Bronze, Silver and Gold baselines unchanged (existing `gold/common/baseline.py`) |

## 10. Assessment alignment (Module 1)

| Requirement | Covered by | Gap |
|---|---|---|
| Structured + unstructured ingestion | Existing 17 / 18 / 5 tables + KPI views; WDS API → Bronze → Silver document tables (§4.4) | Ingestion not built |
| Entity resolution | Project IDs source-asserted; contracts and notices by exact reference; candidates with evidence | Contract-level references are rare in reports; supplier entities in documents are not resolved (D-14b limits supplier display) |
| Linking contracts / suppliers to document evidence | `evidence_links` (verified / candidate) | Expect mostly project-level evidence; supplier-level evidence largely unavailable in public reports |
| Long-document parsing (100+ pages) | Streaming parser; 28/134 cluster PADs ≥ 100 pages; local 114 / 142 / 4,184-page cases | Pilot must include ≥ 10 PADs ≥ 100 pages |
| Section-aware chunking | Template + TOC + layout; section-bounded chunks | Multilingual templates to build; accuracy unmeasured |
| Tables and annexes | Structured tables + summary chunks; annex sections; plans as relational data | Borderless tables (PAD #1) need Docling or another evaluation |
| Embeddings | gte-base-en-v1.5 local; multilingual option | Model and store decisions pending |
| Knowledge representation | Relational evidence model; optional graph view | — |

**Out of scope (deliberately):** technical-specification retrieval (not published in WDS), dashboards, ML, UI.

## 11. Decisions requiring approval

1. **Target size and method:** approve ~500 projects using the §8 method and bias statement (or change the strata or ICR quota).
2. **Pilot:** approve the proposed 43-project list (± parser controls and non-English additions) or ask for a different size.
3. **Downloads:** approve PDF downloads for the pilot only:
   - document types: PAD + Project Papers, ISRs capped at 4, ICR, ICRR;
   - procurement plans for structured extraction only;
   - estimated ≈ 400 PDFs and ≈ 7k narrative pages;
   - logged and checksummed.
4. **Storage location:** PDFs in a new Databricks Volume plus new Bronze and Silver document tables, or local only for the pilot.
5. **Parser stack:** PyMuPDF (note the AGPL licence) + pdfplumber; approve installing **Docling** as a challenger and an OCR engine (Tesseract via Homebrew, or Docling's OCR).
6. **Embedding model:** local `gte-base-en-v1.5` (cached) vs downloading a multilingual model vs an external API.
7. **Vector store:** install **pgvector** in the local PostgreSQL 16 (recommended), or use LanceDB (no install).
8. **Language scope:** English-only for the pilot, or include FR / ES / PT documents.
9. **Version and precedence policy:** latest-version default; D-18 precedence for conflicting evaluations.
10. **D-23:** approve building the retrieval gold set from the pilot (D-23 stays pending until approved).
