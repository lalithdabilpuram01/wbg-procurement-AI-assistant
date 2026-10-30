# RAG & Document-Intelligence Feasibility

Run date 2026-10-20. Evidence:
- [`scripts/p03_document_parsing.py`](../scripts/p03_document_parsing.py) → [`profiling/document_parsing.json`](../profiling/document_parsing.json),
  with per-document heading outlines in [`profiling/document_outlines/`](../profiling/document_outlines/)
- procurement-plan table study ([`p05`](../scripts/p05_procurement_plan_tables.py))
- WDS metadata samples (v3 type sample n = 1,200; cluster n = 22,337)

**No embeddings, indexes or agents were built.**

## 1. Corpus availability (v3 WDS facet counts)

| Document type | Count | Cluster coverage (276 FY2020+ projects) | Relevance |
|---|---:|---:|---|
| Procurement Plan | 143,335 | 100% | Planned packages, methods, estimated vs actual amounts, planned / actual stage dates |
| Implementation Status & Results Report (ISR) | 59,693 | 98.9% | Ratings incl. Procurement, implementation narrative |
| Project Paper (AF / restructuring) | 13,163 | 81.5% | Changes, extensions, reallocations |
| Implementation Completion & Results Report (ICR) | 8,617 | 41.7% | Lessons, key factors, fiduciary / procurement findings |
| ICR Review (IEG) | 7,847 | 33.3% | Independent validation, lessons |
| Project Appraisal Document (PAD) | 7,000 | 78.3% | Procurement arrangements, risk ratings, implementation arrangements |
| PPAR (IEG) | 2,648 | 0% (older projects) | Deep evaluation, lessons |
| Note on Cancelled Operation | 191 | — | Failure narratives |

Procurement *guidance* (Procurement Regulations, Standard Procurement Documents) is published on worldbank.org procurement
pages, not as project documents in WDS. It was **not retrieved** in Phase 1.

## 2. Sample and parsing observations (22 PDFs + WB text renditions; plus 38 procurement plans)

| File | Type | Pages | Producer | Scanned pages | Tables (≤60 p) | Numbered headings | `.txt` form-feeds | "Procurement" heading pp. | "Lessons" heading pp. |
|---|---|---:|---|---:|---:|---:|---:|---|---|
| P122319_PAD | PAD | 44 | PDF PT 3.40p | 0 | 14 | 30 | 44 | 21, 29 | 16 |
| P131049_PAD | PAD | 94 | PDF PT 3.40p | 2 | 0 | 248 | **0** | 29, 50, 56 | 5, 20 |
| P145502_PAD | PAD | 77 | PDF PT 3.40p | 1 | 55 | 74 | 77 | 32, 46, 48, 49 | 5, 25, 72 |
| P515690_PADNEW (2026) | PAD | 44 | Aspose.PDF 25.12 | 0 | 45 | 114 | 44 | 20, 31 | 24 |
| P122319_ISR | ISR | 6 | PDF PT 4.7 | 0 | 8 | 0 | 6 | — | — |
| P131049_ISR | ISR | 12 | PDF PT 4.7 | 0 | 32 | 0 | 12 | — | — |
| P145502_ISR | ISR | 8 | PDF PT 4.7 | 0 | 20 | 0 | 8 | — | — |
| P122319_ICR | ICR | 53 | MS Word | 0 | 16 | 147 | 53 | 29, 50 | 4, 31 |
| P131049_ICR | ICR | 57 | MS Word | 0 | 36 | 84 | 57 | 26 | 5, 28 |
| P145502_ICR | ICR | 49 | MS Word | 0 | 110 | 60 | 49 | 20, 23, 25 | 4, 25 |
| P127203_ICR (French) | ICR | 87 | MS Word | 0 | 37 | 55 | 87 | — *(EN rules)* | — *(EN rules)* |
| P152820_ICR (Spanish) | ICR | 58 | MS Word | 0 | 22 | 122 | 58 | — *(EN rules)* | — *(EN rules)* |
| P122319_ICRR | ICRR | 20 | Aspose.Words | 0 | 2 | 15 | 20 | — | 17 |
| P131049_ICRR | ICRR | 15 | Aspose.Words | 0 | 2 | 20 | 15 | — | 14 |
| P145502_ICRR | ICRR | 16 | Aspose.Words | 0 | 3 | 14 | 16 | 14 | 15 |
| P003606+P046829_PPAR | PPAR | 54 | Adobe PDF Lib 4.0 | **15** | 0 | 21 | 54 | — | 33, 36 |
| P009662_PPAR | PPAR | 71 | PDF PT 4.7 | **10** | 0 | 44 | **0** | — | 21 |
| P049924+P124922_PPAR | PPAR | 49 | MS Word | 0 | 3 | 26 | 49 | — | 14, 39 |
| P039892_SAR (1990) | SAR | 114 | PDF PT 3.10p | 3 | 0 | 100 | 114 | 88–90 | 19 |
| P122319_PP / P131049_PP / P145502_PP | PP | 5 / 10 / 39 | Aspose.Pdf 17.2 | 0 | 12 / 16 / 40 | 2 / 3 / 1 | = pages | 1, 4 / 1, 6 / 1, 2, 29 | — |

