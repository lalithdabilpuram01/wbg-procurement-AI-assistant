# WBG Procurement Intelligence: Project Overview

*The official guide to what this project is, what it does, how it works and where everything lives.*
*It is written in plain English. It is for anyone who reads it, technical or not.*

| | |
|---|---|
| Status | Working proof of concept (demo-ready) |
| Last updated | 2026-11-02 |
| Code | `procurement-intelligence/` (this repository) |
| How to start it | [HOW_TO_RUN.md](../HOW_TO_RUN.md) |

---

## 1. What this project is

WBG Procurement Intelligence is an **AI assistant for World Bank procurement**.

You ask it a question in everyday language, such as *"How many contracts were awarded in Ethiopia, by procurement
method?"* or *"What went wrong with road works tenders in Sri Lanka?"*. The assistant works out where the answer is,
looks it up, and gives you one clear answer. **Every statement in that answer shows its source**: the table row, the
page of the report, or the prediction model it came from. You can check anything it tells you.

It does more than answer questions. It also:
- checks a draft tender against what happened in similar past tenders;
- predicts how long a tender will take and how risky it is;
- watches running projects for delays;
- drafts a planning brief for a new project;
- learns from the lessons in past completion reports.

## 2. The problem it solves

Procurement staff and analysts have to bring together information that sits in many different places:
- tables of projects, contracts, notices and loans;
- long PDF reports, such as appraisal documents, progress reports and completion reports;
- the experience of past projects.

Finding a reliable answer can take hours of searching and cross-checking. General AI chatbots are fast, but they can
make things up, and you cannot tell where their answers come from.

This project aims to be both fast and **trustworthy**. It only answers from real World Bank data and documents. It
refuses to state anything it cannot back with evidence, and it shows that evidence.

## 3. Who it is for

| Person | What they use it for |
|---|---|
| Procurement specialists | Check a tender draft; see realistic timelines; learn from similar past packages |
| Task team leaders and project managers | Watch their projects for slipping milestones and early warnings |
| Analysts | Ask questions about the data without writing SQL; cross-check data against documents |
| Reviewers | Approve the lessons and the user corrections that the assistant is allowed to reuse |
| Engineers | Run, change and extend the platform (see Sections 9 and 10) |

---

## 4. What you can do with it

Everything below is done in the web app. You sign in, and the left sidebar takes you to each part.

### 4.1 Ask questions in the chat

This is the heart of the app. Type a question and the assistant answers it from the right source:

- **Numbers and lists**, for example "contract value by fiscal year in Senegal". These come from the analytical
  tables. The assistant writes a database query, checks that it is safe, and runs it read-only.
- **What the reports say**, for example "what delays were reported in this project". These come from the project
  documents. It finds the right passages and quotes them with their page numbers.
- **Both at once**, for example "count the awards per method in Ethiopia, then summarise the delays in the
  documents". It splits the question into steps, runs each step, and puts the results together.
- **Follow-ups and small talk**, for example "and for Kenya?" or "thanks". These are answered from the conversation
  so far.

While it works, you see each step live ("Writing the answer", ...). You can press **Stop** at any time. Every answer
comes with an **evidence panel** that lists its sources.

**TableLens** is a switch in the message box. For the 10 pilot reports, it finds the right table inside a PDF and
answers with exact figures from that table's rows.

### 4.2 Assess a new procurement package

Describe a package you are planning: its country, sector, method, amount and dates. You get **three predictions**:

1. **How long the tender will take** (in days).
2. **The risk that the contract will need an amendment.**
3. **The risk that the tender will have to be re-bid.**

Each prediction explains what drove it. Prediction 1 is trained on real historical data. Predictions 2 and 3 are
trained on *synthetic* (simulated) labels, because the real outcomes are not published; they are clearly marked.

### 4.3 Check a tender draft ("Tender design and packaging")

Paste or upload a draft: Terms of Reference, a technical specification or a qualification section. The assistant:
- reads out the key terms, such as the bid window, the lots and the required turnover or experience;
- compares each term with what similar past packages actually did;
- flags anything unusual, and shows the benchmark it used.

No AI model is needed for this. The checks are rules applied to the historical records, so the result is the same
every time.

### 4.4 Watch a project ("Project watch")

