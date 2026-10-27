# PDF Table Handling: Current Approach and Future Scope

**Date:** 2026-10-27 · **Scope:** tables inside PDF documents only (not the Databricks Gold tables) · **Corpus:** `pilot_v3`
(237 documents, 35 projects) · **Status:** documentation only; nothing here changes the running system.

---

## 1. How tables are handled today

### 1.1 Extraction (once per document)

1. Each table in a PDF is detected and its rows are extracted. Cell text is cleaned and every row keeps its page number.
2. The results are saved to files, not to a database:

   | Output | Count (`pilot_v3`) | File |
   |---|---:|---|
   | Tables | 4,487 | `shared/tables.jsonl` |
   | Table rows | 60,560 | `shared/table_rows.jsonl` |
   | Table descriptions | 3,141 | `shared/table_descriptions.jsonl` (created, **not used yet**) |
   | Procurement-plan activities (typed records) | 6,837 | `shared/plan_activities.jsonl` |
   | Links from plan activities to contract awards | 8,136 | `shared/evidence_links.jsonl` |

### 1.2 Indexing: tables become 3-row slices

- Each table's non-empty rows are cut into **overlapping slices of 3 rows**: step 2, sharing one row with the previous slice
  (rows 0–1, 1–3, 3–5, …).
- Each slice is turned into one piece of searchable text:

  ```
  <document name / project / type> table in <section heading path>
  cell | cell | cell || cell | cell | cell || cell | cell | cell
  ```

- Every slice is embedded and indexed (keyword + meaning) alongside the ordinary text chunks: **28,142 table slices**.
- Code: `rag/retrieval/core.py`, `Corpus._tables()`.

### 1.3 Procurement plans get extra treatment

- Every procurement-plan row is also stored as a **typed record**: description, method, market approach, review type,
  estimated and actual amounts, stage dates, status and references.
- Records are linked to the matching contract award where one exists.
- A question that names an activity reference or contract number is answered by **exact lookup**: the record is pinned to
  the top of the results, or the question is refused if no such record exists.
- Code: `Corpus._activities()` and the guard in `Retriever.guard()`.

### 1.4 At question time

1. The guard narrows the search scope (project, country, references).
2. Keyword and meaning search rank **all** pieces together (text chunks, table slices, plan activities). The two rankings are
   fused.
3. The top results (at most 8 pieces of evidence) go to Claude, which answers and cites document and page.

### 1.5 Does the table header reach Claude when the answer is in the middle of a table?

**For ordinary tables: no.**
- A slice carries only its own 3 rows plus the document name and section heading.
- The column-header row is inside the slice only when the slice is at the top of the table.
- A slice from row 40 reaches Claude as bare values, for example `2,500,000 | RFB | Prior`, with no label saying which column
  is the estimated cost.

**For procurement plans: effectively yes.** The typed activity record names every field (method, estimated amount, dates),
so that record is self-describing even when the table slice is not.

### 1.6 How a big table is handled

- A 200-row table becomes about 100 independent slices. Nothing represents the table as a whole.
- Each slice competes on its own against every other piece in scope.
- Claude typically sees **one to three slices** (3–9 rows) of the table. It never sees the whole table, the header (unless a
  top slice is selected) or the rows the search did not rank highly.
- When a table continues onto the next page, joining the continuation to the header on the first page is not guaranteed
  (to be verified per document).

### 1.7 Strengths of the current approach

- **Simple and uniform:** tables go through the same search and citation path as text.
- **Good for "needle" questions** whose words appear in one row ("estimated cost of the 260,000 prepaid meters").
- **Every row carries its page**, so citations point to the right page.
- **Procurement plans are strong:** typed records, exact reference lookup and links to contract awards.

### 1.8 Limitations of the current approach

| # | Limitation | Effect |
|---|---|---|
| 1 | No calculations across rows | Totals, counts, averages and "which packages are delayed" cannot be answered reliably |
| 2 | Column headers missing from most slices | Claude may misread which number is which (estimated vs actual, local currency vs USD) |
| 3 | Only part of a table reaches Claude | Answers needing many rows are silently incomplete |
| 4 | No whole-table unit | "Show the results framework table" has to be pieced together from slices; the 3,141 descriptions are unused |
| 5 | Tables broken across pages | Continuations may lose their header and be treated as unrelated |
| 6 | Many vectors | 28,142 slices (more than the text chunks), so rebuilds and new models are slow (the `bge-m3` encode took hours) |
| 7 | Extraction noise passes straight through | Merged cells, headers spread over two rows and split numbers reach search and Claude unchanged |
| 8 | Language | French tables are indexed in French; English questions rarely find them with the English model |
| 9 | Only procurement plans are typed | Cost, financing and results-framework tables exist only as text slices |
| 10 | Numbers are read, not looked up | Claude reads values from slice text; verification checks the page, not the exact cell |

Measured: the hand-verified benchmark has only 4 table questions (recall@5 = 0.75). It is too small to judge tables, so a
dedicated table benchmark is part of the future scope.

---

## 2. Refinements within the current approach (smaller changes)

These keep the slice design but remove its worst weaknesses.