Findings:

1. **Text layer.** All 22 PDFs have extractable text. Scanned pages are confined to old PPARs (15/54 and 10/71 pages), the
   1990 SAR (3/114) and isolated pages in two PADs (likely signature or map pages). Modern PAD / ISR / ICR / ICRR / PP
   files are born-digital. OCR quality was **not** tested.
2. **No structural bookmarks.** 0 of 22 PDFs have a PDF outline. Section hierarchy must come from:
   - layout: bold, or font size above the 11-pt body mode
   - numbering patterns (`I.`, `A.`, `1.`, `ANNEX 1`)
   - the dot-leader text TOC (e.g. ICR p. 4: `V. LESSONS AND RECOMMENDATIONS …`)
3. **Stable templates by type:**
   - ICRs share a common outline: I Context & PDO → II Outcome → III Key Factors → IV Bank Performance, Compliance
     (incl. *Fiduciary*, where procurement lives) → V Lessons → Annexes.
   - ICR Reviews and ISRs are system-generated (Aspose / PDF-tools) with fixed field labels.
   - Per-type templates are therefore more reliable than generic heading detection.
4. **Tables are pervasive.** Tables were found in 18/22 PDFs (up to 110 in the first 60 pages of one ICR), covering
   results frameworks, financing, ratings and procurement plans. Plain text extraction **flattens** them (§4).
5. **WB `.txt` renditions.**
   - `txturl` is populated for 100% of the v3 type sample, and all 22 requested `.txt` files downloaded successfully.
   - In 20/22 files they contain one form-feed per page, so text → page mapping is reconstructable.
   - Two files (a 94-page PAD and a PPAR) have no page breaks, so page citations must come from the PDF.
   - They run 1.07–2.93× longer than the PDF text layer, because of table padding and whitespace.
6. **Languages:**
   - 97.6% of the type sample is English.
   - The French and Spanish ICRs follow the same template, but English keyword rules miss their "Leçons" / "Lecciones"
     sections. Section typing must be multilingual.
7. **Multi-project documents:**
   - 8.25% of documents list several projects (e.g. PPAR `P003606,P046829`).
   - PPARs often cover several related projects, so evidence must be attributable to each.
8. **Disclosure watermark.** Page 1 of all 22 PDFs (and only page 1) carries a "Public Disclosure Authorized" watermark
   text layer, repeated up to 4 times. It is noise to strip before chunking.

## 3. Answers to the key questions

| Question | Answer | Evidence |
|---|---|---|
| Retrieve relevant historical projects using business metadata? | **Yes.** | Document `projectid` → v3 project → country / sector / region / approval FY / status; 100% project-ID join; IEG ratings joinable 100% |
| Narrow search by country, sector, document type, publication period? | **Yes, with normalisation.** | `docty` 100%, `docdt` 100% (filter on `docdt` verified via `strdate`/`enddate`), `count` 99.8% (name, not code; map via project), sector via project (`sectr` 99.5% on docs) |
| Cite the exact document page or section? | **Page: yes. Section: approximately.** | Physical page from the PDF in all 22 files and from `.txt` form-feeds in 20/22. Section path is heuristic (no bookmarks), and printed page labels differ from physical indices in front matter. |
| Distinguish project-wide lessons from contract-specific evidence? | **Yes, if enforced by metadata. The documents themselves rarely name contracts.** | 0 contract numbers and 0 borrower references in ICR / ICRR / ISR / PAD text of 5 projects with 1–32 contracts each ([`contract_reference_in_documents.json`](../profiling/contract_reference_in_documents.json)). Default `evidence_scope = project`; contract scope only when the reference appears in the text. |
| Comparable technical specifications available? | **Largely no** in WDS. | Bidding documents and technical specifications are not published as WDS project documents. Notices carry short descriptions; PADs describe components, not specs. **Specification retrieval is not supported** by the sources examined. |

