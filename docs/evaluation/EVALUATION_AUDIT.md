# Evaluation audit

WBG Procurement Intelligence Platform: every evaluation, validation and benchmark in the repository, what each one
proves, how far it can be trusted, and its results. Audited 2026-10-28. Detailed results of the newest evaluations
are in [RESULTS.md](RESULTS.md).

## Contents

1. [Verdict](#1-verdict)
2. [Inventory](#2-inventory)
3. [Data platform (Bronze, Silver, Gold)](#3-data-platform)
4. [ML models (Module 2)](#4-ml-models)
5. [Document intelligence (RAG, PDF tables, TableLens)](#5-document-intelligence)
6. [Structured Data agent and MCP](#6-structured-data-agent-and-mcp)
7. [Orchestrator and the end-to-end application](#7-orchestrator-and-the-application)
8. [Safety: guardrails, personal data, SQL](#8-safety)
9. [Automated test suites](#9-automated-test-suites)
10. [Cross-cutting findings](#10-cross-cutting-findings)
11. [Gaps and recommended next evaluations](#11-gaps-and-recommended-next-evaluations)

## 1. Verdict

| Area | Evidence | Strongest result | Main limitation |
| --- | --- | --- | --- |
| Gold data | **Strong** | Every Gold table independently recomputed: 0 mismatches; 148 post-write checks | One verifier bug found and fixed (data was correct) |
| Safety (guardrails, SQL, personal data) | **Strong** | 18/18 inputs, 16/16 SQL statements, 6/6 masking cases (golden, no LLM) | Hand-labelled set is small; one staff-names gap |
| Model 1, tender duration (real labels) | **Good, honestly reported** | Frozen test, 616 rows: Ridge MAE 76.7 d, P10–P90 coverage 0.84, long-tender ROC-AUC 0.905 | No better than the median baseline on MAE (−3.1%) |
| Models 2 and 3, amendment and re-bid (synthetic labels) | **Demonstration only** | Recover the assumed generator; pass null controls | The labels are invented, so the metrics say nothing about real risk |
| Document retrieval | **Moderate** | Hand-verified set: recall@5 0.89 | At 237 documents 0.43; French 0.13; most gold is heading-derived; no held-out set |
| TableLens | **Good** | Value returned 20/22 vs 17/22; app answers 17/22 vs 4/22 | 22 questions, all from the 10 pilot PDFs |
| Structured answers (NL2SQL with Claude) | **Thin** | 7/8 exact values end to end | Only 8 questions; generated-SQL accuracy was unmeasured before the end-to-end run |
| Full application | **Moderate** | 71/71 cases completed; groundedness mostly 0.75–1.0 (judge) | One run; judge is an LLM; 4 findings fixed but not re-measured with Claude |
| Routing | **Measured, weak** | 99% of questions reach the right agent | Only 26% are routed directly; the rest need a planner call |
| Operations (cost, latency, resilience) | **Partial** | Spend ledgers on every Claude call; restart and recovery tested | No systematic latency or cost report across runs |

**Bottom line:**
- The data layer and the safety controls are the best-evidenced parts of the system.
- Model 1 and TableLens are credible, with honest caveats.
- Document retrieval at scale, generated SQL, and anything resting on synthetic labels need more evidence before
  stronger claims are made.

## 2. Inventory

36 evaluations, validations and benchmarks.
- **Status:** **current** = describes the system as it is; **superseded** = replaced by a later run or release;
  **historical** = describes a step that is finished.
- **Method:** "No" means deterministic, with no language model involved.

| # | Evaluation | Area | Date | LLM? | Labels | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Gold independent verification (5 tables) | Data | 10-24 | No | Recomputed from Silver | current |
| 2 | Gold KPI views verification (7 views) | Data | 10-24 | No | Recomputed | current |
| 3 | Reference data validation (WDI, OGHIST) | Data | 10-22 | No | API totals | current |
| 4 | ML label feasibility audit | Data/ML | 10-23 | No | Pattern match | historical |
| 5 | Model 1 split and leakage audit | ML | 10-25 | No | Real | current |
| 6 | Model 1 baselines (validation) | ML | 10-25 | No | Real | historical |
| 7 | Model 1 model selection (validation) | ML | 10-25 | No | Real | historical |
| 8 | **Model 1 frozen test** | ML | 10-25 | No | Real | current |
| 9 | Models 2/3 leakage, ceiling, recovery | ML | 10-25 | No | Synthetic | current |
| 10 | **Models 2/3 frozen test** | ML | 10-25 | No | Synthetic | current |
| 11 | PDF extraction validation (pilot_v1) | Documents | 10-25 | No | Source spans | superseded |
| 12 | PDF extraction validation (pilot_v2) | Documents | 10-25 | No | Source spans | current |
| 13 | PDF table quality audit | Documents | 10-26 | No | Automated + 12 manual | current |
| 14 | P1 table-fix validation (shadow) | Documents | 10-26 | No | Byte comparison | current, not merged |
| 15 | Retrieval benchmark v1.1 (pilot_v2) | Documents | 10-25 | No | 31 hand-verified | superseded by release |
| 16 | Release pilot_v2_1 comparison | Documents | 10-26 | No | v1.1 | superseded by release |
| 17 | Release pilot_v3 evaluation (gte) | Documents | 10-26 | No | 117 heading-derived | superseded by bge-m3 |
| 18 | Multilingual: gte vs bge-m3 | Documents | 10-26 | No | v1, v3, French, cross | current |
| 19 | Answer quality (llama 1B, evidence-only) | Documents | 10-25 | Local LLM | 27 dev keys | superseded by Claude responder |
| 20 | Embedding cache parity (6B–6D) | Documents | 10-26 | No | Byte identity | current |
| 21 | Lakebase PDF live validation | Documents | 10-26 | No | 6 queries, 39 vectors | historical |
| 22 | RAG version integration (6E) | Documents | 10-26 | No | v1.1 | current |
| 23 | **TableLens benchmark: find** | Documents | 10-27 | No | 22 hand-checked | current |
| 24 | **TableLens benchmark: answer** | Documents | 10-27 | Claude | 22 hand-checked | current |
| 25 | Structured agent scenario evaluation | Structured | 10-25 | No | 15 reviewed scenarios | current |
| 26 | MCP transport tests | Structured | 10-25 | No | 41 tests | current |
| 27 | Registry evaluation | Structured | 10-25 | No | 15 scenarios | current |
| 28 | LangGraph Milestone A (offline) | Orchestrator | 10-26 | Fake | Acceptance items | historical |
| 29 | LangGraph Milestone B (live) | Orchestrator | 10-26 | Claude | Smoke | historical |
| 30 | C4 end to end, E package, G planning brief (live) | Orchestrator | 10-27 | Claude | Single scenarios | historical |
| 31 | MCP verification (no LLM) | Orchestrator | 10-27 | No | In-process vs MCP | current |
| 32 | **LangSmith end-to-end run 1** | Application | 10-27 | Claude + judge | 71 cases | current |
| 33 | LangSmith run 2 | Application | 10-27 | Claude | 71 cases | **unfinished, no results** |
| 34 | **Golden sets (5)** | Application | 10-28 | No | 643 scored cases | current |
| 35 | Feedback-to-evaluation loop | Module 4 | 10-27 | No | Reviewer-approved | built, no cases yet |
| 36 | Automated test suites (17) | All | 10-28 | No | — | current |

## 3. Data platform

**Gold independent verification (#1, #2).** Each Gold table was rebuilt in plain Python from Silver and compared
field by field, read-only against Databricks. The checks also confirmed that Bronze, Silver and Gold versions,
grants, masks and the cohort hash were unchanged.

| Table | Rows | Dry run | Post-write |
| --- | --- | --- | --- |
| gold_project_portfolio | 2,000 | 46/46 | 58/58 |
| gold_contract_analytics | 178,921 | 18/18 | 24/24 |
| gold_procurement_notices | 243,533 | 18/18 | 23/23 (51 derived columns, 0 mismatches) |
| gold_country_context | 5,642 | 14/14 | 19/19 |
| gold_financing_analytics | 2,685 | 19/19 | 24/24 |
| 7 KPI views | 16,310 | 29/29 | 53/53 (0 mismatches) |

- **Audit note:** the first country_context run failed on 40 columns. The fault was in the verifier (a tuple
  unpacking bug), not the data. It was fixed and re-run, and the failed attempt is kept on disk, which is good
  practice.
- **Reference data (#3):** WDI counts match the API totals (6,916 per indicator), with 0 duplicate country-years and
  266/266 entities resolved. 24 economies have no CPI values.
- **Label feasibility (#4):** 0 verified amendment or re-bid labels exist in public data. This is why Models 2 and 3
  use synthetic labels.

## 4. ML models

### Model 1: tender duration (real labels)

**Split and leakage audit (#5): 10/10 checks pass.**
- Split: chronological, train 646 / validation 353 / test 616.
- Leakage checks: 0 packages cross splits, and prior-tender counts were recomputed with 0 mismatches.

**Frozen test (#8):** 616 rows, 59 projects, scored once on 2026-10-25.

| Metric | B1 median baseline | **Ridge (selected)** | Random forest |
| --- | --- | --- | --- |
| MAE (days) | 74.3 | 76.7 | 73.5 |
| RMSE (days) | 108.0 | 106.5 | 105.1 |
| P10–P90 interval coverage | — | 0.84 | 0.77 |
| Long tender (>365 d): ROC-AUC | 0.768 | 0.905 | 0.919 |
| Long tender: PR-AUC | 0.373 | 0.537 | 0.557 |

Audit findings:
- **Ridge does not beat the median baseline on MAE** (−3.1%, CI −11.8 to 12.4), and no pairwise MAE difference is
  significant. Its value is calibrated intervals (0.84 against a 0.80 target) and detecting long tenders.
- **Long tenders are under-predicted by roughly 190–210 days.** The duration estimate is weakest exactly where it
  matters most.
- **The test was not fully blind.** The test median (141.5 d) was printed in an earlier phase, and the reports
  disclose this.
  - The config's `frozen_at` timestamp (08:20) is later than `scored_at` (08:15).
  - That ordering should be explained or corrected before the frozen claim is relied on.
- **Concentration:** 425 of 616 test rows come from projects seen in training, and 5 projects hold 52.6% of test
  rows.

### Models 2 and 3: amendment and re-bid risk (synthetic labels)

| | Model 2 (amendment) | Model 3 (re-bid) |
| --- | --- | --- |
| Test rows | 28,949 | 20,954 |
| PR-AUC (scenario S2) | 0.232 | 0.334 |
| ROC-AUC | 0.655 | 0.735 |
| Brier (vs constant) | 0.114 (0.119) | 0.065 (0.075) |
| Null control S0 ROC-AUC | 0.504 (passes) | 0.501 (passes) |

Audit findings:
- **These metrics measure how well the model recovers the authors' own assumed generator, not real risk.** The
  reports say this, and the app labels these outputs **Synthetic**.
- The null controls and the label-shuffle test (ROC-AUC 0.500) show the pipeline does not leak.
- Model 2's alert rate of **31.2% is above the 30% cap** in its own design.
- Model 3's High band holds 15% of rows in the null scenario against a 10% design.

## 5. Document intelligence

### Retrieval

Which numbers describe today's app? The app runs pilot_v3 with bge-m3 vectors on Lakebase (halfvec). The golden
run of 2026-10-28 reproduced the bge-m3 benchmark exactly:

| Question set | Gold quality | recall@5 | recall@10 | MRR |
| --- | --- | --- | --- | --- |
| v1 (27 answerable) | Hand-verified | **0.89** | 0.93 | 0.75 |
| v3 English (112) | Heading-derived | **0.43** | 0.66 | 0.28 |
| v3 French (112) | Heading-derived, templated | **0.13** | 0.21 | 0.08 |
| Cross-language (15) | Hand-written | **0.47** | 0.60 | 0.24 |

Audit findings:
- **No held-out set.** The 31 v1 questions were used to tune retrieval and are still the main hand-verified measure,
  so 0.89 is optimistic.
- **Most gold is heading-derived** (229 of 275 questions). These numbers measure "did we retrieve the section whose
  heading matches", not answer correctness.
- **The larger corpus hurt the small set.** On pilot_v3 with gte, v1 MRR fell from 0.777 to 0.629 and abstention
  from 3/4 to 2/4. bge-m3 recovered v1 (0.75).
- **Model choice was a trade-off.** bge-m3 is better on v1 and cross-language, but worse on v3 recall@5
  (0.43 vs 0.47) and on French (0.13 vs 0.21).
- **The re-ranker made v3 worse** (MRR 0.335 → 0.205), so it is correctly off.
- **Retrieval by section type** (v3 recall@5): procurement 0.20, lessons 0.33. These are the weakest and the most
  relevant to procurement planning.

### Answer quality before Claude (#19, superseded)

- Evidence-only answers contained all key facts in 17/27 cases. The local llama 1B model did far worse
  (1/27 and 8/27).
- That generator is no longer used; Claude writes the answers now.
- One discrepancy is left in the report: it says 19 hybrid questions were sent to llama, while the summary file
  says 23.

### PDF tables

**Quality audit (#13):** 220 tables, 3,347 rows on pilot_v2.
- Verdicts: **136 unusable, 81 needs review, 3 verified**.
- Values themselves are accurate: 3,554/3,580 tokens match exactly, plan amounts 367/367, stage dates 1,547/1,547.
- **The structure is the problem**, not the values.
- The automated verdicts were tuned on the same 12-table manual sample they were checked against (8/12 → 9/12), so
  agreement is not an independent estimate.

**P1 fixes (#14):** 18 glued-value cells fixed, with 3,331 untouched rows byte-identical. These sit in a shadow path
and are **not merged**.

**Parity checks (#20, #22):** embedding cache and release switch.
- Vectors are bit-identical, and 125/125 searches return identical results.
- These prove nothing changed when nothing should have; they are not quality measures.

### TableLens

| Benchmark | Measure | ON | OFF |
| --- | --- | --- | --- |
| find (no LLM) | Right table in top 2 / top 1 | 20/22 / 15/22 | — |
| answer (Claude writes SQL) | Expected value in returned rows / top-10 passages | **20/22** | 17/22 (upper bound) |
| App end to end (run 1) | Expected value in the final answer | **17/22** | 4/22 |

Audit findings:
- **Strong effect, small sample.** All 22 questions come from the 10 pilot PDFs that TableLens was built and tuned
  on.
- **The benchmark and the app measure different things.** The benchmark checks rows returned, the app checks the
  answer text. Both should be quoted with their definitions.
- **An earlier answer run was overwritten.** Two ledgers exist, 47 and 46 attempts, about $0.20 each, but only one
  result file.

## 6. Structured Data agent and MCP

| Evaluation | Result |
| --- | --- |
| Scenario evaluation (#25) | **15/15** reviewed scenarios. A naive join was rejected (it would have inflated counts ×2.56); 6/6 destructive statements rejected; 169 audit events with no sensitive content |
| MCP transport (#26) | **41/41** over real stdio (18 protocol + 23 security). The first run was 40/41: a contract number leaked into the audit log via a test request id. The test was fixed and re-run |
| Registry (#27) | 30/30 unique grains, 21/21 relationships; expected object in the top 3 for 15/15 |
| MCP vs in-process (#31) | 8/8 reference queries identical; 5/5 agent evidence sets identical; recovery after a server kill in 4.4 s |
| Golden: reference values and validator | 8/8 values unchanged; 16/16 validator decisions with the correct reasons |

Audit findings:
- **SQL generated by an LLM was not evaluated in #25.** The scenarios used reviewed templates. The only measure of
  Claude-written SQL is the 8 structured cases of run 1 (7/8 exact, S2 partial), which is too few to quote an
  accuracy.
- Scenario runs used developer owner credentials (`production_read_only = false`). The app now runs with the
  read-only principal over MCP, and the golden structured set re-ran the reference queries that way.

## 7. Orchestrator and the application

**Live milestones (#29, #30)** proved that the pieces work together:
- Claude via Portkey, Lakebase checkpoints (9/9), restart recovery, a package assessment and a planning brief;
- costs $0.01–0.29 per scenario.

Each was a single scenario. Most ended `partial` (C4, E and G), which is honest but shows the planner's evidence
requirements were often not fully met.

**LangSmith end-to-end run 1 (#32):** 71/71 cases completed. Scores are rescored after evaluator fixes.

| Category | Deterministic result | Judge: groundedness / relevance |
| --- | --- | --- |
| Structured (8) | Gold values in answer 0.92 | 0.97 / 0.84 |
| Documents (10) | Gold document cited 6/9; gold page 2/9 | 0.85 / 0.78 |
| TableLens ON / OFF (22 each) | 17/22 vs 4/22 | 0.81 / 0.93 vs 0.90 / 0.66 |
| Planning (3) | Required evidence 3/3 | 0.75 / 0.75 |
| Guardrails (6) | 5/6 (G6 over-block, since fixed) | — |

Audit findings:
- **Single run, LLM judge.** Groundedness is not correctness. The judge saw evidence cut to 600 characters, so it
  under-credited some answers; that is fixed for the next run.
- **Cost accounting gap, now fixed.** $0.98 of the $1.99 app spend did not appear in per-case usage (NL2SQL tokens).
  The code is fixed but not re-measured.
- **Run 2 (#33) spent about $1.29** (181 Claude calls) and produced **no results file**. It should be either
  finished or recorded as abandoned.
- **Golden sets (#34), no LLM, 643 scored cases** (routing re-scores the retrieval questions): see [RESULTS.md](RESULTS.md).
  - Routing reaches the right agent in 99% of cases, but only 26% directly. Most document questions, and all French
    ones, fall back to the planner.
- **Feedback loop (#35):** reviewer-approved corrections become evaluation cases automatically. No cases exist yet
  because the app has had no users.

## 8. Safety

| Control | Evidence | Result |
| --- | --- | --- |
| Input guardrails (NeMo, no LLM) | Golden, 18 hand-labelled | 18/18 (10 blocked with the right reason, 8 allowed) |
| Read-only SQL validator | Golden, 16 statements; scenario #25 | 16/16 correct reasons; 6/6 destructive statements rejected |
| Personal-data masking | Golden, 6 texts; run 1 | 6/6; references, amounts and dates kept |
| Audit logs free of sensitive content | #25, MCP tests, Milestone B | 169 events clean; 59 spans, 0 leaks |
| Tracing | Design review | LangSmith tracing off by design; only redacted evaluation results leave the machine |

**Open finding:** "Who are the procurement specialists on the team…" is not stopped by the input rail or the
retrieval privacy check, and TableLens finds two staff tables for it. Decide whether staff names in public reports
count as personal data.

## 9. Automated test suites

1,487 offline tests across 17 suites, all passing on 2026-10-28. Five suites need the base Python environment
(`pdfplumber`, `openpyxl`, cache provenance); they pass there.

| Suite | Tests | Suite | Tests |
| --- | --- | --- | --- |
| structured_agent/tests | 290 | rag/ingestion_p1/tests | 65 |
| orchestrator/tests | 250 | silver/tests | 66 |
| conversation/tests | 236 | gold/tests | 46 |
| discovery/tests | 129 | rag/ingestion/tests | 45 |
| rag/tests | 102 | infrastructure/lakebase_poc/tests | 41 |
| rag/embedding_cache/tests | 101 | rag/release_v2_1/tests | 30 |
| rag/tablelens/tests | 25 | ml/tests | 23 |
| ml/serving/tests | 16 | audits/pdf_table_quality/tests | 12 |
| rag/vector_store/tests | 10 | | |

**Not run in this audit**, because they call live services or spend money: `structured_agent/tests_mcp` (last run
41/41), `tests_live` and `tests_workflow_mcp`; `orchestrator/tests_live`; `infrastructure/lakebase_poc/tests_live`.

**Finding:** the repository has no single command or environment that runs every suite. Five suites fail to load, or
fail, in the orchestrator environment.

## 10. Cross-cutting findings

1. **There are no held-out evaluation sets** for retrieval, TableLens or the application. Every benchmark was also
   used during development, so all of these results are optimistic.
2. **Most document gold is generated, not verified**: 229 of 275 retrieval questions. Hand-verify a sample, e.g.
   30 questions, to measure how far heading-derived gold can be trusted.
3. **Sample sizes are small** wherever Claude is involved: 8 structured questions, 22 table questions, 10 document
   questions, 3 planning cases, and one run of each. No result has a confidence interval except the ML tests.
4. **Synthetic labels must stay labelled.** Models 2 and 3 are demonstrations. Their metrics must never be presented
   as real risk-prediction accuracy.
5. **"Frozen test" claims need care.** Both ML configs carry timestamps that do not line up cleanly with their
   scoring times, and both tests were partly unblinded. The reports disclose the unblinding; the timestamps should be
   reconciled.
6. **Evaluation spend is not fully accounted for.** Run 2 ($1.29) and an overwritten TableLens answer run (about
   $0.20) left no usable results. Every paid run should either write results or be logged as abandoned.
7. **Parity checks are not quality checks.** Many reports (cache, release switch, MCP vs in-process) prove that
   behaviour did not change. That is valuable, but it should not be read as evidence of accuracy.
8. **Discrepancies to fix in the reports:**
   - the llama hybrid count (19 vs 23);
   - the ML `frozen_at` vs `scored_at` timestamps;
   - the TableLens definitions (rows returned vs answer text);
   - the stale README line that says TableLens is "disabled until it ships".

## 11. Gaps and recommended next evaluations

| Priority | Evaluation | Why | Cost |
| --- | --- | --- | --- |
| 1 | **Held-out sets**: 30 new hand-written document questions and 10 table questions, not used for tuning | Gives honest retrieval and TableLens numbers | Free to run; about a day of labelling |
| 2 | **NL2SQL accuracy**: 30–40 questions with reference SQL, scored on result equality | Generated-SQL accuracy is the least-measured capability | About $1–2 with Claude |
| 3 | **Router fix, then golden routing re-run**: add document, table and French signals | Cuts planner calls; fixes T06/T22 | Free |
| 4 | **Repeat the end-to-end run** after the four fixes, with a results file guaranteed | Confirms the fixes; gives run-to-run variance | About $2.50 |
| 5 | **Verify a sample of heading-derived gold** (30 questions) | Shows how much of the 229-question gold can be trusted | Free; about half a day |
| 6 | **Operations report** from the ledgers and audit logs: latency, cost per question, failures, retries | Operationalisation is 15% of the assessment score | Free |
| 7 | **Single test command** covering all 17 offline suites in one environment | Reproducibility | Free |
| 8 | **Decide on staff names**, then add the case to the golden guardrails | Closes the open privacy finding | Free |

Items 1, 3, 5, 6, 7 and 8 need no Claude spend.
