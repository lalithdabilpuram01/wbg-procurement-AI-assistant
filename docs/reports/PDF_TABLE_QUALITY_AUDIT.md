# PDF table-quality audit: 10-PDF pilot (Step 5), 2026-10-26

**Read-only audit.** No PDF, extraction output, embedding, manifest or database was changed. Nothing was uploaded,
downloaded, installed or sent to a model.

Audit code and outputs live in `audits/pdf_table_quality/`:

| File | Contents |
|---|---|
| `audit_tables.py` | The audit |
| `manual_verification.json` | The manual page-comparison verdicts |
| `results/table_audit.csv` | One row per table: metrics and classification |
| `results/findings.csv` | Every finding, with severity and IDs |
| `results/summary.json` | Totals |
| `results/missed_table_candidates.json` | Pages that may contain undetected tables |
| `tests/test_audit_rules.py` | Tests of the audit's own comparison rules |

**Bottom line:**
- **Values are copied faithfully:** 3,554 of 3,580 values match their own source cell exactly.
- **Typed procurement-plan rows are reliable:** 367 of 367 amounts and 1,547 of 1,547 dates are found in their source
  row.
- **Most generic tables are not safe for structured queries:**
  - unlabelled or wrong headers;
  - stacked values glued together;
  - values silently lost at page breaks;
  - undetected borderless tables.
- **Recommendation:** ingest the other 9 PDFs as *semantic and citation evidence* only (with approval), and do
  **not** treat any generic table as trusted SQL data until the P1 fixes below pass their acceptance tests (§11).

## 1. Scope and methodology

**Scope:**
- **Documents:** the 10 processed pilot PDFs (all within the approved 35-project scope).
- **Tables:** all **220 tables** and **3,347 rows** (3,131 data and 216 header rows) in
  `rag/corpus_v3/processed/pilot_v2/shared/`.
- **Typed plan rows:** the 236 typed procurement-plan activities.
- **Excluded:** `restricted/` was not read.

**Three levels of evidence, reported separately:**

| Level | What it is | Coverage |
|---|---|---|
| **A. Automated structural checks** | Column labels; alignment; merged/spanning rows; empty columns; duplicates; continuation links and split rows; reversed (rotated) header text; page and bbox consistency; units in header/caption; total rows; dash/n.a. cells; footnote markers | All 220 tables |
| **B. Automated source comparison** | Each table re-extracted from the **original PDF** with the pipeline's own pdfplumber settings (all 220 tables reproduce exactly: same IDs, row and column counts), held in memory only. Every value token in every cleaned cell is compared with **its own source cell** (same row, same column). Typed plan amounts and dates are compared with their own source row. Pages are scanned for number-dense lines outside any extracted table | All 220 tables; 3,580 value tokens; 236 activities; all 373 pages |
| **C. Manual source-page comparison** | Each page rendered from the original PDF and compared with every extracted row of the table | **12 tables + 4 pages**, a reproducible stratified sample: the table with the most values in each non-staff category, plus edge cases (a wrap repair, a glued-value case, the most "duplicate" rows, the split-row case) and 4 missed-table candidate pages. Staff tables were excluded (personal data) |

**Labels:**

| Label | Meaning |
|---|---|
| `verified` | Assigned **only** by level C: every cell matches the page and the column semantics are usable |
| `needs_review` | Values are plausible or verified, but a structural issue or the lack of manual verification blocks trusted use. All tables that passed automated checks but were not seen manually are `needs_review`, never `verified` |
| `unusable_for_structured_queries` | Values wrong, lost or semantically unusable (or personal-data staff tables). Still usable as cited text for semantic retrieval, except staff tables |

**How reliable the automated checks are, measured against level C on the same 12 tables:**
- Before tuning, they agreed with the manual verdict on "unusable vs not" for **8 of 12**, and **missed 3 unusable
  tables**.
- I added three audit-only rules: paragraph or data lines used as labels; several values packed into one cell; rows
  with a unit but no values after a page break.
