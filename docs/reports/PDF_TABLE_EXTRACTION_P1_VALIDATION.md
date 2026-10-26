# PDF Table Extraction — P1 Fixes, Isolated Implementation and Validation (Step 6A)

**Date:** 2026-10-26.

**Scope:** the first two P1 items from [PDF_TABLE_QUALITY_AUDIT.md](PDF_TABLE_QUALITY_AUDIT.md):

1. stacked numeric values glued into one invalid number;
2. values lost at page breaks.

**Status: implemented and validated in an isolated shadow path only.** Nothing is merged into the active pipeline (`rag/ingestion/`). Nothing was written to Lakebase, Databricks, pilot_v1/pilot_v2, the retrieval caches, the manifests or the evaluation results.

**Next step:** stopping here for approval.

---

## 1. Summary

| Question | Answer |
|---|---|
| Are all 15 glued-value defects eliminated or flagged? | **Yes: all 15 are eliminated, plus 3 more** the audit had misclassified as "correct wrap repairs". There are **18 corrupt cells in total**, all in the 4 PAD tables. Every one now keeps its stacked values on separate lines, and its 10 rows are flagged `needs_review`. The audit's `line_merge` verdict count went from **15 to 0**. |
| Is the missing-value row in ISR `40089196:t00501` detected? | **Yes.** Row 1 is detected and filled **only from the PDF's own words**: Baseline `No`, Previous `Yes`, Current `Yes`, Closing `Yes`. Every Month/Year cell stays empty. The row is flagged `values_recovered` + `needs_review` with provenance `pdf_source_words`. A test removes the source words and confirms nothing is filled in. |
| Were any legitimate values changed or lost? | **No.** All 33,795 source cells of the 220 pilot tables were re-cleaned. Only the 18 corrupt cells differ, and in each case the only difference is inserted line boundaries: removing them gives back the old text exactly. 3,331 / 3,331 unaffected rows are byte-identical to pilot_v2. Row count (3,347) and personal-data mask count (66) are unchanged. |
| Are there new parsing errors? | **None found.** No `line_merge` verdicts remain, and no value lacks support in its source. The 2 tokens the audit rule reports as "not in source cell" are values recovered by design from source words whose pdfplumber cell was `None`; both were checked against the rendered page. |
| Is typed procurement-plan extraction unchanged? | **Yes.** `plan_activities` is not touched. 0 of 7,065 STEP-table cells clean differently, and `plan_tables.py` (amounts) does not use the changed function. *But see §6:* the lost-value detector found that 2 typed STEP activities are already incomplete in pilot_v2 for the same root cause. |
| Were any new defects found? | **Yes, 6 more rows with lost values that the audit had missed:** 1 PAD row, 2 STEP plan rows and 3 ICR rows, plus 14 rows in a staff table, which were counted only. All were confirmed on the rendered pages (§5.2). |

---

## 2. Root causes

### 2.1 Stacked numeric values glued together

When a PDF table has no horizontal rules between logical rows, pdfplumber returns several logical rows as **one cell** whose lines are joined with `\n`, for example `340.00⏎18.00⏎12.00⏎370.00⏎0.00⏎370.00`.

The active cleaner, [cell_clean.py](../../rag/ingestion/cell_clean.py) `clean_cell`, closes **every** line break where the line before ends in a digit and the next line starts with one. That rule exists to repair one number wrapped in a narrow column (`9,890,912.0⏎7` → `9,890,912.07`). But it cannot tell a wrap from independent values, so it produces `340.0018.0012.00370.000.00370.00`.

### 2.2 Values lost at page breaks

pdfplumber's line-based table finder builds cells from ruling lines.

- On ISR p. 5, the continued table has no top border above the value columns, so row 1 gets a cell only in the first column. The other 8 cells come back as `None`, and their printed text (`No`, `Yes`, `Yes`, `Yes`) is dropped **silently**.
- The same mechanism hits the first row of the STEP continuation tables on `33761744` pp. 4–5, and spanning cells in PAD and ICR tables (§5.2).

Nothing in the active pipeline compares the page words with the detected cells, so the loss is invisible downstream.

---

## 3. What was implemented (shadow path only)

New package [rag/ingestion_p1/](../../rag/ingestion_p1/) (864 lines including tests):

- It imports `clean_cell` and `sanitize` from `rag.ingestion` read-only.
- It does **not** modify any existing module.
- No active code imports it.