## 4. Procurement plans: a structured source hidden in PDFs

- STEP plan exports (2017+) have per-category tables with 29 columns:
  - activity reference, loan, component, review type, method, market approach, procurement process
  - estimated and actual amount, process status
  - **Planned / Actual** dates for up to 9 stages
- pdfplumber recovered the grid in **38/40** sampled plans (1,137 activities).
- But the content is sparse:
  - *Signed Contract*: Actual populated 45.5%, Planned 97.8%
  - *Specific Procurement Notice*: Actual 23.0%
  - *Bid evaluation report*: Actual 6.9% (corrected 2026-10-21 from 6.7%)
- Header cells wrap and inject spaces (`Contract Complet ion`). **Corrected 2026-10-21:** this produced 123 header variants for
  71 logical columns. Phase 1 read only one variant per field. After merging
  ([`wbdisc/procurement_plan.py`](../wbdisc/procurement_plan.py)):
  - Process Status is 100% populated (cancelled 30.8%)
  - Market Approach 99.9%
  - Estimated Amount 89.3%
  - Procurement Process 45.4%
- Statuses are multilingual (Signed / Signé / Assinado / Firmado; Canceled / Annulé / Cancelado), and 7 rows were garbled by
  overlapping text.

**Recommendation:** treat procurement plans as **structured data extraction** into relational tables, with provenance. Do
not chunk them for semantic search, which would destroy the planned-vs-actual column semantics.

## 5. Recommended parsing and chunking approach (for Phase 2 design; not implemented)

| Concern | Recommendation | Why (observed) |
|---|---|---|
| Parser | Layout-aware PDF parsing (PyMuPDF / pdfplumber-class; evaluate Databricks-native options against this sample) as primary; WB `.txt` as fallback for text and pagination cross-check | Text layers are good; tables and headings need layout |
| OCR | Only for pages with < 80 chars and an image; flag `is_ocr` | Scanned pages are rare and concentrated in pre-2010 PPARs / SARs |
| Section detection | Per-document-type templates (ICR, ICRR, PAD, ISR, PPAR), seeded by the in-document TOC and numbering, with layout fallback; multilingual heading lexicon (EN / FR / ES / PT first) | No bookmarks; stable templates; FR / ES ICRs |
| Chunking | **Hierarchical:** document → section → paragraph-window chunks (≈300–800 tokens) that never cross section boundaries; keep parent-section IDs for context expansion | Lessons and procurement findings sit in specific sections |
| Tables | Extract as structured tables (rows / cols + caption + page). Index a linearised "table summary" chunk linked to the structured table, not raw flattened text. | Flattened tables lose meaning; ICR / PAD / ISR are table-heavy |
| Procurement plans, ISR rating blocks, award notices | Structured extraction to relational tables; not semantic chunks | Fixed templates; planned/actual and rating semantics |
| Page citations | Store physical page start/end for every chunk, plus the printed page label where detectable | Front matter offsets; 2/22 `.txt` without page breaks |
| Noise removal | Strip the page-1 disclosure watermark, running headers/footers, dot-leader TOC lines (keep the TOC separately for hierarchy) | Watermark on p. 1 of 22/22; TOC lines in ICR / PAD |
| Multi-project docs | Chunk once; attach the `project_ids` array | 8.25% of documents |
| Versions / duplicates | Group by (project, doc type, docdt, normalised title); keep all versions with a `superseded` flag; prefer `versiontyp = Final` | 1,783 near-duplicate records in the cluster |
| Evidence scope | `evidence_scope` ∈ {project, package, contract, country}; set to `contract` only by exact reference-in-text match | §3 |

## 6. Metadata gaps for retrieval

- No section, page-label or table metadata from the source (all derived).
- `last_modified_date` is only 19.2% populated in v3; `no_of_pages` is absent in v3.
- Document country is a name (`count`). Use the project's country codes for filters.
- Regional / multi-country projects need a `REGIONAL` country value.
- Section typing ("lessons", "procurement", "risk") is heuristic and English-biased today.
- There is no public link between evaluation records (DS00053) and their ICRR / PPAR document IDs. Match on project + doc
  type + date (candidate).
