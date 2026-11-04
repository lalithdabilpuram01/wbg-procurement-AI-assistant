"""Local web API (FastAPI) over `AppService`.

Authentication: `Authorization: Bearer <token>`. Users live in Lakebase (schema wbg_auth, accounts.py and
accounts_db.py): anyone can create an account (`POST /auth/signup`), sign in with a password (`POST /auth/login`) and
reset a forgotten password with their recovery code (`POST /auth/reset`). Each sign-in issues a session token that
lives in memory for 12 hours (a restart signs everyone out); script users get a bearer token from `wbg-user add`.
The server derives the owner from the token; no request body or path carries a user id. Another user's
conversation, investigation or job is reported as 404, like a missing one.

Logging: the middleware identifies the token once per request and opens a log context with the request id, the
session id and the pseudonymous user hash, so every line written for the request (and every job it starts) belongs
to its session. Sign-in, sign-up, resets, refused requests (401, 403, 429) and reviewer decisions are security events
(logs.security_event).

    uv run wbg-user init-db && uv run wbg-user migrate       # once: the account table, and the existing users
    uv run wbg-user add --user rita --password '...' --reviewer
    uv run wbg-api [--live]

Endpoints:
- auth: sign up, sign in, reset a password with the recovery code (attempts limited per client address);
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
import json
import re
import sys
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

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
from .contracts import PackageAssessment, PlanningSpec, TenderSpec, new_id
from .feedback import FeedbackRefused, FeedbackRequest, Preferences, ReviewerOnly
from .rate_limit import RateLimiter
from .engine import InvestigationNotFound
from .accounts import AccountError, Identity, UserDirectory, UsernameTaken, user_hash
from .logs import get_logger, log_context, security_event

log = get_logger("api")

CLIENT_ID = r"^[A-Za-z0-9_-]{8,64}$"
PROJECT_ID = re.compile(r"P\d{6}")


# ------------------------------------------------------------------------------------------------- requests
class SignIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class SignUp(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class PasswordReset(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    recovery_code: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=1, max_length=200)


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


# Body of a new chat message: the text, run-the-SQL and TableLens switches, and the client id used to spot repeats.
class NewMessage(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    execute: bool = False                       # the user asks to run the validated, read-only SQL
    table_lens: bool = False                    # answer the 10 pilot PDFs' tables with TableLens (SQL over the table)
    client_message_id: str = Field(pattern=CLIENT_ID)


# The structured request bodies below are the domain specs plus the client message id used to spot repeats.
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
    """The folder name, or 422 when it is the reserved name of the hidden "deleted" folder."""
    if name.strip() == DELETED_FOLDER:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "that folder name is reserved")
    return name


def job_view(job: Job) -> dict[str, Any]:
    return {"job_id": job.job_id, "kind": job.kind, "state": job.state, "conversation_id": job.conversation_id,
            "request_id": job.request_id, "status": job.status, "error": job.error, "trace_id": job.trace_id}


def feedback_view(feedback: Feedback) -> dict[str, Any]:
    return {"feedback_id": feedback.feedback_id, "turn_seq": feedback.turn_seq, "rating": feedback.rating,
            "reasons": list(feedback.reasons), "correction": feedback.correction, "status": feedback.status}


def item_view(item: LearnedItem, expired: bool = False) -> dict[str, Any]:
    """A learned item as JSON for the user's memory page."""
    return {"item_id": item.item_id, "kind": item.kind, "scope": item.scope, "statement": item.statement,
            "question": item.question, "use_count": item.use_count, "created_at": item.created_at.isoformat(),
            "last_used_at": item.last_used_at.isoformat() if item.last_used_at else None, "expired": expired}


def lesson_view(card: LessonCard) -> dict[str, Any]:
    """A lesson card as JSON for the review queue."""
    return {"card_id": card.card_id, "status": card.status, "headline": card.headline, "text": card.text,
            "project_id": card.project_id, "country": card.country, "sector": card.sector, "doc_type": card.doc_type,
            "source": card.source_label, "citation": card.citation, "document_id": card.document_id, "page_start": card.page_start,
            "page_end": card.page_end, "topics": list(card.topics), "stages": list(card.stages), "categories": list(card.categories),
            "decided_by": card.decided_by, "note": card.note}


def review_view(review: Review) -> dict[str, Any]:
    """A review item as JSON; approach details (steps, tables, filters) are shown only for shared approaches."""
    payload = review.payload
    return {"review_id": review.review_id, "kind": review.kind, "submitted_by": review.submitted_by, "status": review.status,
            "question": payload.get("question", ""), "statement": payload.get("statement") or payload.get("correction", ""),
            "details": {k: payload.get("details", {}).get(k) for k in ("steps", "tables", "filters")}
            if review.kind == "share_approach" else {},
            "answer_excerpt": payload.get("answer_excerpt"), "reasons": payload.get("reasons", []),
            "created_at": review.created_at.isoformat(), "note": review.note, "expected_terms": list(review.expected_terms)}


