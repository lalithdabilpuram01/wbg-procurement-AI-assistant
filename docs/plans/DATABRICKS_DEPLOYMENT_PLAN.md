# Databricks deployment plan (parked)

Status: **parked on 2026-10-28.** Nothing in it has been done. It starts from the `mark1` tag (the working demo),
and every step needs an explicit OK before anything is written to the workspace or costs money.

## Goal

Run the app on Databricks and use Databricks services where they fit better than what we use today:

| Today | On Databricks | Why |
| --- | --- | --- |
| Laptop (FastAPI + Next.js) | **Databricks Apps** (one App running both) | Hosted, plus the signed-in user's identity (fixes per-user auth) |
| Own user file and tokens | Apps user identity, app **service principal** with read access to the approved Gold objects only | Real per-user access |
| Portkey (blocked from Apps) | **AI Gateway** (Model Serving external endpoint for Anthropic, key in a secret scope) | Rate limits, usage logs, keys in the workspace |
| LangSmith / Logfire (Logfire blocked from Apps) | **MLflow** tracing and evaluation | Native; traces next to the data |
| Frozen local model files | **Unity Catalog model registry** | Versions and lineage |
| bge-m3 loaded in the app | **Model Serving** endpoint for bge-m3 | Keeps a 2 GB model out of the App |
| `ingest watch` on a laptop | **UC Volume + file-arrival job** | Drop a file, ingestion runs |
| Silver/Gold scripts run from the laptop | **Lakeflow job** (see the ETL section below) | Repeatable, scheduled |
| — | **AI/BI dashboard** on `vw_kpi_*`, optional **Genie space** | A view for business users |

Kept as is: Lakebase (vectors, checkpoints, feedback, knowledge graph, lesson cards).

## How the working demo stays safe

1. **Separate code:** a git worktree on a `databricks-deploy` branch made from `mark1`. The demo folder isn't touched.
2. **Switches with today's defaults:** `WBG_LLM_GATEWAY=portkey|ai_gateway`, `WBG_TRACING=off|mlflow`, and so on.
   Locally nothing changes unless a switch is flipped.
3. **Separate data:** the deployed App gets its own Lakebase branch, made from `dev-orchestrator`. That also handles
   the 2026-11-02 expiry.
4. **Separate workspace names:** its own bundle target, App, jobs, Volume and endpoints. Gold and Silver stay
   read-only. Undo with `databricks bundle destroy`.
5. **Per-step approval**, and a way back at any time with `git checkout mark1`.

## Steps

1. **App and jobs**, with Claude answers off: bundle (`databricks.yml`), service principal grants, own Lakebase branch,
   the Volume-triggered ingestion job.
2. **MLflow tracing**, behind its switch.
3. **AI Gateway** for Claude. This is the only step that spends credits, so it waits until the no-spend rule is lifted.
4. Optional: UC model registry, bge-m3 serving endpoint, dashboard and Genie.

## What is needed from the owner

- A Databricks profile with workspace write rights (`wbg-readonly` is read-only by design).
- Browser login when the CLI asks for it, and a final click-through of the deployed App.
- An OK for each step.
