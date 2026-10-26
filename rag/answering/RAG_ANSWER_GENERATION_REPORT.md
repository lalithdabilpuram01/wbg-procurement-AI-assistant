# RAG Grounded Answer Generation: MVP Report (10-PDF pilot)

**Date:** 2026-10-25 · **Status:** the answer-generation MVP is built and evaluated. **An inference-runtime dependency needs approval.** The only available model (`llama3.2:1b`) is not adequate.

## 1. Inference environment (checked before implementation)

| Item | Finding |
|---|---|
| Local LLM runtime | **Ollama 0.40.7 running on localhost:11434** with one model, `llama3.2:1b` (1.2B parameters, Q8_0, 128k context, JSON-schema structured output supported). Used as-is; nothing downloaded or installed |
| External providers | No API keys configured (Anthropic, OpenAI, Azure, Google, Mistral). The `anthropic` and `openai` SDKs are installed but unused. **No external calls were made** |
| Other local models | `roberta-large-mnli` (claim entailment), gte-base (retrieval), ms-marco cross-encoder; all from the local HF cache, offline |
| Hardware | Apple M3, 16 GB RAM, MPS available (NLI runs on MPS) |
| Latency (1B model) | About 3–5 s per generation, up to about 30 s when the model loops until the token cap; 13 of 19 hybrid calls failed to return valid JSON |

## 2. Implementation (`rag/answering/`)

| Module | Role |
|---|---|
| `query.py` | Query understanding: comparison cues, document-type hints, required-field patterns (bidder count, bid price) |
| `evidence.py` | Guarded hybrid retrieval (reuses `rag/retrieval/core.py`: strategy C, table windows, typed plan rows, scope / privacy / exact-reference guards). Evidence selection: exact typed records first; per-document-type coverage for comparisons; document diversity (at most 3 per document); de-duplication; at most 8 units. Strategy-C parent context. Injection quarantine; PDF page check |
| `generator.py` | `OllamaGenerator` (local only; JSON schema; temperature 0; capped output; fails closed). `TypedRecordAnswerer` (**deterministic, labelled "no LLM"**) for exact-reference questions. `EvidenceOnlyGenerator` (**no LLM**, verbatim excerpts; baseline) |
| `validate.py` | Deterministic claim validation (IDs, numbers, dates, references, quotes, privacy, shape) + local NLI entailment; typed-field validation for typed claims; typed conflict extraction; contract-link status notes |
| `pipeline.py` | Stages 1–7 → `AnswerResponse`. Citations are built only from index metadata. The answer text is rebuilt from validated claims |
| `schemas.py`, `schemas/*.schema.json` | `DraftAnswer` (generator contract), `AnswerResponse`, `Citation` |
| `evaluate_answers.py`, `answer_keys_dev.json` | End-to-end evaluation (see `RAG_END_TO_END_EVALUATION.md`) |
| `run_demos.py`, `demo_outputs/` | The 12 required demonstration cases (`DEMO_SUMMARY.md` + one JSON per case) |

**Response contract:**
- `status`: answered / partial / insufficient_evidence / conflicting_evidence / withheld.
- `answer`, `citations` (document, title, project, type, date, version, physical page, printed page if detected, section or table, excerpt or plan row, URL, association level, page check, contract links), `supporting_evidence_ids`, `conflicts`, `limitations`.
- Kept and removed claims, with their checks.
- The generator name and whether it is an LLM; timings and a trace.
- Guard-stopped responses are labelled "none (stopped before generation)".

## 3. Results summary

*(Development benchmark; details in `RAG_END_TO_END_EVALUATION.md`.)*

**What works:**
- **Typed-record answers:** 8 of 8 exact-reference questions correct and cited, link status kept; the Q15 plan-vs-contract amount conflict is shown unresolved.
- **Abstention:** 4 of 4 insufficient-evidence questions correct; Q28 bidder count is refused at the required-field check, so nothing is inferred.
- **Safety:** privacy 31/31; synthetic injection fixtures quarantined and never cited.
- **Evidence coverage:** 25 of 27 answerable questions have gold evidence in the pack, and all 3 multi-document questions have every source (the Q27 ISR gap is fixed).

**What does not:**
- `llama3.2:1b` produced **no acceptable generated answer**. Of its outputs, 13 were invalid JSON; the rest were removed by validation, or were 2 wrong or vacuous answers the validator missed.
- Narrative, table, project-fact and conflict questions (Q16, Q26, Q27) are therefore unanswered (abstained) in the hybrid configuration.

## 4. Demonstrations (`rag/answering/demo_outputs/`)

| # | Case | Result |
|---|---|---|
| 1 | Project development objective | Abstained (LLM output failed validation) |
| 2 | Exact plan reference | **Answered** (typed route): method, market approach, status; the candidate contract link is labelled |
| 3 | Typed amounts | **Answered** (typed route): estimated and actual amounts; the `amount_verified` link is shown |
| 4 | Table financial (ICR Annex 3) | Abstained (LLM claims removed) |
| 5 | Narrative lesson | Abstained |
| 6 | PAD vs ISR financing | Abstained (both sources were in the evidence; the LLM did not produce a valid answer) |
| 7 | ICR vs IEG ICR Review | Abstained (both sources in the evidence) |
| 8 | Unknown activity | `insufficient_evidence` (no record; stopped before generation) |
| 9 | Missing bidder count | `insufficient_evidence` (required field absent; stopped before generation) |
| 10 | Out-of-corpus country | `insufficient_evidence` (scope guard) |
| 11 | Contact details | `withheld` |
| 12 | Injection fixture | Both fixtures quarantined, not cited, no payload in the output |

## 5. Approval needed: inference runtime (choose one)

| Option | What it needs | Data handling | Expected effect |
|---|---|---|---|
| **A. Larger local model via the existing Ollama** (recommended) | Download one instruct model of about 4–5 GB, e.g. `qwen2.5:7b-instruct` or `llama3.1:8b`, which fits in 16 GB. **Needs approval for the download** | Nothing leaves the machine | Reliable JSON and much better extraction; about 10–30 s per answer on the M3 |
| B. Approved external API (e.g. Claude) | API key + data-governance approval for sending **sanitised** evidence excerpts (never `restricted/`) | Sanitised excerpts leave the machine | Best quality and latency; requires a policy decision |

## 6. Recommendation and next actions

1. **Approve a runtime** (option A), then re-run the unchanged evaluation (`evaluate_answers.py`) to measure the generator on the same development benchmark.
2. **Before trusting any LLM output, add:** the field-role check (values bound to their column or field label), the informativeness check, and deterministic extraction of rating and amount pairs for comparison questions.
3. **Build the held-out benchmark** (50–60 new questions, new documents and languages; `RAG_END_TO_END_EVALUATION.md` §"Proposed held-out benchmark").
4. **30-PDF scaling gate:**
   - **Retrieval, evidence and validation layers:** ready (guards, coverage and typed answers work).
   - **Generated answers:** not ready, pending steps 1–2.

   Recommended order: runtime approval → re-evaluate → held-out benchmark on the 30-PDF batch → scale.