- After that, agreement was **9 of 12** on that same, tuning, sample: 2 conservative over-flags and 1 miss. Because the
  rules were tuned on this sample, this is **not** an independent accuracy estimate.

**Conclusion:** automated `needs_review` must not be read as "probably fine".

## 2. Table inventory

| Document | Type | Project | Pages | Tables | Rows | Notes |
|---|---|---|---|---|---|---|
| 34455066 | ICR | P163452 | 55 | 54 (52 generic, 2 staff) | 985 | Annex results, DLI blocks |
| 34345370 | ICR | P132833 | 57 | 39 (35 generic, 4 staff) | 593 | |
| 34407877 | ICRR | P132833 | 25 | 3 | 38 | Ratings text layouts |
| 24440102 | PAD | P148850 | 120 | 48 (42 generic, 6 staff) | 791 | Cost/economic tables |
| 26256705 | PAD | P132833 | 78 | 31 (24 generic, 7 staff) | 569 | |
| 40089196 | ISR | P148850 | 8 | 5 | 42 | 2025 template; 2 tables on p7 missed |
| 33851641 | ISR | P175325 | 8 | 5 | 13 | 2022 template; results framework (pp. 3–6) missed |
| 33761744 | PP | P132833 | 8 | 12 (10 STEP, 2 generic) | 133 | |
| 40009757 | PP | P175325 | 8 | 10 (10 STEP) | 117 | |
| 40025990 | PP | P148850 | 6 | 13 (6 STEP, 7 generic) | 66 | |

**By audit category:**

| Category | Tables |
|---|---|
| ICR annex | 53 |
| ICR body | 34 |
| PAD financial | 33 |
| PAD other | 33 |
| STEP procurement plan | 26 |
| Staff | 19 |
| Procurement plan, other | 9 |
| ISR results indicators | 5 |
| ISR other | 5 |
| ICRR | 3 |

The pipeline also records 57 continuation tables in 163 continuation groups.

## 3. Extraction-quality measurements

