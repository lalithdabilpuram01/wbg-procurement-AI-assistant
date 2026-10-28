"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ClipboardCheck, FlaskConical, Route as RouteIcon, Users, X } from "lucide-react";
import { api, type Review } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, Card, Input, Spinner } from "@/components/ui/primitives";
import { relativeTime } from "@/lib/utils";

const KINDS: Record<Review["kind"], { label: string; icon: React.ElementType; approve: string }> = {
  share_correction: { label: "Share a correction with the team", icon: Users, approve: "Share" },
  share_approach: { label: "Share an approach with the team", icon: RouteIcon, approve: "Share" },
  eval_case: { label: "Add an evaluation case", icon: FlaskConical, approve: "Add case" },
};

/** Reviewer queue: nothing a user saved reaches other users, or the evaluation set, without a decision here. */
export function ReviewsView({ onError, onChanged }: { onError: (e: unknown) => void; onChanged: (pending: number) => void }) {
  const [reviews, setReviews] = useState<Review[] | null>(null);

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
        <p className="mt-1 text-sm text-muted-foreground">
          Corrections and approaches users confirmed, and answers they marked wrong. Approving shares an item with every user
          as guidance, or keeps a case for the evaluation set (export with <code>learning_cli export</code>).
        </p>
        {!reviews ? (
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

function ReviewCard({ review, onDone, onError }: { review: Review; onDone: () => void; onError: (e: unknown) => void }) {
  const [note, setNote] = useState("");
  const [terms, setTerms] = useState("");
  const [busy, setBusy] = useState(false);
  const kind = KINDS[review.kind];
  const evalCase = review.kind === "eval_case";
  const expected = terms.split(",").map((t) => t.trim()).filter(Boolean);

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
