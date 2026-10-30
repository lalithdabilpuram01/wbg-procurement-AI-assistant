# How to run everything

This is the cheat sheet for starting the app and the side tools: ingestion, lesson cards, evaluations and tests.
Every command runs from the repo root. The whole project is one uv project (`pyproject.toml` + `uv.lock`), so
there's no `cd` and no `PYTHONPATH`.

---

## 1. One-time setup

You only do this once per laptop, or again when something expires.

**Install the dependencies:**

```bash
uv sync                        # one Python environment (.venv) for the API, the agents and every tool
```

The web app's packages install themselves the first time you run `uv run wbg-web` (it runs `npm install` once).

If you ever see `ModuleNotFoundError`, run `uv sync` again.

**Log in to Databricks.** We use two CLI profiles:

```bash
databricks auth login --profile wbg-readonly           # read-only access to the Gold / Silver tables
databricks auth login --profile wbg-databricks-free    # Lakebase (checkpoints, feedback, knowledge graph, lessons)
```

Each opens your browser. Run them again whenever a command complains that the login or token has expired.

**Create the Lakebase stores.** They live on the `dev-orchestrator` branch, and these commands are safe to run twice:

```bash
uv run wbg-feedback-db init      # feedback store
uv run wbg-ingest init-db        # knowledge graph
uv run wbg-lessons init-db       # lesson cards table
uv run wbg-lessons backfill      # 280 proposed cards
```

