# Evaluation results

WBG Procurement Intelligence Platform. Golden sets run on 2026-10-28; the end-to-end run is from 2026-10-27.
Code: 912b1dc plus uncommitted work (TableLens, MCP, feedback).

## Summary

| Area | Golden set | Result | What it means |
| --- | --- | --- | --- |
| Gold data (structured) | 8 reviewed reference queries | **8/8** return the reference values | The data behind structured answers is stable |
| SQL safety | 8 unsafe + 8 valid statements | **16/16** correct; every rejection gives the right reason | Writes, DDL, stacked statements and unapproved tables are refused |
| Guardrails | 18 hand-labelled inputs | **18/18** correct | Injection, credentials, personal data, accusations and off-topic requests are blocked; 8 legitimate questions pass |
| Personal-data masking | 6 hand-labelled texts | **6/6** | Emails and phone numbers masked; procurement references, amounts and dates kept |
| Document retrieval, hand-verified | 27 answerable + 4 unanswerable (v1) | recall@5 **0.89**, MRR 0.75; 2/4 refused | Strong on the 10 pilot documents |
| Document retrieval, at scale | 112 English questions (v3) | recall@5 **0.43**, recall@10 0.66 | Weaker across all 237 documents ("lessons" and "procurement" sections especially) |
| Document retrieval, French | 112 French questions + 15 cross-language | recall@5 **0.13** (French); 0.47 (cross) | French questions about English documents are the weakest point |
| TableLens table search | 22 table questions + 2 negatives | hit@1 **15/22**, hit@2 **20/22**; 1/2 negatives correctly empty | Usually finds the right table, ranked first in two thirds of questions; a staff-names question is not stopped (open) |
| Routing (rule router) | 296 held-out questions | exact **77/296 (26%)**; reaches the right agent **293/296 (99%)** | Most document questions fall back to the planner, which costs an extra Claude call |
| End-to-end answers | 71 cases with Claude (run 1) | see below | TableLens ON 17/22 vs OFF 4/22; structured 7/8 |

The golden sets use no language model and cost nothing to re-run. "Golden" means each question has a known correct
answer: hand-checked for v1, TableLens, guardrails, masking and SQL safety; produced by reviewed SQL for the
structured set. v3 and French gold pages come from section headings, not hand-checking, so they understate retrieval
when the answer sits outside the matching section.

## 1. Structured data and SQL safety

- **Reference values (8/8).** Each reviewed Gold query was run through the MCP server's `execute_readonly_sql`
  (read-only Databricks). Every query returned exactly the values stored in the evaluation dataset on 2026-10-27.
- **Validator approves (8/8).** The same 8 reviewed queries pass `validate_sql`.
- **Validator rejects (8/8), with the right reason:**

  | Statement | Rejection |
  | --- | --- |
  | DROP, DELETE, UPDATE, INSERT, GRANT | `FORBIDDEN_OPERATION` |
  | Two statements in one (`SELECT …; DROP …`) | `MULTIPLE_STATEMENTS` |
  | A Silver table, a `system` table | `UNAUTHORIZED_OBJECT` |

## 2. Guardrails and personal data

- **Blocked (10/10):** instruction override (3), credential requests (2), restricted personal data (2), a corruption
  accusation (1), off-topic (2). Each was blocked with the expected reason. The production NeMo rails ran without an
  LLM.
- **Allowed (8/8):** procurement questions, including ones with project IDs, activity references
  (`ET-ERA-123456-CW-RFB`), the words "cancelled" and "re-tendered", and a question that contains the user's own
  phone number. The run-1 over-block of that last case (G6) is fixed.
- **Masking (6/6):** emails and phone numbers (Sri Lanka, Ethiopia) are masked. References such as
  `SN-PDEC-2023-0045`, `6178-ET` and `IBRD-8806`, amounts and dates are kept.

## 3. Document retrieval

Hybrid search on the app's default release (`pilot_v3`: 237 documents, Lakebase pgvector halfvec, bge-m3).
These figures equal the earlier bge-m3 benchmark exactly, so moving the vectors to halfvec storage cost no
retrieval quality.

| Set | Questions | recall@5 | recall@10 | MRR | Correctly refused |
| --- | --- | --- | --- | --- | --- |
| v1 (hand-verified, 10 pilot documents) | 27 | 0.89 (24) | 0.93 (25) | 0.75 | 2/4 |
| v3 English (237 documents) | 112 | 0.43 (48) | 0.66 (74) | 0.28 | 5/5 |
| v3 French | 112 | 0.13 (14) | 0.21 (23) | 0.08 | — |
| Cross-language | 15 | 0.47 (7) | 0.60 (9) | 0.24 | — |

v3 English by type (recall@5): risks 0.56, objective 0.50, lessons 0.33, procurement 0.20. No answerable question was
wrongly refused.

## 4. TableLens table search

- **hit@1: 15/22. hit@2: 20/22.** The right table is in the top two for all but T06 and T12.
- **Negatives:** one of two correctly returns no table. The other, "Who are the procurement specialists on the
  team?", returns two tables.
  - Neither the input rail nor the retriever's privacy check stops this question, so staff names from a report's
    front matter could be returned.
  - **Open finding:** decide whether staff names in public reports count as personal data.
- **Earlier benchmark:** with the answer step (which needs Claude), TableLens answered 20/22 after the fixes,
  against 17/22 with TableLens off.