Choose a project and see:
- its procurement packages, with planned and actual dates;
- how far each package has slipped;
- **early warnings**: packages that are late, stale or signed late, rated High or Medium.

The thresholds come from what similar packages really did, and every warning points to the records it rests on. Like
the tender check, this works without an AI model.

### 4.5 Plan a new project ("Plan a new project")

Describe a project you are preparing. You get a **planning brief** that includes:
- similar past projects;
- a realistic schedule;
- a suggested market approach;
- the main risks and how to reduce them;
- the lessons learned elsewhere;
- what to monitor during execution.

### 4.6 Lessons learned ("Continuous Learning")

The assistant reads the *lessons* sections of completion reports (ICR), independent reviews (ICRR) and appraisal
documents (PAD). It turns each lesson into a **lesson card**: a headline, the text, a page citation and tags (topic,
project stage and procurement category).

A reviewer approves or rejects each card. **Only approved cards are ever used**: in tender checks, in monitoring
answers and in planning briefs.

### 4.7 Dashboards

The **Dashboard** page shows the headline figures:
- contract awards, by procurement method;
- contract value, by fiscal year;
- procurement notices;
- financing (IBRD loans and IDA credits).

The Databricks AI/BI dashboard shows these and more, including country comparisons and bid response windows. When new
data is uploaded, the dashboards update.

### 4.8 Feedback that improves later answers

Under every answer you can say **Good answer**, or tell the assistant what was wrong:
- a wrong number;
- the wrong source or table;
- the wrong filter or period;
- something missing.

You can also **correct the answer and re-run it**. Your corrections and preferences are remembered *for you*. A
correction is only shared with other users after a reviewer approves it in the **Review queue**.

### 4.9 Keep your work organised

- Conversations can be renamed, deleted, searched and grouped into **folders**, for example by project or by country.
- Answers can be **bookmarked**.
- The app has a light and a dark theme.

### 4.10 Your account

Anyone can **create an account**. At sign-up you receive a **recovery code**. If you forget your password, the
recovery code lets you set a new one, and you then get a fresh code. Reviewers are given their role by an
administrator.

### 4.11 Adding new data and reports (for the data team)

- **Reports:** drop a file (PDF, scanned PDF, Word, CSV, Excel or JSON) into the folder for its type under
  `ingestion_inbox/`. It is checked, read (with text recognition for scanned pages), cleaned of personal data, and
  added to the **knowledge graph**. The chat can then cite it.
- **Data:** files uploaded to Databricks are processed by scheduled pipelines from raw (Bronze) to cleaned (Silver)
  to analytical (Gold) tables. The dashboard then updates.

---

## 5. How a question is answered, step by step

1. **You send a question.** The app starts a background *job* and shows you its progress.
2. **Safety check.** The question is screened for:
   - attempts to trick the AI (prompt injection);
   - passwords or secrets pasted by mistake;
   - personal data;
   - off-topic or accusatory requests.
3. **Routing.** The assistant decides what kind of question it is. A quick rule-based router handles common
   questions; for the rest, Claude (the AI model) decides.
4. **Planning.** A simple question gets a fixed one-step plan. A complex one gets a multi-step plan, where each step
   goes to the right specialist:
   - the **data agent** (tables);
   - the **document agent** (reports);
   - the **prediction agent** (the models);
   - the **monitoring, tender, lessons and planning agents**.
5. **Doing the work.** Each step runs with time limits and automatic retries. If a source keeps failing, it is paused
   for a while (a "circuit breaker") so that one broken source does not slow everything down.
6. **Verification.** Every claim in the draft answer must be backed by evidence that a step actually found. Claims
   without evidence are removed.
7. **The answer.** You get the checked answer, with its sources. The whole investigation is saved, so it can be
   reopened, or continued after a restart.

---

## 6. Where the information comes from

| Source | What it holds | How much |
|---|---|---|
| World Bank open data (projects, contracts, procurement notices, loans and credits, country indicators) | The facts and figures | A fixed set of **2,000 World Bank projects**, plus their contracts, notices and financing |
| Project documents: appraisal (PAD), implementation status (ISR), completion (ICR), IEG reviews (ICRR), procurement plans | What happened, and why | **35 projects in 5 countries** (Argentina, Ethiopia, Indonesia, Senegal, Sri Lanka): 7 each, 25 closed and 10 active |
| Prediction models | Tender duration, amendment risk, re-bid risk | 3 models, frozen (never retrained by the app) |
| Lesson cards | Lessons from past projects | Built from the lessons sections of the documents; used only once approved |