| Module | What it does |
|---|---|
| [stacked_values.py](../../rag/ingestion_p1/stacked_values.py) | **`clean_cell_lines`:** cleans exactly like `clean_cell`, except that it keeps a line boundary between two lines that are each a complete value (number, %, currency, parentheses, or date) and are *not* one wrapped value. A wrap is an incomplete left fragment (`140,348,30`, `290,000,000.`, `2022-05-`) or a 1-decimal value finished by a single digit, where the joined text is well formed. Each segment is still cleaned by the unchanged `clean_cell`, so word-break repair is identical.<br><br>**`split_stacked_row`:** splits a row into sub-rows **only** when (a) all stacked columns have the same count and their *i*-th lines lie within half the smallest line gap (geometry), and (b) every other non-empty column has exactly one line per value on the same baseline (±2.5 pt) with no wrapped continuation (lower-case start, or a line ending in `and`/`of`/`,`/`-` …). **Otherwise nothing is split:** the row keeps its line-preserved cells and is flagged `needs_review` with the reason. |
| [lost_values.py](../../rag/ingestion_p1/lost_values.py) | For a partly detected row, it finds source words inside the row's vertical band and the table width that lie in **no detected cell of the table**. Words inside cells spanning from neighbouring rows are therefore not "lost". A lost word is recovered only if it lies inside **exactly one** column (column x-ranges come from the table's complete rows) **and that column's cell is empty**. Otherwise the row is `values_missing`, and nothing is filled in. |
| [shadow_run.py](../../rag/ingestion_p1/shadow_run.py) | Re-detects the 220 pilot tables with the pipeline's own settings and numbering. Unaffected rows are copied verbatim from pilot_v2; affected rows get P1 cells and a `p1` block (status, reasons, `needs_review`, stacked columns, provenance).<br><br>Recovered text is re-read from the source characters of the recovered words. Changed tables are sanitised as a whole (as `_sanitise_table` does for non-staff tables), and a fail-closed guard keeps the pilot_v2 cells if any mask would be lost.<br><br>A recovered value is flagged `possible_row_span` (never copied down) when the next row's cell in that column is also empty. **Staff tables are never modified, and none of their words are written** (counts only). |
| [validate_p1.py](../../rag/ingestion_p1/validate_p1.py) | Validation against the PDFs, pilot_v2 and the Step 5 audit rules (§5). |

**Output** (new directory, 1.0 MB): `rag/corpus_v3/processed/experimental_p1/`.

- `table_rows.jsonl`: 3,347 rows.
- `changes.jsonl`: 16 rows.
- `summary.json` and `validation.json`.

Only sanitised text is written.

**Output format of a stacked cell:** `cells_clean` holds the values separated by `\n` (for example `"340.00\n18.00\n12.00\n370.00\n0.00\n370.00"`), and `p1.cells_lines` holds the same values as a list.

---

## 4. Before / after: stacked values (P1-1)

### 4.1 The 4 PAD tables (document `24440102`)

| Table | Row | Cell | pilot_v2 (active) | P1 (shadow) | Row status |
|---|---|---|---|---|---|
| `t02201` Project Cost and Financing | 2 | Project cost | `340.0018.0012.00370.000.00370.00` | `340.00⏎18.00⏎12.00⏎370.00⏎0.00⏎370.00` | `needs_review`: label column ambiguous |
| | | IBRD/IDA financing | (same glued value) | (same 6 values) | |
| | | % Financing | `1001001001000100` | `100⏎100⏎100⏎100⏎0⏎100` | |
| `t10401` | 2 | 7 value columns | `57375752`, `126.00.00.00.0`, `0.0100.00.00.0`, `0.00.0370.00.0`, `0.00.00.0315.0`, `99.122.00.045.0`, `225.1122.0370.0360.0` | `57⏎37⏎57⏎52`, `126.0⏎0.0⏎0.0⏎0.0`, … `225.1⏎122.0⏎370.0⏎360.0` | `needs_review`: label column ambiguous |
| `t10801` | 3, 8, 9, 10 | Staff weeks | `4666312`, `1212`, `1248481524`, `66` | `4⏎6⏎6⏎6⏎3⏎12`, `12⏎12`, `12⏎48⏎48⏎15⏎24`, `6⏎6` | `needs_review`: wrapped / spanning labels |
| `t10901` | 3, 5, 6, 7 | Staff weeks | `66`, `62020106`, `44`, `44` | `6⏎6`, `6⏎20⏎20⏎10⏎6`, `4⏎4`, `4⏎4` | `needs_review`: spanning labels |

**Why none of these rows was split into sub-rows:** in every case at least one text column cannot be proven to map one-to-one onto the values.