(These are already done on the current dev branch. You'd only need them again on a new branch.)

---

## 2. Start the app

You need two terminals.

**Terminal 1: the API** (http://127.0.0.1:8080)

```bash
uv run wbg-api                 # Claude OFF: nothing can spend money
uv run wbg-api --live          # Claude ON, at most US$ 5 this session
uv run wbg-api --live --cap 10 # Claude ON, at most US$ 10 this session
```

**Claude is off by default.** These features never use Claude, so they work either way:

- Project watch, and monitoring questions in chat ("Is P148850 slipping?")
- Check tender
- Lesson cards (review queue and lessons in answers)

Anything that needs Claude (most chat questions, the Planning Brief) answers "Claude is off for this session" until
you start with `--live`.

**What `--live` does:**

- Each session gets its own spend record (`orchestrator/runs/llm_ledger_<time>.json`).
- The cap applies to that session alone; a new session starts from zero.
- When the cap is reached, Claude calls stop; nothing goes over.

**Terminal 2: the web app** (http://localhost:3000)

```bash
uv run wbg-web
```

Open http://localhost:3000 and sign in.

**Demo accounts:**

| Username | Password | What they can do |
| --- | --- | --- |
| `user1` | `user1` | Everything a normal user does: chat, Check tender, Project watch, Dashboard |
| `reviewer1` | `reviewer1` | All of that, plus the **Review queue** (feedback items and lesson cards) |

To stop the app, press `Ctrl+C` in both terminals. Restarting the API signs everyone out (sessions last 12 hours).

---

## 3. A quick demo route (the four lifecycle stages)

1. **Planning & Strategy:** click **Plan a new project** and fill in a project. You get a Planning Brief with similar
   projects, benchmarks and estimates. This one needs `--live`.
2. **Tender Design & Packaging:** click **Check tender** and paste or upload a draft ToR or qualification section.
   You get the terms read from it, checks against history, model estimates and lessons. Free.
3. **Execution & Monitoring:** open **Project watch** in the sidebar (try P148850), or ask in chat
   "Is P148850 slipping?". Free.
4. **Continuous Learning:**
   - sign in as `reviewer1`, open **Review queue → Lesson cards** and approve a few cards;
   - run the tender check again: the approved lessons now show up in the answer, with their source and reviewer;
   - thumbs up or down and corrections on answers feed the Review queue and guide later answers.

---

## 4. Users

Add or change a user:

```bash
uv run wbg-user --user <name> --password '<password>'
uv run wbg-user --user <name> --password '<password>' --reviewer
```

Users live in `~/.wbg/api_users.json`, outside the repo. Only salted password hashes are stored. Running `wbg-user`
again for the same name replaces that user. It also prints an access token, which is only needed for scripts.

---

## 5. Automated ingestion

There are two ways in, and both end in the same Lakebase store (`wbg_kg`, pgvector), so a new document becomes
searchable in chat.

### In Databricks (Lakeflow jobs): the demo path

Structured data goes through Bronze → Silver → Gold → the **Dashboard** page. Documents go through parse → bge-m3
embeddings → pgvector → chat and the Review queue. The full runbook is in [etl/README.md](etl/README.md).

```bash
uv run wbg-etl-seed      # once: fill the landing Volume and set aside the demo slice of new contract awards
uv run wbg-etl-watch     # keep running: starts the job whenever a file is uploaded to a Volume
```

Upload in Databricks (**Catalog → wbg_etl → etl → landing/<dataset>** or **inbox/<type>** → Upload), then watch the run
under **Jobs & Pipelines**.

### On the laptop (drop folders): the backup path

Drop a file into the folder for its type under `ingestion_inbox/`: `pad/`, `isr/`, `icr/`, `procurement_plans/`,
`contracts/`, `notices/` or `other/`. Then:

```bash
uv run wbg-ingest run                         # process everything waiting
uv run wbg-ingest run --dry-run               # try it without writing anything
uv run wbg-ingest watch                       # keep running, ingest files as they land
uv run wbg-ingest status                      # what the graph holds
uv run wbg-ingest lessons 1674058             # lessons linked to a contract
uv run wbg-ingest neighbours project:P148850 --depth 2
```

- Processed files move to `ingestion_inbox/_processed/`.
- Refused ones go to `_quarantine/`, with a `.reason.json` that says why.
- Every outcome is logged in `_ledger/ledger.jsonl`.
- An ICR, IEG review or PAD also creates proposed lesson cards automatically.

Scanned PDFs need Tesseract for OCR. It isn't installed yet:

```bash
brew install tesseract
```

---

## 6. Lesson cards

```bash
uv run wbg-lessons preview     # what the corpus would give (no database)
uv run wbg-lessons status      # proposed / approved / rejected counts
uv run wbg-lessons backfill    # re-propose the corpus cards (keeps decisions)
```

You approve or reject cards in the app: **Review queue → Lesson cards** (as `reviewer1`).

---

## 7. Feedback reports

```bash
uv run wbg-feedback report     # feedback rate, causes, revisions
uv run wbg-feedback export     # approved cases -> runs/learning/
uv run wbg-feedback-db check   # is the feedback store ready?
```

---

## 8. Tests and evaluations

**Tests.** All tests live in `tests/`, one folder per component (`tests/orchestrator/`, `tests/conversation/`, ...).
These are offline and safe to run any time:

```bash
uv run wbg-test                # every offline suite in tests/ (about 1,300 tests, 2-3 minutes)
uv run wbg-test -k tender      # extra arguments go to pytest
uv run wbg-test tests/rag      # one component
```

`tests/live/` holds the tests that call Databricks, Lakebase, Claude or the MCP server. They are never collected unless
you ask for them explicitly, one suite at a time (they may cost money):

```bash
WBG_RUN_LIVE_TESTS=1 uv run pytest tests/live/<suite>
```

**Frontend checks:**

```bash
cd frontend && npx tsc --noEmit && npx eslint src && npx next build
```

**Golden evaluations.** These make no LLM calls. Results are written to `orchestrator/live_results/golden/`, and the
write-up is in `docs/evaluation/RESULTS.md`.

```bash
uv run wbg-eval run            # all five sets, locally
uv run wbg-eval upload         # send them to LangSmith
```

The `structured` set reads Databricks (read-only, through the MCP server), and `upload` sends redacted results to
LangSmith. Everything else stays on your laptop.

---

## 9. Debugging: logs and traces

Every chat message, tender check or plan runs as a background **job**. The job and everything it does share the same
ids, in both places an engineer looks:

- **The log file:** `orchestrator/runs/logs/app.jsonl`, one JSON line per event. Each line has `job_id`, `request_id`,
  `conversation_id` and `trace_id`. Every failure is written once, at level ERROR, with `error_type`, `error_location`
  (file:line:function), the traceback and the step that failed. Personal data in messages is masked.
- **Logfire:** one trace per job, with nested spans: `job.message` > `conversation.message` > `orchestrator.node.*`
  (intake, plan, execute, verify, respond) > `orchestrator.task_attempt` > `worker.*` > `mcp.tool_call` /
  `orchestrator.llm_call`. A failed span is red and carries the same `error_type`, `error_location` and `failed_step`.
  Spans are sent to Logfire only when `.env` has `LOGFIRE_SEND_TO_LOGFIRE=true` and `LOGFIRE_TOKEN`; otherwise the trace
  ids are still in the log file.

How to find a failure:

```bash
grep '"level": "ERROR"' orchestrator/runs/logs/app.jsonl | tail -5     # the latest failures
grep '<job_id>' orchestrator/runs/logs/app.jsonl                        # everything one job did, in order
```

The job id is in the API's job response (`GET /jobs/<id>`), along with `trace_id`; paste the trace id into Logfire's
search to open the nested trace. An unexpected server error returns `{"detail": ..., "reference": "http_..."}`: that
reference is on the matching log line. The audit trail of each investigation (metadata only) stays in
`orchestrator/runs/app_audit.jsonl`.

---

## 10. When something goes wrong

| What you see | What to do |
| --- | --- |
| "token expired", "not logged in", or an auth error from Databricks | Run the matching `databricks auth login --profile ...` from step 1 again |
| "Claude is off for this session" | Expected without `--live`; restart with `uv run wbg-api --live` |
| "the learning store is not initialised" | `uv run wbg-feedback-db init` (step 1) |
| "the lesson cards are not initialised" | `uv run wbg-lessons init-db` and then `uv run wbg-lessons backfill` |
| The login page says "the API is not reachable" | Terminal 1 isn't running, or it's on another port (the web app expects 8080) |
| `ModuleNotFoundError` | Run `uv sync` |
| Port 3000 or 8080 already in use | Stop the old process (`Ctrl+C` in its terminal) or `lsof -i :8080` to find it |

**One date to remember:** the Lakebase `dev-orchestrator` branch expires on **2026-11-02**. It holds the feedback,
the knowledge graph, the lesson cards and the checkpoints. Extend it before then, or we move everything to a new
branch.