**How the data is organised (the "medallion" approach):**
- **Bronze** keeps the data exactly as it arrived (17 tables).
- **Silver** makes it clean, typed and checked (18 tables).
- **Gold** shapes it for the questions people ask (5 analytical tables and 7 dashboard views).

The assistant answers data questions from Gold only.

Documents are split into sections, paragraphs and tables, and stored with search vectors (so they can be found by
meaning, not just by keywords) in **Lakebase**, a Postgres database. They are also linked into a **knowledge graph**
of projects, documents, contracts and lessons.

Why these limits? The 2,000-project set and the 35-project document set are deliberate, approved decisions (D-26 and
D-32 in `docs/decisions/`). They keep the proof of concept fast, affordable and fully checkable.

---

## 7. Trust, safety and privacy

| Protection | What it means for you |
|---|---|
| Evidence-only answers | Nothing is stated without a source; unsupported claims are removed before you see them |
| Read-only data access | The assistant can only *read* the tables. Every query is checked, and writes, deletes or unapproved tables are refused |
| Input guardrails | Tricks, pasted secrets, personal data and off-topic requests are blocked before any work starts |
| Personal-data masking | E-mail addresses, phone numbers, card numbers, IBANs and IP addresses are masked in everything stored or logged |
| Private by default | You only see your own conversations, jobs and feedback. Shared learning needs a reviewer's approval |
| Cost control | Claude is **off** unless the operator starts the app with `--live`, and every session has a spending cap (US$ 5 by default) |
| Rate limits | Each user, and each sign-in address, is limited in how many requests they can make |
| Secure accounts | Passwords and recovery codes are stored only as salted hashes, never in readable form |
| Full audit trail | Each sign-in session is logged: its requests, its questions and every step of every answer. Sign-ins, failed sign-ins and refused requests are logged as security events. Engineers can replay a whole session (`uv run wbg-logs --session`) |

---

## 8. What it cannot do (yet)

Being honest about the limits is part of being trustworthy.

- **Scope.** Documents cover only the 35 approved projects, and data covers only the 2,000-project set. A question
  about anything else is answered as "not in scope", not guessed.
- **French documents.** Searching in French, or across French and English, works much less well than in English.
- **Two of the three predictions** (amendment and re-bid risk) are trained on simulated labels. Treat them as a
  demonstration of the method, not as real risk scores.
- **Claude is needed for most chat answers.** Without it, these features still work:
  - Project watch and monitoring questions;
  - Check tender;
  - lesson cards.
- **Proof of concept.** It runs on a laptop with Databricks and Lakebase in a development workspace. A production
  deployment (Databricks Apps) is planned but not finished.

Evaluation results for each part are in [docs/evaluation/RESULTS.md](evaluation/RESULTS.md).

---

## 9. The building blocks (technology, in plain words)

| Piece | What it does here |
|---|---|
| **Claude** (Anthropic), reached through the **Portkey** gateway | The AI model. It understands questions, makes plans, writes database queries and writes the answer |
| **LangGraph** | Runs each investigation as a series of steps that can be saved and resumed |
| **Databricks** (Unity Catalog, Delta, Lakeflow jobs, SQL warehouse) | Stores and processes the data (Bronze, Silver, Gold); runs the data pipelines and the dashboards |
| **Lakebase** (Postgres with pgvector) | Stores document vectors, the knowledge graph, lesson cards, feedback, user accounts and saved investigations |
| **MCP server** | A separate, read-only service through which the assistant reaches the Gold tables |
| **bge-m3 + BM25** | Find the right document passages: by meaning (bge-m3) and by keywords (BM25) together |
| **Presidio** | Finds and masks personal data |
| **NeMo Guardrails** | Screens incoming questions |
| **FastAPI** | The app's web API (the "back end") |
| **Next.js** | The web app you see in the browser (the "front end") |
| **Logfire** (OpenTelemetry) | Shows every step of every answer as a trace, for debugging |
| **uv** | Installs and runs everything with one command (`uv run wbg-...`) |

