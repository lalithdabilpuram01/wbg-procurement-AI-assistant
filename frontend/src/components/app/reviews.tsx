"use client";

// The reviewer-only Review queue: pending feedback items (GET /reviews, decided with POST /reviews/{id}) and a tab
// for lesson cards. The API refuses both to non-reviewers with 403; the sidebar only shows the view to reviewers.

import { useCallback, useEffect, useState } from "react";
import { Check, ClipboardCheck, FlaskConical, Route as RouteIcon, Users, X } from "lucide-react";
import { api, type Review } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, Card, Input, Spinner } from "@/components/ui/primitives";
import { cn, relativeTime } from "@/lib/utils";
import { LessonReviews } from "./lesson-reviews";

// What each kind of review item is, and the wording of its approve button.
const KINDS: Record<Review["kind"], { label: string; icon: React.ElementType; approve: string }> = {
  share_correction: { label: "Share a correction with the team", icon: Users, approve: "Share" },
  share_approach: { label: "Share an approach with the team", icon: RouteIcon, approve: "Share" },
  eval_case: { label: "Add an evaluation case", icon: FlaskConical, approve: "Add case" },
};

/** Reviewer queue: nothing a user saved reaches other users, or the evaluation set, without a decision here. */
export function ReviewsView({ onError, onChanged }: { onError: (e: unknown) => void; onChanged: (pending: number) => void }) {
  const [reviews, setReviews] = useState<Review[] | null>(null);
  const [tab, setTab] = useState<"feedback" | "lessons">("feedback");

  // Reloads the pending items and reports their count, which keeps the sidebar badge in step after each decision.
  // `onError` and `onChanged` are stable callbacks from AppShell, so this does not reload on every render.
  const load = useCallback(async () => {
    try {
      const pending = await api.reviews();
      setReviews(pending);
      onChanged(pending.length);
    } catch (e) { onError(e); }
  }, [onError, onChanged]);
  useEffect(() => { void load(); }, [load]);

  return (
    <div className="scrollbar-thin h-full flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl px-4 py-8">
        <h1 className="text-xl font-semibold tracking-tight">Review queue</h1>
        <div className="mt-3 flex gap-1 border-b border-border">
          {([["feedback", "Feedback items"], ["lessons", "Lesson cards"]] as const).map(([key, label]) => (
            <button key={key} onClick={() => setTab(key)}
              className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === key ? "border-primary font-semibold" : "border-transparent text-muted-foreground hover:text-foreground")}>
              {label}{key === "feedback" && reviews ? ` (${reviews.length})` : ""}
            </button>
          ))}
        </div>
        <p className="mt-3 text-sm text-muted-foreground">
          {tab === "feedback"
            ? <>Corrections and approaches users confirmed, and answers they marked wrong. Approving shares an item with every user
              as guidance, or keeps a case for the evaluation set (export with <code>learning_cli export</code>).</>
            : <>Lessons from completion reports, IEG reviews and appraisal documents. Only approved cards are used in tender checks,
              monitoring answers and Planning Briefs; a decision is kept when the document is ingested again.</>}
        </p>
        {tab === "lessons" ? <LessonReviews onError={onError} /> : !reviews ? (
          <div className="mt-10 flex justify-center"><Spinner /></div>
        ) : reviews.length === 0 ? (
          <Card className="mt-8 p-10 text-center text-sm text-muted-foreground">
            <ClipboardCheck className="mx-auto mb-2 size-6" /> Nothing to review.
          </Card>
        ) : (
          <div className="mt-6 space-y-3">
            {reviews.map((r) => <ReviewCard key={r.review_id} review={r} onDone={load} onError={onError} />)}
          </div>
        )}
      </div>
    </div>
  );
}

/** One pending item with approve and reject buttons. An evaluation case needs at least one expected term (what a
 *  correct answer must contain) before it can be added. */
function ReviewCard({ review, onDone, onError }: { review: Review; onDone: () => void; onError: (e: unknown) => void }) {
  const [note, setNote] = useState("");
  const [terms, setTerms] = useState("");
  const [busy, setBusy] = useState(false);
  const kind = KINDS[review.kind];
  const evalCase = review.kind === "eval_case";
  const expected = terms.split(",").map((t) => t.trim()).filter(Boolean);

  // After a decision the list is reloaded, so the decided item leaves the queue; no local removal is needed.
  const decide = async (approved: boolean) => {
    setBusy(true);
    try {
      await api.decideReview(review.review_id, approved, note, expected);
      onDone();
    } catch (e) { onError(e); } finally { setBusy(false); }
  };

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={evalCase ? "warning" : "info"}><kind.icon /> {kind.label}</Badge>
        <span className="text-[11px] text-muted-foreground">from {review.submitted_by} · {relativeTime(review.created_at)}</span>
      </div>
      <p className="mt-3 text-xs font-semibold text-muted-foreground">Question</p>
      <p className="text-sm">{review.question}</p>
      <p className="mt-3 text-xs font-semibold text-muted-foreground">{evalCase ? "User's correction" : "To share"}</p>
      <p className="text-sm font-medium">{review.statement || "—"}</p>
      {review.details.tables && (
        <p className="mt-2 text-xs text-muted-foreground">
          Steps: {review.details.steps?.join(", ") || "—"} · Tables: {review.details.tables.join(", ") || "—"}
          {review.details.filters?.length ? ` · Filters: ${review.details.filters.join("; ")}` : ""}
        </p>
      )}
      {evalCase && (
        <>
          {review.reasons.length > 0 && <p className="mt-2 text-xs text-muted-foreground">Marked: {review.reasons.join(", ").replaceAll("_", " ")}</p>}
          {review.answer_excerpt && (
            <blockquote className="mt-2 border-l-2 border-border pl-3 text-xs text-muted-foreground">{review.answer_excerpt}</blockquote>
          )}
          <Input className="mt-3" value={terms} onChange={(e) => setTerms(e.target.value)}
            placeholder="What a correct answer must contain, comma-separated (e.g. 120, Ethiopia)" />
        </>
      )}
      <div className="mt-3 flex items-center gap-2">
        <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} placeholder="Note (optional)" />
        <Button size="sm" variant="outline" onClick={() => decide(false)} disabled={busy}><X /> Reject</Button>
        <Button size="sm" onClick={() => decide(true)} disabled={busy || (evalCase && expected.length === 0)}>
          {busy ? <Spinner /> : <Check />} {kind.approve}
        </Button>
      </div>
    </Card>
  );
}