| Refinement | Fixes | Strengths | Limitations |
|---|---|---|---|
| **R1. Repeat the header row in every slice** | 2 | Every slice says what its columns mean; small change | Re-embed the table slices; multi-row and noisy headers still need cleaning |
| **R2. Label each cell with its header** ("Estimated cost: 2,500,000") | 2, 10 | Claude reads labelled values instead of bare numbers | Needs reliable header-to-column mapping; slices get longer |
| **R3. Index the existing table descriptions** as extra searchable pieces | 4, 8 | Finds the table as a whole; descriptions can be written in English for French tables | Description quality varies; 1,346 tables have no description yet |
| **R4. Small-to-big for tables:** when a slice is hit, also give Claude the header and neighbouring rows (or the whole table if small) | 3, 2 | More complete answers with no new vectors | More text per question; very large tables still need a cap |
| **R5. Join tables continued across pages** during extraction | 5 | Continuations inherit the header | Extraction change, needs validation on real documents |

---

## 3. Future scope: table descriptions + relational store (proposed design)

### 3.1 The idea

1. **Store every PDF table in a relational database** with metadata: document, project, country, document type, page range,
   section, caption, column headers, units and currency.
2. **Embed one description per table** (a table card): what the table is about, its columns, period, units and a few sample
   values. The vectors cover table cards, not row slices.
3. At question time:
   1. search the table cards to find the right table or tables;
   2. use the metadata to locate them in the database;
   3. **retrieve from the table itself**: rank its rows for a lookup, or run SQL for filters and totals;
   4. answer from the exact rows and cells, citing table, row and page.

### 3.2 Refined flow

```
Question
 └─ Guard (scope, privacy, exact references): unchanged
 └─ Route by question type
     ├─ narrative ("why / lessons / risks") ──► text chunks, as today
     └─ table-type ("how much / which method / dates / totals")
          1. search TABLE CARDS (description + headers + section + document metadata) → top tables
          2. inside those tables
               lookup     ("cost of the 260,000 meters") → rank that table's rows
               aggregate  ("total of all goods packages") → SQL on the typed table
          3. answer from exact rows/cells; cite table, row, page
```

### 3.3 Storage options

| Data | Delta (Databricks) | Lakebase Postgres |
|---|---|---|
| Typed procurement-plan activities (uniform schema) | **Preferred.** A Gold table that the existing SQL worker queries with the read-only principal, joinable with contract awards | Possible; the SQL worker would need a second database |
| Generic tables (metadata + rows, cells keyed by header) | Possible | **Preferred.** It sits beside the table-card vectors (pgvector), so finding and fetching a table happen in one database |

PDF tables have very different layouts, so generic tables are stored in **one common shape**:
- one record per row, with cells keyed by their column header;
- the numeric value and unit where they can be parsed.

Only uniform table families (procurement plans first) get a dedicated typed table.

### 3.4 Strengths

- **Headers and context are always preserved:** the table is handled as a whole.
- **Calculations become possible:** totals, counts, filters and version comparisons via SQL.
- **Exact numbers:** values come from cells, so citations are cleaner and hallucinations fewer (assessment Module 3).
- **Far fewer vectors:** about 4,500 table cards instead of 28,142 slices, so new embedding models encode in minutes, not hours.
- **Cross-language:** cards can be written in English (and French), so English questions find French tables.
- **Assessment fit:** PDF tables become structured data linked to projects and documents. This is the semi-structured →
  structured link that Module 1 asks for.

### 3.5 Limitations and risks

| Risk | Mitigation |
|---|---|
| Messy PDF tables (multi-row headers, merged cells, page breaks) do not fit clean schemas | The common row-and-header shape for generic tables; typed tables only for uniform families |
| A card finds the table, not the row | Second step inside the chosen table: row ranking for lookups, SQL for aggregates |
| Card quality decides recall | Cards built from document, section, caption, headers and sample values; optional Claude-written summary (a few dollars for about 4,500 tables, needs approval) |
| SQL over arbitrary extracted columns is error-prone | SQL only on typed tables, with the same read-only validation as for Gold; generic tables use row ranking and simple filters |
| Questions spanning several tables (plan versions) | Card search returns several tables; plan versions (85) are already linked by plan stream |
| Two-step retrieval adds latency | A few seconds; acceptable for decision support |

### 3.6 How it would be validated

- **A table benchmark of about 25 hand-checked questions**, split into lookups, totals and filters, version comparisons, and
  questions that must be refused.
- **Metrics:** right table found in the top 3, right value or cell, correct refusal.
- **Comparison:** run against the current slice approach. Slices are retired only if the new path wins.

### 3.7 Indicative phases

1. **Table cards:** build, embed and measure table-finding accuracy.
2. **Rows into the relational store:** header mapping, row ranking inside a table.
3. **Procurement plans as a Gold table:** wire them to the SQL worker for totals and filters.

---

## 4. Summary

| | Current (3-row slices) | Refinements R1–R5 | Future: table cards + relational store |
|---|---|---|---|
| Header reaches Claude | Only for top slices (plans: via typed record) | Yes | Yes |
| Whole-table view | No | Partly (R3, R4) | Yes |
| Totals / counts / filters | No | No | Yes (typed tables via SQL) |
| Exact cell values | No (read from text) | Better (R2) | Yes |
| Vectors for tables | 28,142 | About 28,000 + descriptions | About 4,500 |
| French tables from English questions | Weak | Better (R3) | Good |
| Effort | — | Small | About 1–1.5 days |