- **`t02201`:** the label column has 11 lines for 6 values, and includes headings such as "Total Costs".
- **`t10401`:** a label wraps ("3. Batu (Zeway)-Arsi⏎Negele").
- **`t10801` / `t10901`:**
  - one activity label spans several roles ("Project construction" for 5 values);
  - a phrase wraps ("Team leadership and⏎leadership Support").

The rule refuses all of these by design, as required. The splitting logic is covered by synthetic tests (§7).

### 4.2 Whole-pilot differential and audit verdicts

| Check | pilot_v2 | P1 |
|---|---|---|
| Source cells re-cleaned (220 tables) | 33,795 | 33,795 |
| Cells cleaned differently | — | **18**, all in the 4 PAD tables |
| … of which a pure line-boundary insertion (removing `\n` restores the old text) | — | **18 / 18** |
| Staff-table cells changed | — | **0** |
| STEP-plan cells changed | — | **0 / 7,065** |
| Audit verdict `exact` (value tokens of data rows vs their own source cell) | 3,477 | **3,549**: the 15 glued tokens became 72 exact ones |
| Audit verdict `line_merge` (glued) | **15** | **0** |
| Audit verdict `wrap_joined` (correct wrap repair) | 11 | **8** |
| Audit verdict `not_in_source` | 0 | 2: recovered values whose source cell is `None` (§5.1) |

**Correction to the Step 5 audit:**

- The audit reported **11 correct wrap repairs and 15 glued tokens**.
- 3 of those "wrap repairs" are actually glued values in `t10401`: columns 3, 7 and 8 (`126.00.00.00.0`, `99.122.00.045.0`, `225.1122.0370.0360.0`). The audit's token regex happened to read them as one wrapped number.
- The true numbers are **8 wrap repairs and 18 corrupt cells**. All 18 are fixed here.

---

## 5. Before / after: lost values (P1-2)

### 5.1 Detection over the whole pilot

| Row | What the page shows | pilot_v2 | P1 result | Verified |
|---|---|---|---|---|
| **ISR `40089196:t00501` r1** (p. 5) "Key operational units … (Yes/No)" | `No` / `Yes` / `Yes` / `Yes` in the 4 Value columns | value cells empty | **`values_recovered`**: `No, "", Yes, "", Yes, "", Yes, ""`, flagged `needs_review` | Rendered page ✔ |
| PAD `24440102:t10801` r9 (p. 108) | "12-48 months" (Time) and "NA" (Partner role), in cells that span rows 9–10 | empty | **`values_recovered`** + **`possible_row_span`** for both columns; row 10 is left empty (not copied down) | Rendered page ✔ |
| STEP `33761744:t00501` r1 (p. 5) | Loan `IDA / 57880`, Post, Request for Bids, Open - National, Single Stage - One Envelope, Actual 0.00, Pending Implementation, 6 planned dates (2020-11-20 … 2022-05-18) | all 13 cells empty | **`values_recovered`** (13 cells, each in a unique column), `needs_review` | Rendered page ✔ |
| STEP `33761744:t00401` r1 (p. 4) | Same kind of activity row | empty | **`values_missing`**: 53 lost words, some glued across narrow columns by the PDF's character spacing (e.g. `2019-10-082020-01-24…`), so no unique column; **nothing filled** | Words confirmed |
| ICR `34345370:t00701` r6, r19, r24 (p. 7) | Restructuring dates `12-Aug-2019`, `23-Jun-2020`, `02-May-2023` | absent from the table | **`values_missing`**: the table has no complete row to learn the column grid from, so **nothing filled** | Rendered page ✔ |
| Staff table `34345370:t04801` | 14 rows | first column lost | **Counted only**: staff tables are excluded and no word was written | — |

The detector gave **0 false positives** after one design fix.

- **Problem:** the first version compared words only against the row's own cells, which flagged words inside cells spanning down from a neighbouring row (`t00501` rows 6, 8 and 9).
- **Fix:** words are now compared against every detected cell of the table.
- **Tests:** a synthetic test and the full-pilot run cover this.

### 5.2 Effect on typed records (reported, not changed)

- **ISR results values** (the Step 4 `pdf_table_values` logic, run offline with the same function):
  - pilot_v2 row 1 of `40089196:t00501` yields **no indicator values at all**, so the indicator is missing from the typed ISR values;
  - the P1 row would yield Baseline `No`, Previous `Yes`, Current `Yes`, Closing `Yes`.
- **Typed STEP activities** `33761744:p4t1r1` (`…-136348-CW-RFB`) and `33761744:p5t1r1` (`…-205625-CW-RFB`):
  - In pilot_v2, every field except reference, description and component is empty: review, method, market approach, status `blank`, amounts, and all stage dates. The cause is the lost-values defect.
  - The Step 5 audit noted "2 activities have no amounts" without identifying the cause.
  - The typed plan is **unchanged** here, as required. A fix would need a separate, approved change to `plan_tables.py` / `pipeline_v2.py`.

