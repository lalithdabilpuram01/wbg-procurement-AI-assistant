"""Local web API (FastAPI) over `AppService`.

Authentication: `Authorization: Bearer <token>`. Tokens are issued once with `add-user`; only their SHA-256 is kept
in the user file (outside the repository, mode 600). The server derives the owner from the token; no request body or
path carries a user id. Another user's conversation, investigation or job is reported as 404, like a missing one.

    cd orchestrator
    uv run python -m wbg_orchestrator.api add-user --user alice          # prints the token once
    WBG_ALLOW_LIVE_LLM_CALLS=true uv run python -m wbg_orchestrator.api serve --confirm-live-call

Endpoints:
- conversations: create, list, read;
- messages: submit (returns a job);
- jobs: poll;
- investigations: read, decide an approval, recover after a restart, cancel.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from conversation.models import Owner
from conversation.store import ThreadNotFound

from .app_service import AppService, ConversationBusy, Job, JobNotFound, summarize
from .contracts import PackageSpec
from .engine import ApprovalNotPending, InvestigationNotFound

DEFAULT_USERS_FILE = Path.home() / ".wbg" / "api_users.json"
CLIENT_ID = r"^[A-Za-z0-9_-]{8,64}$"


# --------------------------------------------------------------------------------------------------- users
class UserDirectory:
    """Local app users: SHA-256 of a bearer token -> (tenant, user)."""

    def __init__(self, entries: list[dict[str, str]]):
        self._entries = entries

    @classmethod
    def load(cls, path: Path = DEFAULT_USERS_FILE) -> "UserDirectory":
        return cls(json.loads(path.read_text())["users"] if path.is_file() else [])

    def authenticate(self, token: str) -> Owner | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        for entry in self._entries:
            if hmac.compare_digest(entry["token_sha256"], digest):
                return Owner(tenant_id=entry["tenant_id"], user_id=entry["user_id"])
        return None

    @staticmethod
    def add_user(path: Path, user_id: str, tenant_id: str = "local") -> str:
        """Create a token for a user, store only its hash, and return the token (shown once)."""
        token = secrets.token_urlsafe(32)
        data = json.loads(path.read_text()) if path.is_file() else {"users": []}
        data["users"] = [u for u in data["users"] if not (u["user_id"] == user_id and u["tenant_id"] == tenant_id)]
        data["users"].append({"tenant_id": tenant_id, "user_id": user_id, "token_sha256": hashlib.sha256(token.encode()).hexdigest()})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return token


# ------------------------------------------------------------------------------------------------- requests
class NewConversation(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class NewMessage(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    execute: bool = False                       # the user asks to run the validated, read-only SQL
    client_message_id: str = Field(pattern=CLIENT_ID)


class NewPackage(PackageSpec):
    client_message_id: str = Field(pattern=CLIENT_ID)


class ApprovalAnswer(BaseModel):
    approval_id: str = Field(min_length=1, max_length=80)
    approved: bool
    reason: str | None = Field(default=None, max_length=300)
    client_message_id: str = Field(pattern=CLIENT_ID)


class Retry(BaseModel):
    client_message_id: str = Field(pattern=CLIENT_ID)


def job_view(job: Job) -> dict[str, Any]:
    return {"job_id": job.job_id, "kind": job.kind, "state": job.state, "conversation_id": job.conversation_id,
            "request_id": job.request_id, "status": job.status, "error": job.error}


# ------------------------------------------------------------------------------------------------------ app
def create_app(service: AppService, users: UserDirectory) -> FastAPI:
    app = FastAPI(title="WBG Procurement Intelligence API", version="0.1.0")

    def current_owner(authorization: str = Header(default="")) -> Owner:
        scheme, _, token = authorization.partition(" ")
        owner = users.authenticate(token) if scheme.lower() == "bearer" and token else None
        if owner is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required", {"WWW-Authenticate": "Bearer"})
        return owner

    @app.exception_handler(ThreadNotFound)
    @app.exception_handler(InvestigationNotFound)
    @app.exception_handler(JobNotFound)
    def not_found(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "not found"}, status_code=404)

    @app.exception_handler(ConversationBusy)
    @app.exception_handler(ApprovalNotPending)
    def conflict(request: Request, error: Exception) -> JSONResponse:
        detail = "another request is still running" if isinstance(error, ConversationBusy) else "no pending approval with this id"
        return JSONResponse({"detail": detail}, status_code=409)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/conversations", status_code=201)
    def create_conversation(body: NewConversation, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        thread = service.start_conversation(owner, body.title)
        return {"conversation_id": thread.thread_id, "title": thread.title}

    @app.get("/conversations")
    def list_conversations(owner: Owner = Depends(current_owner)) -> list[dict[str, Any]]:
        return [{"conversation_id": t.thread_id, "title": t.title, "updated_at": t.updated_at.isoformat()}
                for t in service.list_conversations(owner)]

    @app.get("/conversations/{conversation_id}")
    def read_conversation(conversation_id: str, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        turns = service.history(owner, conversation_id)
        return {"conversation_id": conversation_id,
                "turns": [{"seq": t.seq, "role": t.role, "text": t.text, "status": t.metadata.status, "route": t.metadata.route,
                           "request_id": t.metadata.request_id, "created_at": t.created_at.isoformat()} for t in turns],
                "investigations": [{"request_id": r.request_id, "turn_seq": r.turn_seq, "question": r.question}
                                   for r in service.investigations(owner, conversation_id)]}

    @app.post("/conversations/{conversation_id}/messages", status_code=202)
    def submit_message(conversation_id: str, body: NewMessage, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        return job_view(service.submit_message(owner, conversation_id, body.text, body.execute, body.client_message_id))

    @app.post("/conversations/{conversation_id}/packages", status_code=202)
    def submit_package(conversation_id: str, body: NewPackage, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        package = PackageSpec(**body.model_dump(exclude={"client_message_id"}))
        return job_view(service.submit_package(owner, conversation_id, package, body.client_message_id))

    @app.get("/jobs/{job_id}")
    def read_job(job_id: str, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        return job_view(service.job(owner, job_id))

    @app.get("/conversations/{conversation_id}/investigations/{request_id}")
    def read_investigation(conversation_id: str, request_id: str, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        outcome = service.investigation(owner, conversation_id, request_id)
        view = summarize(outcome)
        if outcome.unfinished:
            view["status"] = "running" if service.is_running(owner, conversation_id, request_id) else "interrupted"
        return {"request_id": request_id, **view}

    @app.post("/conversations/{conversation_id}/investigations/{request_id}/decision", status_code=202)
    def decide(conversation_id: str, request_id: str, body: ApprovalAnswer, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        return job_view(service.decide(owner, conversation_id, request_id, body.approval_id, body.approved, body.reason,
                                       body.client_message_id))

    @app.post("/conversations/{conversation_id}/investigations/{request_id}/recover", status_code=202)
    def recover(conversation_id: str, request_id: str, body: Retry, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        return job_view(service.recover(owner, conversation_id, request_id, body.client_message_id))

    @app.post("/conversations/{conversation_id}/investigations/{request_id}/cancel")
    def cancel(conversation_id: str, request_id: str, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        return {"request_id": request_id, **summarize(service.cancel(owner, conversation_id, request_id))}

    return app


# ------------------------------------------------------------------------------------------------------ cli
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="WBG Procurement Intelligence local API")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add-user", help="create (or replace) a user's token; prints it once")
    add.add_argument("--user", required=True)
    add.add_argument("--tenant", default="local")
    serve = commands.add_parser("serve", help="run the API on this laptop (127.0.0.1)")
    serve.add_argument("--confirm-live-call", action="store_true", help="operator approval for live Claude calls")
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    if args.command == "add-user":
        print(UserDirectory.add_user(DEFAULT_USERS_FILE, args.user, args.tenant))
        return 0
    import uvicorn

    from .runtime import RuntimeConfig, build_runtime
    service, close = build_runtime(RuntimeConfig.from_env(), operator_confirmed=args.confirm_live_call)
    try:
        uvicorn.run(create_app(service, UserDirectory.load()), host="127.0.0.1", port=args.port)
    finally:
        close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