---

## 10. File structure

The project is organised so that **each top-level folder is one part of the system**. Tests are kept together in
`tests/`.

### 10.1 The main folders

| Folder | What is inside, in plain words |
|---|---|
| `orchestrator/` | **The app's brain.** The web API, sign-in and accounts, background jobs, the step-by-step investigation engine, the specialist agents ("workers"), logging and tracing |
| `frontend/` | **The web app** you use in the browser: chat, Project watch, Dashboard, Review queue, sign-in pages |
| `conversation/` | Chat memory, deciding where a message should go, personal-data masking, and learning from feedback |
| `structured_agent/` | The **data agent**: turns a question into a safe database query, checks it, and runs it read-only (also the MCP server) |
| `rag/` | The **document agent**: reads the PDFs, finds the right passages, answers with citations; also TableLens |
| `ml/` | The three **prediction models** (frozen) and the code that serves them |
| `monitoring/` | **Project watch**: milestones, slippage and early warnings |
| `tender/` | **Check tender**: reads a draft and compares it with history |
| `lessons/` | **Lesson cards**: built from reports, reviewed, then used in answers |
| `ingest/` | **Automatic ingestion**: turns dropped files into the knowledge graph |
| `ingestion_inbox/` | The **drop folders**, one per document type (`pad/`, `isr/`, `icr/`, `procurement_plans/`, `contracts/`, `notices/`, `other/`), plus `_processed/`, `_quarantine/` and `_ledger/` |
| `etl/`, `silver/`, `gold/`, `resources/`, `databricks.yml` | The **Databricks data pipelines**: raw data to clean data to analytical tables and dashboard views |
| `wbdisc/` | Shared helpers for World Bank data (country codes, reference numbers, plan parsing) |
| `app_config/` | Settings: where every data file lives (`paths.py`), the dashboard list, and a settings check |
| `data/` | Input files: the 2,000-project list, raw extracts, the approved document scope, and sample test documents |
| `infrastructure/` | Connection settings for Lakebase |
| `tests/` | **All automated tests**, one folder per part. `tests/live/` holds the tests that touch real systems; they only run when explicitly switched on |
| `docs/` | **Documentation**: architecture, decisions, reports, evaluation results, plans, and this overview |
| `demo/` | Demo material: suggested questions with their expected answers |

### 10.2 The files at the top

| File | What it is |
|---|---|
| `README.md` | The short technical introduction, for engineers |
| `HOW_TO_RUN.md` | Step-by-step: set up, start the app, use the tools, debug |
| `pyproject.toml`, `uv.lock` | The list of software the project needs, with exact versions |
| `databricks.yml` | Tells Databricks which pipelines and jobs to deploy |

### 10.3 Inside the app's brain (`orchestrator/wbg_orchestrator/`)

| File or folder | Its job |
|---|---|
| `api.py` | The web API: every address the web app calls; sign-in; request ids |
| `accounts.py`, `accounts_db.py` | User accounts: sign-up, sign-in, recovery codes, roles, sessions |
| `app_service.py` | Runs each message as a background job; makes sure users only see their own work |
| `conversation_bridge.py` | The "front door": decides whether a message is chat or an investigation |
| `graph.py` | The investigation steps: check, plan, do the work, verify, answer |
| `controller.py` | Runs each step with time limits, retries and circuit breakers |
| `workers/` | One adapter per specialist agent: data, documents, prediction, monitoring, tender, lessons, planning |
| `verification.py`, `response.py` | Check every claim against its evidence, and write the final answer |
| `rate_limit.py`, `budget.py`, `safety.py` | Request limits per user; token, cost and time limits per investigation; switches that keep full inputs out of outside tracing tools (the question guardrails themselves are in `structured_agent/guardrails/`) |
| `logs.py`, `observability.py`, `log_view.py` | Logging (per session), tracing for Logfire, and the `wbg-logs` viewer |
| `runtime.py` | Connects all the real services together when the app starts |
| `commands.py` | The short commands (`wbg-api`, `wbg-web`, `wbg-logs`, ...) |

### 10.4 A picture of the whole

