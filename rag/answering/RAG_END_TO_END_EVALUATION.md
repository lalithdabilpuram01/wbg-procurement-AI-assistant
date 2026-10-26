# RAG End-to-End Evaluation (DEVELOPMENT benchmark)

**Benchmark:** `rag/retrieval/benchmark_v1.json` (v1.1): 31 questions, 27 answerable, 4 insufficient-evidence.

> **This is a development benchmark.** It was already used to build and tune retrieval, so these numbers are diagnostics only and do not show generalisation.

**Answer keys:** `rag/answering/answer_keys_dev.json`. Key facts with acceptable alternatives were written from the verified benchmark answers before any answer-generation run. One regex refinement was made before the first run, so that "Satisfactory" is not matched inside "Moderately Satisfactory".

## Runs

| Run | Content | Status |
|---|---|---|
| `rag/answering/runs/20261025T170358Z/` | evidence_only, llm_only, hybrid (first run) | evidence_only and llm_only are final. **The first hybrid run is superseded:** a validator bug applied prose checks to deterministic typed-record claims and removed correct claims |
| `rag/answering/runs/20261025T172710Z/` | hybrid, re-run after fixing that bug | Final hybrid result |

Every run directory holds `answers_<config>.jsonl`, `per_question.csv`, `summary.json` and `injection_fixture.json`. Console logs: `rag/answering/eval_console*.log`.

**Configurations** (all use the same evidence selection):
- **evidence_only:** no LLM. The "answer" is the top 3 selected excerpts, verbatim.
- **llm_only:** local `llama3.2:1b` (Ollama, localhost) for every question.
- **hybrid** (proposed design): deterministic typed-record answers for exact-reference questions, otherwise `llama3.2:1b`. All configurations pass through the same validation.

## Results

| Metric | evidence_only (no LLM) | llm_only (llama3.2:1b) | hybrid (typed route + llama3.2:1b) |
|---|---:|---:|---:|
| **Retrieval:** any gold evidence in the selected pack | 0.926 | 0.926 | 0.926 |
| **Retrieval:** all sources present, 3 multi-document questions | 3/3 | 3/3 | 3/3 |
| All key facts in the answer (27 answerable) | 17/27* | 1/27 | 8/27 |
| Mean key-fact recall | 0.673* | 0.056 | 0.315 |
| Status correct, answerable | 25/27 | 4/27 | 10/27 |
| **Abstention correct, 4 unanswerable** | 3/4 | 4/4 | 4/4 |
| False abstentions on answerable questions | 0 | 23 | 17 |
| Claims kept / removed by the validator | – | 21 / 28 | 38 / 17 |
| Grounded-claim rate (kept / all) | – | 0.43 | 0.69 |
| Kept claims with NLI entailment ≥ 0.5 | – | 0.66 | 0.22** |
| **Manual audit:** LLM answers shown that are acceptable | – | 0 of 4 | 0 of 2 |
| Citations resolving to real indexed units with matching page metadata | 31/31 | 31/31 | 31/31 |
| Both conflict sources reported, 3 multi-document questions | 3/3* | 0/3 | 0/3 |
| Typed plan-vs-contract conflict preserved (Q15) | – | no (generation failed) | **yes, both amounts with sources** |
| Privacy (no e-mail / phone in any answer; Q30 withheld) | 31/31 | 31/31 | 31/31 |
| LLM generation failures (invalid structured output after 2 attempts) | – | 18 | 13 |
| Median / p95 end-to-end latency | 0.1 s / 0.5 s | 32 s / 41 s | 7 s / 36 s |

\* Evidence-only scores are lenient: a key fact counts if it appears anywhere in about 1,200 characters of excerpts. That is not a synthesised answer, and conflicts are not labelled.
\*\* Typed-route claims are checked field by field, not by NLI, so the NLI share is low by construction.

**Hybrid key-fact recall by question type:**

| Type | Recall |
|---|---:|
| Plan activity | **1.0** |
| Contract lookup | **1.0** |
| Conflicting | 0.25 (Q15 typed conflict) |
| Dates and milestones | 0.25 |
| Table | 0.125 |
| Project fact | 0 |
| Narrative | 0 |

## What the numbers say

1. **The typed-record route works.** All 8 exact-reference questions (Q08–Q15) are answered with every key fact, cited to the plan row, with link status preserved:
   - Q14 is labelled `candidate`;
   - Q15 shows `conflicting_evidence` with US$2,408,752.45 (plan) vs US$3,535,555.66 (DS00005), unresolved.
2. **Abstention behaves correctly.**
   - All 4 unanswerable questions are handled: Q28 bidder count stops at the required-field check before generation; Q29 is out of scope; Q30 is withheld; Q31 is an unknown reference.
   - The validator turned every unverifiable LLM output into an abstention, rather than an unsupported answer in most cases.
3. **The available LLM is not usable.** `llama3.2:1b` was invoked for 19 hybrid questions:
   - 13 failed to produce valid structured output;
   - the rest produced claims that were removed, plus 2 answers that passed validation but are not acceptable on manual audit (Q06 vacuous header fragments; Q20 a correct number in the wrong role).
   - Evidence-only excerpts contain more correct facts than any LLM answer.
4. **Retrieval and evidence selection are not the bottleneck.** The gold evidence is in the pack for 25 of 27 answerable questions, and both sources for all 3 multi-document questions.
   - The Q27 Ethiopia ISR p7 gap is fixed by per-document coverage that takes the best fused, dense and BM25 unit. Disclosure: this design was informed by diagnosing Q27 on this benchmark.
5. **The validator has measurable gaps** (wrong-role values, vacuous claims), described in `RAG_GROUNDING_AND_GUARDRAIL_REPORT.md`. Its "grounded-claim rate" must not be read as factual accuracy.

## Proposed held-out benchmark (before scaling)

- **Size and authorship:** 50–60 questions over **new documents** from the 30-PDF scaling batch. Questions are written before any system output is seen, by a second reviewer, ideally a procurement SME (D-23).
- **Coverage:**
  - French (Senegal plans / ISRs), Spanish (Argentina) and Indonesian (English-language) documents;
  - multi-version plan streams;
  - field-role traps (planned vs actual dates, original vs revised vs cancelled amounts);
  - at least 8 conflict pairs;
  - at least 8 insufficient-evidence questions (missing fields, out-of-scope, unknown references);
  - 2 synthetic injection fixtures.
- **Protocol:** answer keys with field roles; frozen and hashed; scored once per release. The current 31 questions stay as the development set.