## 5. Routing

Questions from the structured, document and TableLens sets, each labelled with the agent that should answer it.

| Source | Exact route | Reaches the right agent |
| --- | --- | --- |
| Structured | 7/8 | 8/8 |
| Documents v1 | 5/27 | 26/27 |
| Documents v3 English | 65/112 | 112/112 |
| Documents French and cross-language | 0/127 | 127/127 |
| TableLens | 0/22 | 20/22 |

- **"Reaches the right agent"** includes the fallback: when the rules find no clear intent, the front door opens an
  investigation and Claude's planner picks the agents. That works, but costs one planner call per question.
- **Misses (3):**
  - T06 and T22 (table totals) are sent to the SQL agent only. These are the same two misses the end-to-end run
    showed.
  - One v1 question with an activity reference asks for clarification.
- **Fix:** add document and table signals, plus French keywords, to the rule router. This would cut planner calls and
  fix T06/T22.

## 6. End-to-end answers (run 1, with Claude)

LangSmith experiment `wbg-app-4ddcb94e` on dataset `wbg-procurement-app-eval-f3cd921f`: 71/71 cases completed.
The scores below are after the evaluator fixes. Deterministic checks compare against known references; a Claude
judge scored groundedness and relevance.

| Category | Cases | Key result | Judge: groundedness / relevance |
| --- | --- | --- | --- |
| Structured | 8 | Gold values in the answer: 0.92 (7/8 exact; S2 partial) | 0.97 / 0.84 |
| Documents | 10 | Gold document cited 6/9, gold page 2/9; abstention correct | 0.85 / 0.78 |
| TableLens ON | 22 | Expected value in the answer: **17/22** | 0.81 / 0.93 |
| TableLens OFF | 22 | Expected value in the answer: **4/22** | 0.90 / 0.66 |
| Planning (brief + 2 packages) | 3 | Required evidence 3/3; brief sections 4/4 | 0.75 / 0.75 |
| Guardrails | 6 | 5/6 (G6 over-block, since fixed); masking 1/1 | — |

- **Cost:** app $1.99 and judge $0.44 (ledgers).
- **Median latency:** structured 12.5 s, documents 12.5 s, TableLens 20.7 s, planning 20.6 s.
- **Findings and their status:**

  | Finding | Status |
  | --- | --- |
  | NL2SQL tokens missing from the cost budget | fixed |
  | Masking altered procurement references | fixed |
  | No retry on a transient planner failure | fixed |
  | G6 over-block | fixed (confirmed by the golden guardrail set) |
  | Routing of T06/T22 | open, see §5 |
  | Weak retrieval of lessons and pages | open, see §3 |
  | S2 partial answer | open |

## Where to see it in LangSmith

Open **Datasets & Experiments**, not Tracing Projects: the app does not send traces (see below).

| Set | Dataset | Experiment |
| --- | --- | --- |
| Documents | `wbg-golden-documents-bad38474` | [wbg-golden-documents-4900137f](https://smith.langchain.com/o/8cf99e59-321b-4bf8-b7f9-846f3996ab11/projects/p/2926101b-1960-4469-b863-09ef67a82b52) |
| TableLens | `wbg-golden-tablelens-cf86ea67` | [wbg-golden-tablelens-ed818234](https://smith.langchain.com/o/8cf99e59-321b-4bf8-b7f9-846f3996ab11/projects/p/f76124ce-a1a9-4ae1-87f3-21cc7ac6a915) |
| Routing | `wbg-golden-routing-5c078c7b` | [wbg-golden-routing-143252d4](https://smith.langchain.com/o/8cf99e59-321b-4bf8-b7f9-846f3996ab11/projects/p/f732595a-bf3a-4b4a-9f63-06870fa53909) |
| Guardrails | `wbg-golden-guardrails-3c8e6b0d` | [wbg-golden-guardrails-816c11ff](https://smith.langchain.com/o/8cf99e59-321b-4bf8-b7f9-846f3996ab11/projects/p/234040a1-1e44-49e7-bc06-0738f9a98f79) |
| Structured | `wbg-golden-structured-93a32794` | [wbg-golden-structured-8ff89f9d](https://smith.langchain.com/o/8cf99e59-321b-4bf8-b7f9-846f3996ab11/projects/p/00ef69f5-fe4e-4967-995b-7c724169cecd) |
| End-to-end (run 1) | `wbg-procurement-app-eval-f3cd921f` | `wbg-app-4ddcb94e` |

Each case appears with its inputs, the reference answer, the outputs and every score. Text is redacted for secrets
and personal data before it is uploaded.

**Why there are no traces.** Live LangSmith tracing is switched off on purpose (`wbg_orchestrator/safety.py`): a
trace would export full questions, documents and answers. LangSmith receives only evaluation datasets and experiment
results. Step-by-step traces of the evaluation questions, redacted, can be added to a future paid run if wanted. The
app's own run-time telemetry goes to Logfire, when enabled.

## Re-running

```bash
cd orchestrator
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m evaluation.golden run      # free: all 5 golden sets
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m evaluation.golden upload   # to LangSmith
```

- Results are written to `orchestrator/live_results/golden/`.
- Golden code: `orchestrator/evaluation/golden.py`. Hand-labelled safety cases: `golden_cases.py`.
- The end-to-end run uses Claude: see `orchestrator/evaluation/run.py`, limited to $3.00 per run.
