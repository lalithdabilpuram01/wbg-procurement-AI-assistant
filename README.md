# WBG Procurement Intelligence

An agentic AI analyst for World Bank procurement. Ask in plain language; agents query the Gold data, read project
reports, run prediction models and return one answer in which every statement cites its source.

![WBG Procurement Intelligence architecture](docs/architecture/architecture.svg)

The numbers on the arrows are the five workflows: (1) ask a question, (2) follow-up chat, (3) upload data and the
dashboard updates, (4) upload a report and chat can cite it, (5) feedback that guides later answers.
A PNG copy for slides is at [docs/architecture/architecture.png](docs/architecture/architecture.png).

| Read next | What it covers |
|---|---|
| [HOW_TO_RUN.md](HOW_TO_RUN.md) | Start the app (`uv run wbg-api`, `uv run wbg-web`) and the commands |
| [etl/README.md](etl/README.md) | The Lakeflow ingestion pipelines and the live demo script |
| [docs/architecture/DATA_MODELING.md](docs/architecture/DATA_MODELING.md) | How the data was modelled: Bronze, Silver, Gold, documents, graph |
| [docs/evaluation/RESULTS.md](docs/evaluation/RESULTS.md) | Evaluation results |
| [demo/DEMO_QUESTIONS.pdf](demo/DEMO_QUESTIONS.pdf) | Questions to ask during a demo, with the expected answers |

## How the code is organised

Every top-level folder is one working component; tests are kept apart in `tests/`, and code that the app no longer
uses is kept for the record in `research/`.

```text
procurement-intelligence/
  orchestrator/wbg_orchestrator/   the app: web API, background jobs, the LangGraph investigation engine
    api.py                         HTTP endpoints, sign-in, request ids
    app_service.py                 background jobs (one per message), ownership checks
    conversation_bridge.py         the front door: which route a message takes
    graph.py                       the investigation steps: intake > plan > execute > verify > respond
    controller.py                  runs each task with retries, deadlines and circuit breakers
    workers/                       one adapter per agent (structured data, documents, prediction, monitoring, ...)
    observability.py, logs.py      Logfire spans and the JSON log file (see "Debugging")
    state_view.py, log_view.py     the state summary logged after every step; `wbg-logs`, which reads the log as a tree
    accounts.py, accounts_db.py    user accounts: sign-up, sign-in, recovery codes, roles (Lakebase wbg_auth); `wbg-user`
    runtime.py                     wires the real services together at start-up
  conversation/        chat memory, intent routing, personal-data masking, feedback learning
  structured_agent/    questions over the Gold tables: question > SQL (Claude) > validation > Databricks; MCP server
  rag/                 documents: parsing, hybrid search (bge-m3 + BM25), cited answers, TableLens, pgvector store
  ml/serving/          the three frozen prediction models (ml/final*/frozen/ holds the model files)
  monitoring/          Execution & Monitoring: milestones and early warnings per project
  tender/              Tender Design: a draft checked against history
  lessons/             Continuous Learning: lesson cards from completion reports and reviews
  ingest/              laptop drop-folder ingestion into the knowledge graph
  etl/, silver/, gold/, resources/, databricks.yml   the Databricks Lakeflow jobs (Bronze > Silver > Gold > KPIs)
  wbdisc/              World Bank data helpers shared by the ETL and RAG (reference normalisation, plan parsing)
  app_config/          paths.py (every data file the code reads), dashboards.json, the settings check
  data/                input files: cohort, Bronze extracts and their manifests, the RAG scope
  infrastructure/lakebase_poc/   Lakebase connection settings and credentials (its own small uv project)
  frontend/            the Next.js web app
  tests/               all tests, one folder per component; tests/live/ only with WBG_RUN_LIVE_TESTS=1
  research/            not used by the app: discovery, experiments, earlier releases (see research/README.md)
  docs/                architecture, decisions, reports, evaluation results
```

## Follow one question through the code

1. `api.py` `POST /conversations/{id}/messages` starts a job and returns its `job_id` at once.
2. `app_service.py` `_guarded` runs the job in a worker thread; this is the root span `job.message`.
3. `conversation_bridge.py` `handle_message` routes it (`conversation.message`): small talk and follow-ups are
   answered from the chat history; questions about data or documents open an investigation.
4. `graph.py` runs the investigation: `intake` (input guardrail), `supervise` and `plan` (a fixed plan or Claude's),
   `execute` (each task through `controller.py` and a worker), `verify` (every claim needs evidence), `respond`.
5. The web app polls `GET /jobs/{job_id}` (live steps come from `GET /jobs/{job_id}/events`) and then reads the
   investigation.

## Debugging

Each job writes JSON lines to `orchestrator/runs/logs/app.jsonl`. When Logfire export is switched on, it also sends
one nested trace to Logfire. Every line carries the job's ids and the step it was written in, with that step's full
path. Every graph step logs what it changed and the state after it, and a failure is logged once with its step, file
and line. `uv run wbg-logs` prints the latest job as an indented tree; `--failed` shows the latest failed one. See
[HOW_TO_RUN.md](HOW_TO_RUN.md), section 9.

Phase 1 discovery notes (history) are in [research/discovery/PHASE1_README.md](research/discovery/PHASE1_README.md).
