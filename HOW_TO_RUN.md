# How to run everything

This is the cheat sheet for starting the app and the side tools (ingestion, lesson cards, evaluations, tests).
Copy the commands as they are. Most of them are run from the `orchestrator/` folder, because that's where the
Python environment lives.

One rule for every Python command: we always use `uv`, never a plain `python` from another environment. If you ever
see `ModuleNotFoundError: fastapi` (or similar), you ran it in the wrong environment.

---

## 1. One-time setup

You only do this once per laptop (or again when something expires).

**Install the dependencies:**

```bash
cd orchestrator
env -u VIRTUAL_ENV uv sync --locked          # the Python environment for the API and all tools

cd ../frontend
npm install                                  # the web app
```

The MCP server that talks to Databricks has its own environment, `.venv-structured-mcp/`. It's already set up, and
the app starts it by itself, so you never run it separately.

**Log in to Databricks.** We use two CLI profiles:

```bash
databricks auth login --profile wbg-readonly           # read-only access to the Gold / Silver tables
databricks auth login --profile wbg-databricks-free    # Lakebase (checkpoints, feedback, knowledge graph, lessons)
```

Each opens your browser. Run them again whenever a command complains that the login or token has expired.

**Create the Lakebase stores.** They live on the `dev-orchestrator` branch, and these commands are safe to run twice:

```bash
cd orchestrator
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m wbg_orchestrator.learning_db init   # feedback store
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m ingest init-db                      # knowledge graph
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m lessons init-db                     # lesson cards table
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m lessons backfill                    # 280 proposed cards
```

(These are already done on the current dev branch. You'd only need them again on a new branch.)

---

## 2. Start the app

You need two terminals.

**Terminal 1: the API** (http://127.0.0.1:8080)

```bash
cd orchestrator
WBG_ALLOW_LIVE_LLM_CALLS=true WBG_LLM_MAX_USD=0.50 \
  env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -m wbg_orchestrator.api serve --confirm-live-call
```

Heads-up about Claude: the API only starts with the live-call switch on (both the variable and `--confirm-live-call`).
That doesn't spend anything by itself, but any chat answer written by Claude does. `WBG_LLM_MAX_USD` is a hard
spending cap for this run (I set it to 50 cents above). These features never call Claude, so they're free to demo:

- Project watch, and monitoring questions in chat ("Is P148850 slipping?")
- Check tender
- Lesson cards (review queue and lessons in answers)

**Terminal 2: the web app** (http://localhost:3000)

```bash
cd frontend
npm run dev
```

Open http://localhost:3000 and sign in.

**Demo accounts:**

| Username | Password | What they can do |
| --- | --- | --- |
| `user1` | `user1` | Everything a normal user does: chat, Check tender, Project watch, Memory |
| `reviewer1` | `reviewer1` | All of that, plus the **Review queue** (feedback items and lesson cards) |

To stop the app, press `Ctrl+C` in both terminals. Restarting the API signs everyone out (sessions last 12 hours).

---

## 3. A quick demo route (the four lifecycle stages)

1. **Planning & Strategy:** click **Plan a new project** and fill in a project. You get a Planning Brief with similar
   projects, benchmarks and estimates. This one uses Claude.
2. **Tender Design & Packaging:** click **Check tender** and paste or upload a draft ToR or qualification section.
   You get the terms read from it, checks against history, model estimates and lessons. No Claude.
3. **Execution & Monitoring:** open **Project watch** in the sidebar (try P148850), or ask in chat
   "Is P148850 slipping?". No Claude.
4. **Continuous Learning:**
   - sign in as `reviewer1`, open **Review queue → Lesson cards** and approve a few cards;
   - run the tender check again: the approved lessons now show up in the answer, with their source and reviewer;
   - thumbs up or down and corrections on answers feed the Memory page and the Review queue too.

---

## 4. Users

Add or change a user (run from `orchestrator/`):

```bash
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m wbg_orchestrator.api add-user --user <name> --password '<password>'
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m wbg_orchestrator.api add-user --user <name> --password '<password>' --reviewer
```

Users live in `~/.wbg/api_users.json`, outside the repo. Only salted password hashes are stored. Running `add-user`
again for the same name replaces that user. It also prints an access token, which is only needed for scripts.

---

## 5. Automated ingestion (drop folders)

Drop a file into the folder for its type under `ingestion_inbox/`: `pad/`, `isr/`, `icr/`, `procurement_plans/`,
`contracts/`, `notices/` or `other/`. Then, from `orchestrator/`:

```bash
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m ingest run              # process everything waiting
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m ingest run --dry-run    # try it without writing anything
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m ingest watch            # keep running, ingest files as they land
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m ingest status           # what the graph holds
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m ingest lessons 1674058  # lessons linked to a contract
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m ingest neighbours project:P148850 --depth 2
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

From `orchestrator/`:

```bash
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m lessons preview     # what the corpus would give (no database)
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m lessons status      # proposed / approved / rejected counts
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m lessons backfill    # re-propose the corpus cards (keeps decisions)
```

You approve or reject cards in the app: **Review queue → Lesson cards** (as `reviewer1`).

---

## 7. Feedback reports

From `orchestrator/`:

```bash
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m wbg_orchestrator.learning_cli report    # feedback rate, causes, revisions
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m wbg_orchestrator.learning_cli export    # approved cases -> runs/learning/
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m wbg_orchestrator.learning_db check      # is the feedback store ready?
```

---

## 8. Tests and evaluations

**Tests.** These are offline and safe to run any time. Run them from `orchestrator/`:

```bash
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m pytest -q -p no:cacheprovider \
  tests ../monitoring/tests ../ingest/tests ../lessons/tests ../conversation/tests
```

Don't run `tests_mcp/`: those tests make live calls.

**Frontend checks:**

```bash
cd frontend
npx tsc --noEmit && npx eslint src && npx next build
```

**Golden evaluations.** These make no LLM calls. Results are written to `live_results/golden/`, and the write-up is
in `docs/evaluation/RESULTS.md`.

```bash
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m evaluation.golden run       # all five sets, locally
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -B -m evaluation.golden upload    # send them to LangSmith
```

The `structured` set reads Databricks (read-only, through the MCP server), and `upload` sends redacted results to
LangSmith. Everything else stays on your laptop.

---

## 9. When something goes wrong

| What you see | What to do |
| --- | --- |
| "token expired", "not logged in", or an auth error from Databricks | Run the matching `databricks auth login --profile ...` from step 1 again |
| "the learning store is not initialised" | `python -m wbg_orchestrator.learning_db init` (step 1) |
| "the lesson cards are not initialised" | `python -m lessons init-db` and then `python -m lessons backfill` |
| "Live LLM calls are disabled" when starting the API | You missed `WBG_ALLOW_LIVE_LLM_CALLS=true` or `--confirm-live-call` |
| The login page says "the API is not reachable" | Terminal 1 isn't running, or it's on another port (the web app expects 8080) |
| `ModuleNotFoundError` | You used a different Python; use the `uv run` commands exactly as written |
| Port 3000 or 8080 already in use | Stop the old process (`Ctrl+C` in its terminal) or `lsof -i :8080` to find it |

**One date to remember:** the Lakebase `dev-orchestrator` branch expires on **2026-11-02**. It holds the feedback,
the knowledge graph, the lesson cards and the checkpoints. Extend it before then, or we move everything to a new
branch.