- Bidding documents and technical specifications are absent, which limits "Tender Design & Packaging" use cases. Notices
  (descriptions), procurement plans (packaging and methods) and PAD procurement sections are the available substitutes.

## 7. Preliminary feasibility verdict

| Capability | Verdict |
|---|---|
| Lessons-learned retrieval (ICR / ICRR / PPAR) by country / sector / period | **Feasible** (English first; multilingual needs work) |
| Page-level citations | **Feasible** |
| Section-level citations | **Feasible with heuristics**; needs a validation set |
| Procurement-arrangement retrieval (PAD procurement sections, plans) | **Feasible**; plans via structured extraction |
| Contract-specific evidence from evaluations | **Generally not supported**; only by explicit reference match |
| Technical-specification comparison | **Not supported** by the sources examined |

---

## Alignment update (2026-10-21)

Added after the technical assessment became available (see
[`docs/requirements/ASSESSMENT_ALIGNMENT_REPORT.md`](../../docs/requirements/ASSESSMENT_ALIGNMENT_REPORT.md)).
Evidence: [`profiling/assessment_gap_probes.json`](../profiling/assessment_gap_probes.json).

**Long-form documents (assessment Module 1, "100+ pages").** Page counts from v2 `no_of_pages`, type sample n = 1,198:

| Type | Median pages | p90 | Max | ≥ 100 pages |
|---|---:|---:|---:|---:|
| PAD | 98 | 140 | 203 | **46.0%** |
| PPAR | 66.5 | 112 | 182 | 16.0% |
| ICR | 59 | 91 | 129 | 3.3% |
| Procurement Plan | 9 | 16 | 154 | 0.7% |
| ICR Review | 11 | 17 | 22 | 0% |
| ISR | 5 | 8 | 12 | 0% |

Phase 1 parsed only 3 documents of ≥ 94 pages. A pilot on ≥ 50 long PADs is required before the chunking design is fixed.
Embedding strategy was not assessed in either phase.

**ISR documents:**
- **Three text layouts** were observed:
  - (A) column-aligned, until about mid-2024
  - (B) glyph-delimited, with "Last Approved / Proposed" risk columns and printed "Page n of m" labels
  - (C) vertically flattened values (2 of 27 renditions), which can't be parsed from text
- Rating values are prefixed by a private-use glyph (U+F06C).
- **Component ratings (Procurement, FM, M&E) are absent** from public ISRs.
- ISR series contain **duplicate re-uploads** (one sequence number up to 8 times).
- Parser: [`wbdisc/isr.py`](../wbdisc/isr.py) (tested). For ISRs, PDF-table parsing is recommended over `.txt`.

**Tender-design substitutes (assessment §2, Module 2).** Technical specifications remain unavailable (mentioned in 8.9% of
REOI and 7.2% of IFB notice texts). Available substitutes:
- REOI text with ToR content (71.7%) and qualification / experience criteria (83.0%; median 4,450 chars)
- IFB qualification criteria (57.4%)
- award-notice rejection reasons ("Non Responsive", "Does not meet Eligibility Requirements", "Failure to meet minimum
  technical score")
- procurement-plan packaging (method, market approach, estimated amount)
- the **Procurement Regulations for IPF Borrowers** (4th ed., Nov 2020, 142 pp., text layer), now retrieved

**Additional repositories:**
- OKR is reachable through a DSpace 7 REST API, and 5 of 5 sampled items map to WDS documents by GUID (overlap, not a new
  corpus).
- WDS v3 contains CPF 864, SCD 282, IEG Evaluation 1,019 and Aide Memoire 2,815 documents.
- CPF / SCD / IEG Evaluation documents have **no project link** (0% of cluster projects). They need `evidence_scope =
  country` or `sector` and country / sector filters, not project joins.
- **No Word documents** were found in any examined source.

**Conflicting evidence (assessment Module 3).**
- Retrieval must return *all* evaluations of a project, with type and date, because:
  - the latest ISR and the IEG outcome disagree on direction for 14.4% of 4,928 projects
  - the two official IEG sources differ for 793 projects (782 being a later PPAR superseding an ICR Review)
- The answer layer then applies the approved precedence policy (D-18).