# ------------------------------------------------------------------------------------------------------ app
def create_app(service: AppService, users: UserDirectory, limiter: RateLimiter | None = None,
               kpi_board: KpiBoard | None = None, auth_limiter: RateLimiter | None = None) -> FastAPI:
    """Build the FastAPI app: authentication, rate limits, error mapping, request logging and every endpoint."""
    app = FastAPI(title="WBG Procurement Intelligence API", version="0.1.0")

    def identify(authorization: str) -> Identity | None:
        """The identity behind an `Authorization: Bearer <token>` header; None for any other header."""
        scheme, _, token = authorization.partition(" ")
        return users.identify(token) if scheme.lower() == "bearer" and token else None

    def current_owner(request: Request) -> Owner:
        """The owner identified by the middleware (once per request); a missing or unknown token is refused."""
        identity: Identity | None = getattr(request.state, "identity", None)
        if identity is None:
            reason = "invalid_token" if request.headers.get("authorization") else "missing_token"
            security_event("access.denied", "failure", status_code=401, reason=reason, path=request.url.path)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required", {"WWW-Authenticate": "Bearer"})
        return identity.owner

    limits = limiter or RateLimiter.from_env()

    def limited_owner(owner: Owner = Depends(current_owner)) -> Owner:
        """The owner of a request that starts work, within that user's request limits."""
        refusal = limits.check(f"{owner.tenant_id}\x00{owner.user_id}")
        if refusal:
            security_event("access.denied", "failure", status_code=429, reason=f"rate_limited_{refusal.limit}")
            window = "minute" if refusal.limit == "per_minute" else "day"
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                f"Too many requests: the limit is {refusal.allowed} per {window}. "
                                f"Please try again in {refusal.retry_after_seconds} seconds.",
                                {"Retry-After": str(refusal.retry_after_seconds)})
        return owner

    attempts = auth_limiter or RateLimiter(per_minute=10, per_day=300)

    def limited_attempt(request: Request) -> None:
        """Sign-in, sign-up and reset attempts per client address: slows down password and recovery-code guessing."""
        refusal = attempts.check(f"auth\x00{request.client.host if request.client else '-'}")
        if refusal:
            security_event("access.denied", "failure", status_code=429, reason="auth_attempts_limited",
                           path=request.url.path)
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                f"Too many attempts. Please try again in {refusal.retry_after_seconds} seconds.",
                                {"Retry-After": str(refusal.retry_after_seconds)})

    def reviewer_owner(owner: Owner = Depends(current_owner)) -> Owner:
        """The owner of a reviewer-only request; others get 403 and a security event."""
        if not users.is_reviewer(owner):
            security_event("access.denied", "failure", status_code=403, reason="reviewers_only")
            raise HTTPException(status.HTTP_403_FORBIDDEN, "reviewers only")
        return owner

    # Every "not found" error maps to the same 404, so a foreign item cannot be told apart from a missing one.
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

    @app.exception_handler(Exception)
    def unexpected(request: Request, error: Exception) -> JSONResponse:
        """Any other failure: logged with its traceback under a reference id the user can quote; no details leak."""
        reference = getattr(request.state, "request_ref", None) or new_id("http")
        log.error("request failed", extra={"method": request.method, "path": request.url.path, "reference": reference},
                  exc_info=error)
        return JSONResponse({"detail": "Something went wrong on the server.", "reference": reference}, status_code=500,
                            headers={"X-Request-ID": reference})

    @app.middleware("http")
    async def correlate(request: Request, call_next):
        """Give every request a reference id (X-Request-ID, echoed back), identify its session, log requests that
        change something or fail, and attach the ids to every log line written while the request is handled."""
        reference = request.headers.get("X-Request-ID") or new_id("http")
        # A client-supplied id goes into logs and headers, so anything outside a safe pattern is replaced.
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", reference):
            reference = new_id("http")
        request.state.request_ref = reference
        identity = await run_in_threadpool(identify, request.headers.get("authorization", ""))
        request.state.identity = identity
        started = time.perf_counter()
        with log_context(http_request_id=reference, session_id=identity.session_id if identity else None,
                         user_hash=identity.user_hash if identity else None):
            response = await call_next(request)
            ms = round(1000 * (time.perf_counter() - started))
            if request.method != "GET" or response.status_code >= 400:
                level = "warning" if response.status_code >= 400 else "info"
                getattr(log, level)("http request", extra={"method": request.method, "path": request.url.path,
                                                            "status_code": response.status_code, "duration_ms": ms})
        response.headers["X-Request-ID"] = reference
        return response

    def session_started(event: str, token: str) -> None:
        """The security event of a new session: which session, for which (pseudonymous) user."""
        identity = users.identify(token)
        security_event(event, "success", session_id=identity.session_id if identity else None,
                       user_hash=identity.user_hash if identity else None)

    def auth_failed(event: str, username: str, reason: str) -> None:
        """A failed sign-in or reset. The user hash is logged only for an existing account, so a password typed into
        the username field never reaches the log, not even hashed."""
        owner = users.account_owner(username)
        security_event(event, "failure", reason=reason, user_hash=user_hash(owner) if owner else None)

    # Sign in with a password: a session token, or 401 with the same message for a wrong username or password.
    @app.post("/auth/login", dependencies=[Depends(limited_attempt)])
    def sign_in(body: SignIn) -> dict[str, str]:
        token = users.sign_in(body.username, body.password)
        if token is None:
            auth_failed("auth.sign_in", body.username, "wrong_credentials")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong username or password")
        session_started("auth.sign_in", token)
        return {"token": token}

    @app.post("/auth/signup", status_code=201, dependencies=[Depends(limited_attempt)])
    def sign_up(body: SignUp) -> dict[str, str]:
        """A new normal user, signed in at once. The recovery code is returned this once and never again."""
        try:
            token, code = users.sign_up(body.username, body.password)
        except UsernameTaken as error:
            security_event("auth.sign_up", "failure", reason="username_taken")
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
        except AccountError as error:
            security_event("auth.sign_up", "failure", reason="invalid_credentials")
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
        session_started("auth.sign_up", token)
        return {"token": token, "recovery_code": code}

    @app.post("/auth/reset", dependencies=[Depends(limited_attempt)])
    def reset_password(body: PasswordReset) -> dict[str, str]:
        """Forgot password: username + recovery code + new password. Returns a session and a NEW recovery code."""
        try:
            result = users.reset_password(body.username, body.recovery_code, body.new_password)
        except AccountError as error:
            auth_failed("auth.password_reset", body.username, "invalid_new_password")
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
        if result is None:
            auth_failed("auth.password_reset", body.username, "wrong_recovery_code")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong username or recovery code")
        token, code = result
        session_started("auth.password_reset", token)
        return {"token": token, "recovery_code": code}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/projects")
    def list_projects(owner: Owner = Depends(current_owner)) -> list[dict[str, Any]]:
        return watched_projects()

    # The monitoring view of one project; 404 when it has neither a procurement plan nor ISR ratings.
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

    # One dashboard's KPIs (cached); 503 when no SQL warehouse is configured.
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

    # A conversation with its turns, the investigations it started and the feedback given on its answers.
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
    # Rate an answer; a thumbs-down with a correction also returns the revision job it started.
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

    # Approve or reject a lesson card (reviewers only); the decision is a security event.
    @app.post("/lessons/{card_id}")
    def decide_lesson(card_id: str, body: LessonDecision, owner: Owner = Depends(reviewer_owner)) -> dict[str, Any]:
        card = service.decide_lesson(owner, card_id, body.approved, body.note)
        security_event("review.lesson_decided", "success", card_id=card_id, approved=body.approved)
        return lesson_view(card)

    # The reviewer queue filtered by status ("all" for every status).
    @app.get("/reviews")
    def list_reviews(state: str = Query("pending", alias="status"), owner: Owner = Depends(reviewer_owner)) -> list[dict[str, Any]]:
        wanted = None if state == "all" else state
        if wanted not in (None, "pending", "approved", "rejected"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown status")
        return [review_view(r) for r in service.reviews(owner, wanted)]

    # Decide a review item (reviewers only); the decision is a security event.
    @app.post("/reviews/{review_id}")
    def decide_review(review_id: str, body: ReviewDecision, owner: Owner = Depends(reviewer_owner)) -> dict[str, Any]:
        review = service.decide_review(owner, review_id, body.approved, body.note, body.expected_terms)
        security_event("review.feedback_decided", "success", review_id=review_id, approved=body.approved)
        return review_view(review)

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

    # One investigation. An unfinished one is "running" while its job runs, else "interrupted" (can be recovered).
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
    """`serve`: configure logging, check the account store, build the runtime and run uvicorn on 127.0.0.1."""
    parser = argparse.ArgumentParser(description="WBG Procurement Intelligence local API")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the API on this laptop (127.0.0.1); users: uv run wbg-user")
    serve.add_argument("--confirm-live-call", action="store_true", help="operator approval for live Claude calls")
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    import uvicorn

    from . import accounts_db

    from .logs import ServiceInfo, configure_logging, level_from_env
    from .runtime import ORCHESTRATOR_DIR, RuntimeConfig, build_runtime
    service_info = ServiceInfo.from_env()
    log_file = configure_logging(ORCHESTRATOR_DIR / "runs" / "logs", level=level_from_env("WBG_LOG_LEVEL", "INFO"),
                                 console_level=level_from_env("WBG_CONSOLE_LOG_LEVEL", "WARNING"), service=service_info)
    print(f"Logs: {log_file}  (version {service_info.version}, environment {service_info.environment})")
    accounts = accounts_db.accounts_pool()
    try:
        accounts_db.verify(accounts)                       # fail closed: no sign-in against a missing account store
        service, close = build_runtime(RuntimeConfig.from_env(), operator_confirmed=args.confirm_live_call)
        board = kpi_board()
        try:
            print(f"API: http://127.0.0.1:{args.port}")
            uvicorn.run(create_app(service, accounts_db.directory(accounts), kpi_board=board), host="127.0.0.1",
                        port=args.port, log_config=None)          # uvicorn's own lines go to the app's JSON log
        finally:
            close()
    finally:
        accounts.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