---

## 6. Typed procurement-plan extraction: unchanged

- `plan_activities.jsonl` and `plan_versions.jsonl` are not read for writing and not regenerated.
- The plan fields that `pipeline_v2.py` cleans with `clean_cell` are description, Method, Process Status, Component and Market Approach. **0 of 7,065** STEP-table cells would clean differently under `clean_cell_lines`.
- Amounts come from `plan_tables.money()` on raw cells and do not use the changed function.
- Integrating P1-1 as implemented would therefore **not change** any typed activity.
- Integrating P1-2 into the plan path would *improve* the 2 activities above, but only after a separate review.

---

## 7. Test coverage and regressions

| Suite | Result |
|---|---|
| **New:** `rag/ingestion_p1/tests/test_synthetic_layouts.py` (no PDF) | **42 passed**. See the coverage list below. |
| **New:** `rag/ingestion_p1/tests/test_pilot_regressions.py` (pilot PDFs, read-only) | **23 passed**. See the coverage list below. |
| `rag/ingestion/tests` (active pipeline) | **24 passed** |
| `rag/tests` (RAG scope, retrieval, ingestion outputs) | **52 passed** |
| `infrastructure/lakebase_poc/tests` (offline) | **41 passed** |
| `ml/tests` (frozen artifacts) | **23 passed** |
| `audits/pdf_table_quality/tests` | **12 passed** |

The synthetic suite covers:

- **Stacked layouts:** decimals of mixed precision, %, negatives / parentheses, `US$` prefix, years, `dd-Mon-yyyy` and ISO dates, thousands groups.
- **Wraps still joined:** split thousands groups, a trailing decimal point, a last digit, `1,⏎234`, wrapped dates.
- **Cleaned exactly as before:** word breaks and identifiers.
- **Splitting:** an aligned split, five ambiguous-label layouts, mismatched counts, an offset ≥ half a line.
- **Lost values:** recovery, a word straddling columns, no grid, a spanning cell from another row, complete rows, words outside the table.

The pilot regression suite covers:

- for the 4 PAD tables (including `t02201`): no glued value, every value in its source cell, only boundaries added, exact expected values, and rows flagged rather than split;
- ISR `t00501`: detection, source-word-only filling, and no invention when the words are absent;
- the `possible_row_span` flag.

Commands, run from `procurement-intelligence/`:

```bash
/opt/anaconda3/bin/python3 -B -m pytest -q -p no:cacheprovider rag/ingestion_p1/tests
/opt/anaconda3/bin/python3 -B -m rag.ingestion_p1.shadow_run       # writes experimental_p1/ only
/opt/anaconda3/bin/python3 -B -m rag.ingestion_p1.validate_p1      # writes experimental_p1/validation.json
```

**Protected-file check:** a full inventory (SHA-256 of every file) was compared with the inventory taken after Step 5.

- Changed: **0** files.
- Removed: **0** files.
- Added: **13** files, all under `rag/ingestion_p1/` and `rag/corpus_v3/processed/experimental_p1/`, plus the Step 5 audit report (written after that inventory).
- No `__pycache__` or `.pytest_cache` was created.

No network, cloud, LLM or model-download call was made.

---

## 8. Unresolved cases

1. **No real row was split.** All 10 stacked rows are labelled in ways the rule refuses to guess: spanning group labels, wrapped phrases, headings among labels. Their values are now correct and separated, but each row still pairs one label cell with several values and needs review before structured use. A table-specific layout model (P2) would be needed to assign labels safely.
2. **`values_missing` rows are flagged but not filled:**
   - STEP `33761744:t00401` r1: 53 words;
   - ICR `34345370:t00701`: 3 dates.

   Recovering them would need either character-level column assignment (STEP) or a column grid learned from rules/lines rather than complete rows (ICR).
3. **Row spans:** "12-48 months" and "NA" in `t10801` apply to rows 9–10 in the source. They are recovered into row 9 only and flagged `possible_row_span`. Propagating them is left to review.
4. **Residual wrap ambiguity**, outside the pilot, in two cases:
   - an integer wrapped **without** a separator (`12345⏎6`) is now treated as two values (the active code would join it);
   - `99.1⏎2` is treated as a wrapped `99.12`.

   Neither pattern occurs in the pilot: the differential shows 0 such cells. Geometry (character gap) could settle both if they appear in the full corpus.
