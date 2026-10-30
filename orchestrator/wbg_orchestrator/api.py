"""Local web API (FastAPI) over `AppService`.

Authentication: `Authorization: Bearer <token>`. Tokens are issued once with `add-user`; only their SHA-256 is kept
in the user file (outside the repository, mode 600). A user given a password (`add-user --password`) can also sign in
with it (`POST /auth/login`): the password is kept as a salted scrypt hash and each sign-in issues a session token
that lives in memory for SESSION_HOURS (a restart signs everyone out). The server derives the owner from the token; no request body or
path carries a user id. Another user's conversation, investigation or job is reported as 404, like a missing one.

    cd orchestrator
    uv run python -m wbg_orchestrator.api add-user --user alice          # prints the token once
    uv run python -m wbg_orchestrator.api add-user --user rita --password '...' --reviewer
    WBG_ALLOW_LIVE_LLM_CALLS=true uv run python -m wbg_orchestrator.api serve --confirm-live-call

Endpoints:
- conversations: create, list, read;
- messages: submit (returns a job);
- jobs: poll;
- investigations: read, recover after a restart, cancel;
- feedback: rate an answer (a correction starts a revision job), the user's memory (preferences, learned items),
  and the reviewer queue (only for users added with `--reviewer`);
- monitoring: a project's milestones and early warnings (monitoring.watch; deterministic, cited, no model call).
- lessons: the lesson-card review queue (reviewers only): approve or reject each proposed card;
- tender design: check a draft against history (a job, like packages and plans); the text of an uploaded draft (not stored).
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from conversation.learning import Feedback, LearnedItem, LearningItemNotFound, Review
from conversation.models import Owner
from monitoring.watch import watch as watch_project, watched_projects
from lessons.cards import LessonCard
from lessons.store import STATUSES as LESSON_STATUSES, LessonNotFound
from tender.text import UnreadableDraft, text_from_file
from conversation.store import ThreadNotFound
from conversation.workspace import WorkspaceItemNotFound

from .dashboards import KpiBoard, dashboards, settings as dashboard_settings, warehouse_sql
from .app_service import DELETED_FOLDER, AppService, ConversationBusy, Job, JobNotFound, summarize
from .contracts import PackageAssessment, PlanningSpec, TenderSpec
from .feedback import FeedbackRefused, FeedbackRequest, Preferences, ReviewerOnly
from .rate_limit import RateLimiter
from .engine import InvestigationNotFound

DEFAULT_USERS_FILE = Path.home() / ".wbg" / "api_users.json"
CLIENT_ID = r"^[A-Za-z0-9_-]{8,64}$"
PROJECT_ID = re.compile(r"P\d{6}")
SESSION_HOURS = 12
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


# --------------------------------------------------------------------------------------------------- users
class UserDirectory:
    """Local app users: SHA-256 of a bearer token -> (tenant, user). Re-reads its file when it changes, so a user added
    (or a token replaced) with `add-user` takes effect without restarting the API."""

    def __init__(self, entries: list[dict[str, str]], path: Path | None = None):
        self._entries, self._path = entries, path
        self._mtime = path.stat().st_mtime_ns if path and path.is_file() else None
        self._sessions: dict[str, tuple[Owner, float]] = {}                 # token SHA-256 -> (owner, expiry)

    @classmethod
    def load(cls, path: Path = DEFAULT_USERS_FILE) -> "UserDirectory":
        return cls(json.loads(path.read_text())["users"] if path.is_file() else [], path)

    def _refresh(self) -> None:
        if self._path and self._path.is_file() and self._path.stat().st_mtime_ns != self._mtime:
            self._entries, self._mtime = json.loads(self._path.read_text())["users"], self._path.stat().st_mtime_ns

    def authenticate(self, token: str) -> Owner | None:
        self._refresh()
        digest = hashlib.sha256(token.encode()).hexdigest()
        for entry in self._entries:
            if hmac.compare_digest(entry["token_sha256"], digest):
                return Owner(tenant_id=entry["tenant_id"], user_id=entry["user_id"])
        session = self._sessions.get(digest)
        if session and session[1] > time.time():
            return session[0]
        self._sessions.pop(digest, None)
        return None

    def sign_in(self, user_id: str, password: str) -> str | None:
        """A session token for the right password (None otherwise; the same work is done for unknown users)."""
        self._refresh()
        entry = next((e for e in self._entries if e["user_id"] == user_id and e.get("password")), None)
        stored = entry["password"] if entry else {"salt": "00" * 16, "scrypt": "00" * 32}
        attempt = hashlib.scrypt(password.encode(), salt=bytes.fromhex(stored["salt"]), **SCRYPT).hex()
        if entry is None or not hmac.compare_digest(attempt, stored["scrypt"]):
            return None
        token = secrets.token_urlsafe(32)
        self._sessions[hashlib.sha256(token.encode()).hexdigest()] = (
            Owner(tenant_id=entry["tenant_id"], user_id=entry["user_id"]), time.time() + SESSION_HOURS * 3600)
        return token

    def is_reviewer(self, owner: Owner) -> bool:
        """Reviewers decide what feedback is shared with the tenant and which evaluation cases are kept."""
        self._refresh()
        return any(e["tenant_id"] == owner.tenant_id and e["user_id"] == owner.user_id and "reviewer" in e.get("roles", [])
                   for e in self._entries)

    @staticmethod
    def add_user(path: Path, user_id: str, tenant_id: str = "local", reviewer: bool = False, password: str | None = None) -> str:
        """Create a token for a user (and a password sign-in when given), store only hashes, and return the token."""
        token = secrets.token_urlsafe(32)
        data = json.loads(path.read_text()) if path.is_file() else {"users": []}
        data["users"] = [u for u in data["users"] if not (u["user_id"] == user_id and u["tenant_id"] == tenant_id)]
        data["users"].append({"tenant_id": tenant_id, "user_id": user_id, "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                              "roles": ["reviewer"] if reviewer else [],
                              **({"password": _password_hash(password)} if password else {})})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return token


def _password_hash(password: str) -> dict[str, str]:
    salt = secrets.token_bytes(16)
    return {"salt": salt.hex(), "scrypt": hashlib.scrypt(password.encode(), salt=salt, **SCRYPT).hex()}


# ------------------------------------------------------------------------------------------------- requests
class SignIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class NewConversation(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class FolderName(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class Filing(BaseModel):
    folder_id: str | None = None                       # None takes the conversation out of its folder


class NewBookmark(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=64)
    turn_seq: int = Field(gt=0)
    note: str = Field(default="", max_length=200)


class NewMessage(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    execute: bool = False                       # the user asks to run the validated, read-only SQL
    table_lens: bool = False                    # answer the 10 pilot PDFs' tables with TableLens (SQL over the table)
    client_message_id: str = Field(pattern=CLIENT_ID)


class NewPackage(PackageAssessment):
    client_message_id: str = Field(pattern=CLIENT_ID)


class NewPlan(PlanningSpec):
    client_message_id: str = Field(pattern=CLIENT_ID)


class NewTender(TenderSpec):
    client_message_id: str = Field(pattern=CLIENT_ID)


class DraftFile(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1, max_length=7_000_000)        # about 5 MB of file


class LessonDecision(BaseModel):
    approved: bool
    note: str | None = Field(default=None, max_length=300)


class Retry(BaseModel):
    client_message_id: str = Field(pattern=CLIENT_ID)


class FeedbackBody(FeedbackRequest):
    client_message_id: str = Field(pattern=CLIENT_ID)


class ReviewDecision(BaseModel):
    approved: bool
    note: str | None = Field(default=None, max_length=300)
    expected_terms: list[str] = Field(default_factory=list, max_length=10)


def allowed_folder_name(name: str) -> str:
    if name.strip() == DELETED_FOLDER:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "that folder name is reserved")
    return name


def job_view(job: Job) -> dict[str, Any]:
    return {"job_id": job.job_id, "kind": job.kind, "state": job.state, "conversation_id": job.conversation_id,
            "request_id": job.request_id, "status": job.status, "error": job.error}


def feedback_view(feedback: Feedback) -> dict[str, Any]:
    return {"feedback_id": feedback.feedback_id, "turn_seq": feedback.turn_seq, "rating": feedback.rating,
            "reasons": list(feedback.reasons), "correction": feedback.correction, "status": feedback.status}


def item_view(item: LearnedItem, expired: bool = False) -> dict[str, Any]:
    return {"item_id": item.item_id, "kind": item.kind, "scope": item.scope, "statement": item.statement,
            "question": item.question, "use_count": item.use_count, "created_at": item.created_at.isoformat(),
            "last_used_at": item.last_used_at.isoformat() if item.last_used_at else None, "expired": expired}


def lesson_view(card: LessonCard) -> dict[str, Any]:
    return {"card_id": card.card_id, "status": card.status, "headline": card.headline, "text": card.text,
            "project_id": card.project_id, "country": card.country, "sector": card.sector, "doc_type": card.doc_type,
            "source": card.source_label, "citation": card.citation, "document_id": card.document_id, "page_start": card.page_start,
            "page_end": card.page_end, "topics": list(card.topics), "stages": list(card.stages), "categories": list(card.categories),
            "decided_by": card.decided_by, "note": card.note}


def review_view(review: Review) -> dict[str, Any]:
    payload = review.payload
    return {"review_id": review.review_id, "kind": review.kind, "submitted_by": review.submitted_by, "status": review.status,
            "question": payload.get("question", ""), "statement": payload.get("statement") or payload.get("correction", ""),
            "details": {k: payload.get("details", {}).get(k) for k in ("steps", "tables", "filters")}
            if review.kind == "share_approach" else {},
            "answer_excerpt": payload.get("answer_excerpt"), "reasons": payload.get("reasons", []),
            "created_at": review.created_at.isoformat(), "note": review.note, "expected_terms": list(review.expected_terms)}


# ------------------------------------------------------------------------------------------------------ app
def create_app(service: AppService, users: UserDirectory, limiter: RateLimiter | None = None,
               kpi_board: KpiBoard | None = None) -> FastAPI:
    app = FastAPI(title="WBG Procurement Intelligence API", version="0.1.0")

    def current_owner(authorization: str = Header(default="")) -> Owner:
        scheme, _, token = authorization.partition(" ")
        owner = users.authenticate(token) if scheme.lower() == "bearer" and token else None
        if owner is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required", {"WWW-Authenticate": "Bearer"})
        return owner

    limits = limiter or RateLimiter.from_env()

    def limited_owner(owner: Owner = Depends(current_owner)) -> Owner:
        """The owner of a request that starts work, within that user's request limits."""
        refusal = limits.check(f"{owner.tenant_id}\x00{owner.user_id}")
        if refusal:
            window = "minute" if refusal.limit == "per_minute" else "day"
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                f"Too many requests: the limit is {refusal.allowed} per {window}. "
                                f"Please try again in {refusal.retry_after_seconds} seconds.",
                                {"Retry-After": str(refusal.retry_after_seconds)})
        return owner

    def reviewer_owner(owner: Owner = Depends(current_owner)) -> Owner:
        if not users.is_reviewer(owner):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "reviewers only")
        return owner

    @app.exception_handler(ThreadNotFound)
    @app.exception_handler(InvestigationNotFound)
    @app.exception_handler(JobNotFound)
    @app.exception_handler(WorkspaceItemNotFound)
    @app.exception_handler(LearningItemNotFound)
    @app.exception_handler(LessonNotFound)
    def not_found(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "not found"}, status_code=404)

    @app.exception_handler(FeedbackRefused)
    def refused(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.exception_handler(ReviewerOnly)
    def forbidden(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=403)

    @app.exception_handler(ConversationBusy)
    def conflict(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "another request is still running"}, status_code=409)

    @app.post("/auth/login")
    def sign_in(body: SignIn) -> dict[str, str]:
        token = users.sign_in(body.username.strip(), body.password)
        if token is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong username or password")
        return {"token": token}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/projects")
    def list_projects(owner: Owner = Depends(current_owner)) -> list[dict[str, Any]]:
        return watched_projects()

    @app.get("/projects/{project_id}/watch")
    def project_watch(project_id: str, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        if not PROJECT_ID.fullmatch(project_id):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "a project id is P followed by 6 digits")
        result = watch_project(project_id)
        if not result.packages and not result.ratings:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no procurement plan or ISR for this project")
        return result.to_dict()

    @app.get("/dashboards")
    def list_dashboards(owner: Owner = Depends(current_owner)) -> list[dict[str, str]]:
        return dashboards()

    @app.get("/dashboards/{key}/kpis")
    def dashboard_kpis(key: str, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        if kpi_board is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "the KPI dashboard is not connected")
        try:
            return kpi_board.kpis(key)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such dashboard") from None

    @app.get("/me")
    def me(owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        return {"user_id": owner.user_id, "reviewer": users.is_reviewer(owner), "feedback": service.learning is not None}

    @app.post("/conversations", status_code=201)
    def create_conversation(body: NewConversation, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        thread = service.start_conversation(owner, body.title)
        return {"conversation_id": thread.thread_id, "title": thread.title}

    @app.get("/conversations")
    def list_conversations(owner: Owner = Depends(current_owner)) -> list[dict[str, Any]]:
        return [{"conversation_id": t.thread_id, "title": t.title, "updated_at": t.updated_at.isoformat(), "folder_id": folder}
                for t, folder in service.list_conversations(owner)]

    @app.delete("/conversations/{conversation_id}", status_code=204)
    def delete_conversation(conversation_id: str, owner: Owner = Depends(current_owner)) -> None:
        service.delete_conversation(owner, conversation_id)

    @app.patch("/conversations/{conversation_id}", status_code=204)
    def rename_conversation(conversation_id: str, body: Rename, owner: Owner = Depends(current_owner)) -> None:
        service.rename_conversation(owner, conversation_id, body.title)

    @app.put("/conversations/{conversation_id}/folder", status_code=204)
    def file_conversation(conversation_id: str, body: Filing, owner: Owner = Depends(current_owner)) -> None:
        service.file_conversation(owner, conversation_id, body.folder_id)

    # ---------------------------------------------------------------------------------------- workspace
    @app.get("/folders")
    def list_folders(owner: Owner = Depends(current_owner)) -> list[dict[str, Any]]:
        return [f.model_dump(mode="json") for f in service.folders(owner)]

    @app.post("/folders", status_code=201)
    def create_folder(body: FolderName, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        return service.create_folder(owner, allowed_folder_name(body.name)).model_dump(mode="json")

    @app.patch("/folders/{folder_id}")
    def rename_folder(folder_id: str, body: FolderName, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        return service.rename_folder(owner, folder_id, allowed_folder_name(body.name)).model_dump(mode="json")

    @app.delete("/folders/{folder_id}", status_code=204)
    def delete_folder(folder_id: str, owner: Owner = Depends(current_owner)) -> None:
        service.delete_folder(owner, folder_id)

    @app.get("/bookmarks")
    def list_bookmarks(owner: Owner = Depends(current_owner)) -> list[dict[str, Any]]:
        return [{**b.model_dump(mode="json"), "conversation_title": title, "excerpt": excerpt}
                for b, title, excerpt in service.bookmarks(owner)]

    @app.post("/bookmarks", status_code=201)
    def add_bookmark(body: NewBookmark, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        return service.add_bookmark(owner, body.conversation_id, body.turn_seq, body.note).model_dump(mode="json")

    @app.delete("/bookmarks/{bookmark_id}", status_code=204)
    def delete_bookmark(bookmark_id: str, owner: Owner = Depends(current_owner)) -> None:
        service.delete_bookmark(owner, bookmark_id)

    @app.get("/conversations/{conversation_id}")
    def read_conversation(conversation_id: str, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        turns = service.history(owner, conversation_id)
        return {"conversation_id": conversation_id,
                "turns": [{"seq": t.seq, "role": t.role, "text": t.text, "status": t.metadata.status, "route": t.metadata.route,
                           "request_id": t.metadata.request_id, "created_at": t.created_at.isoformat()} for t in turns],
                "investigations": [{"request_id": r.request_id, "turn_seq": r.turn_seq, "question": r.question}
                                   for r in service.investigations(owner, conversation_id)],
                "feedback": [feedback_view(f) for f in service.thread_feedback(owner, conversation_id).values()]}

    # ------------------------------------------------------------------------------------------ feedback
    @app.post("/conversations/{conversation_id}/feedback", status_code=201)
    def give_feedback(conversation_id: str, body: FeedbackBody, owner: Owner = Depends(limited_owner)) -> dict[str, Any]:
        request = FeedbackRequest(**body.model_dump(exclude={"client_message_id"}))
        feedback, job = service.give_feedback(owner, conversation_id, request, body.client_message_id)
        return {"feedback": feedback_view(feedback), "job": job_view(job) if job else None}

    @app.get("/learning")
    def read_memory(owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        preferences, items = service.memory(owner)
        return {"preferences": preferences.model_dump(mode="json"), "items": [item_view(i, expired) for i, expired in items]}

    @app.put("/learning/preferences")
    def save_preferences(body: Preferences, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        return service.set_preferences(owner, body).model_dump(mode="json")

    @app.delete("/learning/items/{item_id}", status_code=204)
    def revoke_item(item_id: str, owner: Owner = Depends(current_owner)) -> None:
        service.revoke_item(owner, item_id, users.is_reviewer(owner))

    @app.get("/lessons")
    def list_lessons(state: str = Query("proposed", alias="status"), owner: Owner = Depends(reviewer_owner)) -> dict[str, Any]:
        """Lesson cards for the review queue (reviewers only): the cards of one status and the counts of all."""
        if state not in LESSON_STATUSES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown status")
        return {"counts": service.lesson_counts(), "cards": [lesson_view(c) for c in service.lesson_cards(state)]}

    @app.post("/lessons/{card_id}")
    def decide_lesson(card_id: str, body: LessonDecision, owner: Owner = Depends(reviewer_owner)) -> dict[str, Any]:
        return lesson_view(service.decide_lesson(owner, card_id, body.approved, body.note))

    @app.get("/reviews")
    def list_reviews(state: str = Query("pending", alias="status"), owner: Owner = Depends(reviewer_owner)) -> list[dict[str, Any]]:
        wanted = None if state == "all" else state
        if wanted not in (None, "pending", "approved", "rejected"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown status")
        return [review_view(r) for r in service.reviews(owner, wanted)]

    @app.post("/reviews/{review_id}")
    def decide_review(review_id: str, body: ReviewDecision, owner: Owner = Depends(reviewer_owner)) -> dict[str, Any]:
        return review_view(service.decide_review(owner, review_id, body.approved, body.note, body.expected_terms))

    @app.post("/conversations/{conversation_id}/messages", status_code=202)
    def submit_message(conversation_id: str, body: NewMessage, owner: Owner = Depends(limited_owner)) -> dict[str, Any]:
        return job_view(service.submit_message(owner, conversation_id, body.text, body.execute, body.client_message_id,
                                               body.table_lens))

    @app.post("/conversations/{conversation_id}/packages", status_code=202)
    def submit_package(conversation_id: str, body: NewPackage, owner: Owner = Depends(limited_owner)) -> dict[str, Any]:
        package = PackageAssessment(**body.model_dump(exclude={"client_message_id"}))
        return job_view(service.submit_package(owner, conversation_id, package, body.client_message_id))

    @app.post("/conversations/{conversation_id}/tenders", status_code=202)
    def submit_tender(conversation_id: str, body: NewTender, owner: Owner = Depends(limited_owner)) -> dict[str, Any]:
        spec = TenderSpec(**body.model_dump(exclude={"client_message_id"}))
        return job_view(service.submit_tender(owner, conversation_id, spec, body.client_message_id))

    @app.post("/drafts/text")
    def draft_text(body: DraftFile, owner: Owner = Depends(limited_owner)) -> dict[str, Any]:
        """The text of an uploaded draft, for the user to review before a tender check (nothing is stored)."""
        try:
            text = text_from_file(body.filename, base64.b64decode(body.content_base64, validate=True))
        except (UnreadableDraft, binascii.Error) as error:
            detail = str(error) if isinstance(error, UnreadableDraft) else "The file could not be decoded."
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail) from error
        return {"text": text, "characters": len(text)}

    @app.post("/conversations/{conversation_id}/plans", status_code=202)
    def submit_plan(conversation_id: str, body: NewPlan, owner: Owner = Depends(limited_owner)) -> dict[str, Any]:
        spec = PlanningSpec(**body.model_dump(exclude={"client_message_id"}))
        return job_view(service.submit_plan(owner, conversation_id, spec, body.client_message_id))

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        return job_view(service.cancel_job(owner, job_id))

    @app.get("/jobs/{job_id}")
    def read_job(job_id: str, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        return job_view(service.job(owner, job_id))

    @app.get("/jobs/{job_id}/events")
    def job_events(job_id: str, after: int = Query(default=0, ge=0), owner: Owner = Depends(current_owner)) -> StreamingResponse:
        """The job's live steps and answer text as server-sent events (see progress.py), from event `after` on."""
        service.job(owner, job_id)                                              # the owner's job, else not found
        return StreamingResponse(event_stream(service.streams, job_id, after), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/conversations/{conversation_id}/investigations/{request_id}")
    def read_investigation(conversation_id: str, request_id: str, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        outcome = service.investigation(owner, conversation_id, request_id)
        view = summarize(outcome)
        if outcome.unfinished:
            view["status"] = "running" if service.is_running(owner, conversation_id, request_id) else "interrupted"
        return {"request_id": request_id, **view,
                "applied_learning": [item_view(i) for i in service.applied_learning(owner, request_id)]}

    @app.post("/conversations/{conversation_id}/investigations/{request_id}/recover", status_code=202)
    def recover(conversation_id: str, request_id: str, body: Retry, owner: Owner = Depends(limited_owner)) -> dict[str, Any]:
        return job_view(service.recover(owner, conversation_id, request_id, body.client_message_id))

    @app.post("/conversations/{conversation_id}/investigations/{request_id}/cancel")
    def cancel(conversation_id: str, request_id: str, owner: Owner = Depends(current_owner)) -> dict[str, Any]:
        return {"request_id": request_id, **summarize(service.cancel(owner, conversation_id, request_id))}

    return app


# ------------------------------------------------------------------------------------------------------ cli
def event_stream(streams, job_id: str, after: int, heartbeat_seconds: float = 15.0):
    """Server-sent events: one `data:` line per event (its index as the id), a comment as keep-alive while waiting."""
    sent = after
    while True:
        events, ended = streams.read(job_id, sent, timeout=heartbeat_seconds)
        for event in events:
            sent += 1
            yield f"id: {sent}\ndata: {json.dumps(event)}\n\n"
        if ended:
            return
        if not events:
            yield ": keep-alive\n\n"


def kpi_board() -> KpiBoard | None:
    """The Dashboard page's KPIs from the SQL warehouse (None when no warehouse is configured); warmed in the background."""
    from infrastructure.lakebase_poc.lakebase_poc.settings import load_settings as lakebase_login
    warehouse = dashboard_settings().get("warehouse_id")
    if not warehouse:
        return None
    board = KpiBoard(warehouse_sql(lakebase_login().databricks_profile, warehouse))
    board.warm()
    return board


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="WBG Procurement Intelligence local API")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add-user", help="create (or replace) a user's token; prints it once")
    add.add_argument("--user", required=True)
    add.add_argument("--tenant", default="local")
    add.add_argument("--reviewer", action="store_true", help="may approve shared feedback and evaluation cases")
    add.add_argument("--password", help="also allow signing in with this password (kept as a salted hash)")
    serve = commands.add_parser("serve", help="run the API on this laptop (127.0.0.1)")
    serve.add_argument("--confirm-live-call", action="store_true", help="operator approval for live Claude calls")
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    if args.command == "add-user":
        print(UserDirectory.add_user(DEFAULT_USERS_FILE, args.user, args.tenant, args.reviewer, args.password))
        return 0
    import uvicorn

    from .runtime import RuntimeConfig, build_runtime
    service, close = build_runtime(RuntimeConfig.from_env(), operator_confirmed=args.confirm_live_call)
    board = kpi_board()
    try:
        uvicorn.run(create_app(service, UserDirectory.load(), kpi_board=board), host="127.0.0.1", port=args.port)
    finally:
        close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
