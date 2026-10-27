# Procurement Intelligence – web app

Next.js 15 (App Router) + TypeScript + Tailwind v4, with shadcn/ui-style components on Radix primitives and an Azure
(Fluent) palette in light and dark mode. It is the user interface of the local FastAPI app (`orchestrator/`).

## Run locally

```bash
# 1. The API (from procurement-intelligence/orchestrator). Live Claude calls need both opt-ins.
WBG_ALLOW_LIVE_LLM_CALLS=true env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -m wbg_orchestrator.api serve --confirm-live-call

# 2. A sign-in token (printed once; only its hash is stored in ~/.wbg/api_users.json; no API restart needed)
env -u VIRTUAL_ENV PYTHONPATH=..:. uv run --locked python -m wbg_orchestrator.api add-user --user <name>

# 3. The web app (from procurement-intelligence/frontend)
npm install && npm run dev        # http://localhost:3000   (WBG_API_URL defaults to http://127.0.0.1:8080)
```

## How it is put together

| Area | Files | Notes |
|---|---|---|
| Session | `app/api/session/route.ts`, `middleware.ts` | The token is checked against the API, then held in an httpOnly, SameSite=strict cookie; page scripts never see it |
| API access | `app/api/backend/[...path]/route.ts`, `lib/api.ts` | Same-origin proxy with allow-listed paths; the token is attached on the server |
| Chat | `components/app/chat.tsx`, `answer.tsx` | Claims with numbered citations, status badges, notes, approval of SQL runs, copy and bookmark |
| Sources | `components/app/evidence-panel.tsx` | Each cited record: kind, citation, excerpt, verification, warnings, document link |
| Package assessment | `components/app/package-dialog.tsx`, `prediction-cards.tsx` | Mode B form; duration range bar; amendment and re-bidding risk marked **Synthetic** |
| Workspace | `components/app/sidebar.tsx`, `bookmarks.tsx` | Folders (create, rename, delete, move chats), chat search and rename, bookmarks |
| TableLens | header switch in `chat.tsx` | Reserved for the TableLens demo pipeline (Card-to-Cell table retrieval); disabled until it ships |

Spending: every Claude call goes through the API's ledger (default 10 calls / $2.00; set `WBG_LLM_MAX_ATTEMPTS` and
`WBG_LLM_MAX_USD` when starting the API to change it).