5. **Staff table `34345370:t04801`** loses its first column (14 rows). It is excluded by policy and only counted.
6. The 15 unchecked missed-table candidate pages from Step 5 are out of scope here.

---

## 9. Integration readiness

**Verdict:**

- **P1-1 (stacked values): ready to integrate after approval.** It is low-risk, deterministic, and on the pilot it changes only the 18 corrupt cells, each by boundary insertion.
- **P1-2 (lost values): ready for detection and flagging. Recovery needs a review decision.** All 3 recoveries on the pilot are correct on the rendered pages. However, recovered values come from page geometry rather than a detected cell, so they should stay `needs_review` (as implemented) until reviewed.

**Before integrating, decide or do the following:**

1. **Cell format.** Stacked values carry `\n` inside `cells_clean`. Downstream code that treats `\n` as a row separator must be adapted:
   - `_sanitise_table` (the shadow path uses a segment sentinel for this);
   - table windows (`" || ".join` / `" | ".join` would embed the newline);
   - table descriptions.

   Either render segments as `"; "` in indexed text, or add a typed `cells_lines` field.
2. **Schema.** Add a `p1` / `extraction_flags` field (status, reasons, `needs_review`, provenance) to `table_rows`, and carry it into `pdf_table_rows` in Lakebase.
3. **Version bump.** Change `EXTRACTION_VERSION` (`ingest-pilot-2.0.0` → 2.1.0) and the code hash, then run a new processed output directory, so pilot_v2 stays reproducible.
4. **Decide whether recovered values are indexed or only stored.** The recommendation is to store and display them with their flag.

---

## 10. Risks to citations, chunks and embeddings

| Artefact | Effect if integrated | Risk |
|---|---|---|
| **Citations** (`table_id`, `row_index`, page) | **None.** No row is added, removed or renumbered (3,347 = 3,347), table IDs are unchanged, and pages are unchanged. | Low |
| **Narrative chunks** (strategies A/B/C) | **None.** They are built from non-table lines, and none contains the affected cell text. | None |
| **Table-window retrieval units** | Text changes in **11 of 1,609** table windows per strategy (corrected in Step 6B; first published as 12 of 1,845):<br>• `t02201` w1-2;<br>• `t10401` w1-2, w2-3;<br>• `t10801` w2-4, w6-8, w8-10;<br>• `t10901` w2-4, w4-6, w6-8;<br>• `33761744:t00501` w1-2;<br>• `40089196:t00501` w1-2.<br><br>Unit IDs are stable. The 5 windows that contain only `values_missing` rows keep their text. | Medium |
| **Embedding caches** | The cache key in `rag/retrieval/core.py` hashes **all** unit texts, so changing any of the 11 windows invalidates the whole cache file for each strategy. That forces full re-embedding of about 2,300–2,400 units per strategy, unless a per-unit cache is introduced first. **No embeddings were regenerated here.** | **High (cost and approval)** |
| **Table descriptions** | The 8 affected tables keep their descriptions. Example rows could change only for these tables. | Low |
| **Lakebase `wbg_rag_pdf_poc`** (document `40089196`) | Would change:<br>• 1 `pdf_table_rows` row (`t00501` r1);<br>• its typed `pdf_table_values` (from none to 4 values);<br>• 1 retrieval unit plus its embedding (`40089196:t00501:w1-2`).<br><br>**Not done:** it requires your approval and a re-load of that unit. | Medium |
| **Evaluation results** | Retrieval evaluations that include table units could shift slightly. They were not re-run, to avoid overwriting current results. | Low–medium |

---

## 11. Files

**Added**:

- `rag/ingestion_p1/__init__.py`
- `rag/ingestion_p1/stacked_values.py`
- `rag/ingestion_p1/lost_values.py`
- `rag/ingestion_p1/shadow_run.py`
- `rag/ingestion_p1/validate_p1.py`
- `rag/ingestion_p1/tests/__init__.py`
- `rag/ingestion_p1/tests/test_synthetic_layouts.py`
- `rag/ingestion_p1/tests/test_pilot_regressions.py`
- `rag/corpus_v3/processed/experimental_p1/{table_rows.jsonl, changes.jsonl, summary.json, validation.json}`
- this report

**Not modified:**

- `rag/ingestion/*` (active pipeline);
- pilot_v1 / pilot_v2;
- retrieval caches and embeddings;
- PDFs and manifests;
- evaluation results;
- the Step 5 audit outputs;
- Bronze / Silver / Gold;
- Lakebase, local PostgreSQL and Databricks;
- Structured Data Agent / MCP / guardrails / routing / memory;
- ML models;
- cleanup archives.