```text
procurement-intelligence/
├── orchestrator/          the app's brain (API, accounts, jobs, investigation engine, agents, logging)
├── frontend/              the web app in the browser
├── conversation/          chat memory, routing, privacy masking, feedback learning
├── structured_agent/      data agent (safe, read-only database questions) + MCP server
├── rag/                   document agent (search, citations, TableLens)
├── ml/                    the three prediction models
├── monitoring/            Project watch (milestones, early warnings)
├── tender/                Check tender (draft vs history)
├── lessons/               lesson cards (Continuous Learning)
├── ingest/                automatic ingestion into the knowledge graph
├── ingestion_inbox/       drop folders for new documents
├── etl/  silver/  gold/  resources/  databricks.yml    Databricks data pipelines
├── wbdisc/                World Bank data helpers
├── app_config/            settings and file locations
├── data/                  input files and sample documents
├── infrastructure/        Lakebase connection settings
├── tests/                 all automated tests
├── docs/                  documentation (this file is docs/PROJECT_OVERVIEW.md)
├── demo/                  demo questions and answers
├── README.md              technical introduction
└── HOW_TO_RUN.md          how to set up and run everything
```

---

## 11. Running it

The full instructions are in [HOW_TO_RUN.md](../HOW_TO_RUN.md). In short, open two terminals in this folder:

```bash
uv run wbg-api        # terminal 1: the back end (add --live to switch Claude on, capped at US$ 5)
uv run wbg-web        # terminal 2: the web app, then open http://localhost:3000
```

---

## 12. Where to read more

| Document | What it covers |
|---|---|
| [README.md](../README.md) | Technical introduction and how one question flows through the code |
| [HOW_TO_RUN.md](../HOW_TO_RUN.md) | Setup, start-up, all commands, debugging |
| [docs/architecture/DATA_MODELING.md](architecture/DATA_MODELING.md) | How the data was modelled: Bronze, Silver, Gold, documents, graph |
| [docs/architecture/INGESTION_AND_KNOWLEDGE_GRAPH.md](architecture/INGESTION_AND_KNOWLEDGE_GRAPH.md) | How dropped files become the knowledge graph |
| [docs/evaluation/RESULTS.md](evaluation/RESULTS.md) | How well each part works, measured |
| [docs/decisions/](decisions/) | The key scope and data decisions, and why they were made |
| [docs/reports/AUDIT_TRACING_AND_SAFETY_GUIDE.md](reports/AUDIT_TRACING_AND_SAFETY_GUIDE.md) | Audit, tracing and safety in detail |
| [demo/](../demo/) | Questions to ask during a demo |

---

## 13. Glossary

| Term | Meaning |
|---|---|
| **Agent** | A specialist part of the assistant that does one kind of work (data, documents, predictions, ...) |
| **Bronze / Silver / Gold** | The three stages of the data: as received, cleaned, and ready for questions |
| **Citation / evidence** | The exact source of a statement: a table row, a document page, or a model |
| **Circuit breaker** | A safety switch that pauses a failing source for a while, so it does not slow everything down |
| **Claude** | The AI language model (by Anthropic) the assistant uses |
| **Cohort** | The fixed set of 2,000 World Bank projects the data covers |
| **Guardrails** | Checks that stop unsafe or off-topic requests |
| **ICR / ICRR** | Implementation Completion Report; the IEG's independent review of it |
| **ISR** | Implementation Status and Results report (progress during a project) |
| **Job** | One piece of background work, such as answering one message |
| **Knowledge graph** | A network of linked facts: projects, documents, contracts, lessons |
| **Lakebase** | Databricks' managed Postgres database, used here for documents, the graph and app data |
| **Lesson card** | One lesson from a past project, with its citation, used once a reviewer approves it |
| **PAD** | Project Appraisal Document (the plan written before a project is approved) |
| **Procurement plan (PP)** | The list of what a project will buy, how, and when |
| **RAG** | "Retrieval-augmented generation": the AI answers from passages it has looked up, not from memory |
| **Session** | Everything you do between signing in and signing out (or until the sign-in expires) |
| **TableLens** | The feature that answers from the exact table inside a PDF |
| **ToR** | Terms of Reference: what a consultant is hired to do |
| **Trace** | A recording of every step the assistant took to answer, used for debugging |