| Measure | Value |
|---|---|
| Tables detected / audited automatically (A + B) / manually verified (C) | **220 / 220 / 12** (+ 4 pages) |
| Rows examined | **3,347** |
| Tables with all columns labelled (placeholders, paragraphs and data lines don't count as labels) | **57** |
| Tables with most columns unlabelled | **124** |
| Tables whose header text is reversed (rotated header) | 1 found automatically (and confirmed manually) |
| Continuation tables / split-row findings at page breaks | 57 / 2 high (ISR 40089196: one split name, one row whose values were lost) |
| Value tokens compared with their source cell | **3,580**: 3,554 exact, 11 correct wrap repairs, **15 glued from separate stacked values (4 PAD tables)**, 0 absent from their cell |
| Typed plan amounts / stage dates found in their own source row | **367 / 367**, **1,547 / 1,547** |
| Numeric tables with no unit or currency in header or caption | 94 |
| Tables with total/subtotal rows stored as ordinary rows | 25 |
| Tables with repeated data rows | 38 (in the sample these were repeated labels of hierarchical layouts, not extraction duplicates) |
| Data rows with text only in the first cell (spanning/merged) | > 30% of data rows in 23 tables |
| Columns empty in every data row (split header columns) | 124 tables |
| Missed-table candidate pages (≥ 3 number-dense lines outside any table) | **19**. Of 4 checked manually, 2 real (3 tables) and 2 prose false positives; 15 unchecked (§5) |

**Classification (final, A + B + C):**

| Category | `verified` | `needs_review` | `unusable_for_structured_queries` |
|---|---|---|---|
| STEP procurement plan (26) | 1 | 25 | 0 |
| Procurement plan, other (9) | 0 | 4 | 5 |
| ISR results indicators (5) | 0 | 1 | 4 |
| ISR other (5) | 0 | 3 | 2 |
| PAD financial (33) | 0 | 15 | 18 |
| PAD other (33) | 1 | 11 | 21 |
| ICR body (34) | 1 | 9 | 24 |
| ICR annex (53) | 0 | 11 | 42 |
| ICRR (3) | 0 | 2 | 1 |
| Staff (19) | 0 | 0 | 19 (personal data, never SQL) |
| **Total (220)** | **3** | **81** | **136** |

**Tables suitable for trusted SQL today: none as generic tables.** The only reliable structured data is the typed
procurement-plan activities (§7).

## 4. Incorrect or ambiguous tables (verified examples)

| Severity | Table (page) | Finding | Evidence |
|---|---|---|---|
| **High** | `24440102:t02201` (PAD p. 22, Table 1 Project Cost and Financing, US$ million) | The whole table collapsed into **one data row**; stacked values glued without separators | Source cell `340.00⏎18.00⏎12.00⏎370.00⏎0.00⏎370.00` became `340.0018.0012.00370.000.00370.00`; `% Financing` became `1001001001000100` |
| **High** | `24440102:t10401`, `t10801`, `t10901` (PAD pp. 104–109) | The same glued-value defect | `57⏎37⏎57⏎52` → `57375752`; `6⏎20⏎20⏎10⏎6` → `62020106` |
| **High** | `40089196:t00501` (ISR P148850 p. 5) | **Values lost silently** at a continuation: pdfplumber detected only the first-column cell of row 1 (no top border) | "Key operational units … (Yes/No)" shows No / Yes / Yes / Yes on the page, but every extracted value cell is empty. *This also affects the Step 4 Lakebase data:* that indicator is stored with no values (not wrong values). Not changed here |
| **High** | ISR `33851641` pp. 3–6 (P175325, 2022 template) | Results framework **not detected**. The one part extracted (`t00701`, p. 7) put each printed line in one cell | "Land area managed sustainably (Hectare(Ha))" with end target 6,000.00 for 20-Sep-2026 exists only as page text; `t00701` cell: `Value 0.00 -- 0.00 3,000.00` |
| **High** | ISR `40089196` p. 7 | Two borderless tables **not detected**: 7.2 Disbursements (by loan) and 7.3 Key Dates (by loan) | IDA-56250: 370.00 / 264.43 / 96.99 / 264.43 / 0.00; the unit (US$ millions) appears only on the chart axis |
| **High** | `34455066:t04502` (ICR p. 45, Table 4) | All values correct, but the rotated column headers were **read backwards** | `eganiarD` = Drainage, `sdaoR` = Roads, `sllifdnaL` = Landfills |
| **High** | `40025990:t00102` / `t00201` (PP pp. 1–2) | The continuation was linked correctly, but the header (narrow wrapped columns on p. 1) was not recognised: 7 `col_n` columns, unit lost | `Summary of the ICB (Works) 290 / 1 / No / Prior`, where 290 is US$ million according to the unrecognised header |
| **High** | `24440102:t11501` (PAD p. 115, Table 5.6, US$/km) | Values correct but structurally unusable: 3 stacked option sub-tables in a 16-column grid; headers stored as data rows; row-spanning weighted averages attached only to the first lot | Weighted average 3,271,081 covers lots 1–4 but sits on lot 1's row |
| Medium | `34455066:t03101` (ICR p. 31, DLI block) | Hierarchical layout: repeated row labels without the parent DLI; DLI titles split over 3 rows; `col_n`; no currency | "Allocated Amount 0.00 … 14,960,000.00" with no parent DLI |
| Medium | `40089196:t00301` (ISR p. 3) | Two-level header (the Value/Month-Year sub-header is stored as a data row); last row split across pages 3–4 | Values all correct; already flagged `needs_review` in Step 4 |
| Medium | `34407877:t01401` (ICRR p. 14) | Ratings text layout: a paragraph captured as a column name; template marker rows (`OBJR1_TBL`) | Semantic use only |
| Low | `40025990:t00401` plan activities | Typed stage-date keys keep unrepaired word breaks | `"Contract Complet ion"`, `"Draft Bidding Docum ent / Justification"` |

**Verified correct (level C):**

| Table | What was confirmed |
|---|---|
| `40025990:t00401` (STEP, P148850 p. 4) | Amounts, statuses and all planned/actual dates under the right stages; 0.00 kept as zero; wrapped amounts repaired |
| `26256705:t07001` (PAD Table 5.4 Base Year Traffic) | All 117 cells; em-dashes kept as `—`, not 0 |
| `34455066:t01401` (ICR Table 2 Summary of Achievement) | All rows and labels |

## 5. Multi-page, merged-cell and missed-table findings

**Continuations.** 57 continuation tables share their parent's column count. Two problems turned up:
- **Split rows:** one ISR row name is split across pages (flagged). In the next part, a row whose value cells have no
  top border lost its values (§4).
- **Header on the previous page:** for one plan summary table, the header on page 1 was not recognised at all.

**Merged and spanning cells:**
- **Row-spanning values** (for example the weighted averages in PAD Table 5.6) are attached to the first row only.
- **Spanning title or group rows** (objectives, components, DLIs) are stored as ordinary rows with text in the first
  cell only; this applies to more than 30% of data rows in 23 tables.
- **Multi-line cells without row rules** collapse several logical rows into one, which causes the glued values (§4).
- **Two-level headers** keep their second level as a data row.

**Rotated text:** vertical header labels come out reversed. One table was found; others may be hidden among the
`col_n` tables.

**Missed tables:**
- 19 candidate pages were flagged. Confirmed missed: ISR P175325 pp. 3–6 (results framework, 2022 template; p. 3
  checked visually) and ISR P148850 p. 7 (2 tables).
- False positives (narrative prose dense with figures): ICRR p. 3, ICR P132833 p. 14.
- **Not yet checked (15):** PAD 24440102 pp. 16, 30, 50, 97, 118; PAD 26256705 pp. 17, 31; ISR 33851641 pp. 4–6;
  ICRR pp. 2, 10; ICR 34455066 pp. 9, 16, 46.

## 6. Numeric extraction reliability

| Aspect | Finding |
|---|---|
| **Fidelity of cell text** | **3,554 / 3,580** value tokens identical to their own source cell; 11 correct repairs of numbers wrapped inside a cell (e.g. `140,348,30⏎8.59` → `140,348,308.59`); **15 corrupt** (glued stacked values, 4 PAD tables); **0** values not present in their source cell |
| Typed plan amounts and dates | 367 / 367 amounts and 1,547 / 1,547 dates found in their own source row. `money()` keeps blank as `None` (not 0). A printed `0.00` stays 0.0 (verified). 15 activities carry flags (8 `extractor_disagreement`, 7 `text_before_reference_in_cell`); 2 activities have no amounts (`33761744:p4t1r1`, `p5t1r1`) |
| Currencies and units | 94 numeric tables have no unit or currency in the header or caption. Units often live elsewhere: the caption (`US$ million`), the indicator name (`(Kilometers)`), or the page (`US$ millions` on a chart axis). Generic rows store no unit field |
| Decimals, negatives, percentages | Decimals are preserved where cells are not glued. 234 percentage cells, often mixed formats (`50%`, `Largely Achieved (90 %)`). Only 1 negative-looking cell, and `money()` would reject negatives and parentheses. Scale words (`6.6 million`) are not parsed |
| Blank vs zero | Dashes and `n.a.` are kept as text (9 tables; verified `—` in PAD Table 5.4), and blanks stay blank in the sample. There is **no generic numeric typing** yet, so "not zero" holds today only because nothing is typed |
| Dates and versions | 1,790 date-bearing cells in mixed formats (`2016-06-15`, `30-Jul-2025`, `Jun/2026`, `May 9, 2016`). Plan versions are not merged: one plan version per PP document (P132833 2022-03-18, P175325 2025-04-29, P148850 2025-06-27). ISR "previous" and "current" values carry their own dates |
| Totals | 25 tables have total/subtotal rows stored as ordinary rows. Summing a column would double-count |

## 7. Are the current representations sufficient?

| Representation | Semantic table discovery | Schema-aware retrieval | SQL over rows | Deterministic numeric calculation | PDF/page citation |
|---|---|---|---|---|---|
| Generic table rows (`table_rows`) | Partly (through windows and descriptions) | **No**: 124 tables mostly unlabelled; no units, totals or parent labels | **No** | **No** (no typing; glued and lost values) | **Yes** (table, row, page) |
| Typed plan activities | Yes (row text embedded) | **Yes** (fixed STEP schema) | **Yes, with care**: keys need normalising; flagged rows need review | **Yes** for amounts and dates (100% source-row match); USD only | **Yes** (page, table, row) |
| Table search windows (3-row) | **Yes**: the retrieval benchmark already uses them | No | No | No | Yes |
| Table descriptions (163, one per continuation group) | **Yes**: good for discovery; not yet embedded in production | Partly (caption, section, columns) | No | No | Yes (pages, table IDs) |
| Draft schema `wb_isr_results_indicators_v1` (Step 4) | n/a | **Yes** for the 2025 ISR template | Only for complete rows, after review | Only complete rows, with units | Yes |

**Needs extra extraction logic or human verification:**
- PAD cost and financing tables;
- PAD/ICR annex economic tables (stacked sub-tables, row spans);
- ICR DLI and results blocks (hierarchy);
- ISR results frameworks (two templates; borderless 2022 layout);
- ISR financial-performance tables (borderless);
- rotated-header tables;
- the plan summary tables on PP cover pages.

**Not suitable for SQL at all:**
- staff tables (personal data, D-14);
- ICRR rating text layouts;
- narrative grids.

## 8. Citation and source verification

**Holds today:**
- Every table row carries its document, table ID, row index and page, and all row pages equal the table page.
- All 220 table bounding boxes lie on their page. Two STEP pages are landscape; their bboxes exceed the portrait width
  by 0.1 pt only (rounding).
- All 220 tables re-extract identically from the original PDFs, so table and row citations are reproducible.

**Not yet true:** citations point to the table *row*, not the cell. A future SQL answer should cite
`table_id / row_index / column / page`.

**Gaps:**
- Missed tables have no table citation; their content is only citable as narrative text, where it was chunked.
- Values lost at page breaks (`t00501`) cannot be cited from the table at all.

## 9. Recommended improvements (prioritised; extend the existing pipeline; not implemented)

The design keeps today's flow (original PDF in a Unity Catalog Volume → `rag/ingestion` v2 → Lakebase) and adds
table-specific stages:

```
UC Volume (original PDF, SHA-256)
  → pdfplumber detection (lines)  +  NEW fallback detection (text/horizontal-rule strategy) for known borderless templates
  → table rows (generic, as today; raw cell LINES kept, not joined)                       [Lakebase pdf_tables / pdf_table_rows]
  → NEW template classifier → template parser → typed values with unit, scale, quality    [pdf_table_values + table_schemas registry]
  → NEW validation gates (below) → status: draft | needs_review | approved
  → table descriptions embedded (pgvector) for semantic table discovery
  → guarded SQL ONLY over approved schema views (allow-listed; validator; citations per cell)
  → human review queue for needs_review tables (decision records; HITL)
```

**P1: blockers before any table is treated as structured data**

| # | Change |
|---|---|
| 1 | **Stop gluing stacked values.** In `tables.py`/`cell_clean.py`, keep cell lines as a list. Split a multi-line numeric cell into rows only when all cells of the row have the same number of lines (column-aligned stacking); otherwise mark the row `needs_review`. Never join numeric lines without a separator |
| 2 | **Recover or flag lost values at page breaks.** When a detected row covers only part of the table width (cells `None`), recover values from words inside that row's band, or mark the row `values_missing`, so that "no data" never comes from a detection gap |
| 3 | **Template parsers for high-value layouts:** ISR results framework (2025 grid and 2022 block template), ISR §7 disbursements and key dates (borderless), STEP plans (existing), PAD "Project Cost and Financing", ICR annex costs. Each emits typed fields with units and a quality flag into `pdf_table_values`, governed by a `table_schemas` entry |
| 4 | **Approval gate for SQL:** only `approved` schemas, whose tables passed the gates in §11, are exposed as allow-listed views to the guarded SQL path. Everything else is semantic evidence only |

**P2: semantics**

| # | Change |
|---|---|
| 5 | **Headers:** compose two-level headers ("Baseline › Value"); read rotated text upright (pdfplumber char matrix / `upright` flag); take a continuation's header from the previous page even when it wraps narrowly |
| 6 | **Row roles:** flag `total` / `subtotal` / `comment` / `group-title` rows and propagate parent labels (Objective, Component, DLI) to their rows |
| 7 | **Units and scale:** take the unit from the header, caption, indicator name or page context, recording its source. Parse `million`, `%`, currencies and `--` / `n.a.` (= not available, never 0). Refuse typing when no unit can be attributed |
| 8 | **Normalise typed keys:** apply word repair to stage names (`Contract Completion`), so keys are stable across plans |

**P3: completeness and monitoring**

| # | Change |
|---|---|
| 9 | Link footnote markers to their notes |
| 10 | Add the missed-table detector and this audit's comparisons to `validate_v2` as reported metrics, with a reviewed list of candidate pages per document |

## 10. Tests run

| Suite | Result |
|---|---|
| `audits/pdf_table_quality/tests` (the audit's own rules: wrap vs glued values, identifier tokens, label quality, reversed headers) | **12 passed** |
| `rag/ingestion/tests` (existing extraction and validation suite, including table continuations and plan rows) | **24 passed** |
| `rag/tests` (approved 35-project scope) | **50 passed** |
| `infrastructure/lakebase_poc` offline (PDF evidence mapping, Step 4) | **41 passed** |
| `ml/tests` (frozen artefacts) | **23 passed** |

**Integrity:** a SHA-256 inventory confirms that no file outside `audits/pdf_table_quality/` changed.

## 11. Proposed acceptance criteria before scaling structured table ingestion

A table, or a schema, may move to `approved` (SQL-eligible) only if **all** of these hold. They should become
automated gates plus a recorded manual sample:

| # | Criterion | Target |
|---|---|---|
| 1 | Typed values present in their own source cell (automated, like level B) | 100% |
| 2 | Glued stacked values in typed or approved tables | **0** |
| 3 | Rows with a label (and unit) but missing values at a page break | 0 unflagged |
| 4 | Reversed header text in approved schemas | 0 |
| 5 | Every typed numeric value has an attributed unit/scale (with its source) | 100% |
| 6 | Total/subtotal rows flagged; parent labels propagated | 100% of approved tables |
| 7 | Manual stratified sample per approved schema, every cell compared with the page | ≥ 20% of its tables (at least 3), 100% cell agreement |
| 8 | Missed-table candidate pages for each document reviewed and recorded | 100% |
| 9 | Cell-level citations (`table_id`, `row`, `column`, `page`) for every typed value | 100% |
| 10 | Regression: retrieval benchmark, citation checks and privacy checks unchanged; existing 24 ingestion tests pass | No change |
| 11 | Staff/personal-data tables excluded from SQL | Always |

## 12. Go / no-go for the remaining nine pilot PDFs

| Use | Recommendation |
|---|---|
| **Narrative chunks, table search windows and table descriptions as cited semantic evidence** (the Step 4 pattern) | **Conditional GO**, with your approval: the cell text is faithful (3,554 / 3,580 exact; 0 values absent from their cell). Conditions: mark every generic table `needs_review` or `unusable_for_structured_queries` as classified here; exclude or flag the 4 PAD tables with glued values; keep staff tables out; record the known gaps (lost values in `40089196:t00501`, missed ISR tables) with the data |
| **Typed STEP procurement-plan activities** (3 plan PDFs) | **GO for structured use with care**: 100% of amounts and dates match their source rows. Keep the 15 flagged activities `needs_review`, normalise stage keys, and treat USD as the only currency |
| **Generic tables as trusted structured data (SQL, numeric answers)** | **NO-GO** until the P1 fixes (§9) are implemented and the acceptance criteria (§11) pass for each schema |

Stopped after the audit and recommendations, as instructed.
