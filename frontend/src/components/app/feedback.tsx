"use client";

import { useState } from "react";
import { Brain, ChevronDown, ThumbsDown, ThumbsUp, Users } from "lucide-react";
import type { FeedbackReason, FeedbackState, LearnedItem } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Spinner, Textarea } from "@/components/ui/primitives";
import { Tooltip } from "@/components/ui/overlays";
import { cn } from "@/lib/utils";

const REASONS: { id: FeedbackReason; label: string }[] = [
  { id: "wrong_number", label: "Wrong number" },
  { id: "wrong_scope", label: "Wrong filter or period" },
  { id: "wrong_source", label: "Wrong source or table" },
  { id: "missing_information", label: "Something is missing" },
  { id: "other", label: "Other" },
];

export type SendFeedback = (rating: "up" | "down", reasons?: FeedbackReason[], correction?: string) => Promise<void>;

/** Thumbs up / down under an answer; the current rating stays highlighted. */
export function RateButtons({ state, onUp, onDown }: { state?: FeedbackState; onUp: () => void; onDown: () => void }) {
  return (
    <>
      <Tooltip content="Good answer – saves how it was answered for similar questions">
        <Button variant="ghost" size="icon" onClick={onUp} aria-label="Good answer" aria-pressed={state?.rating === "up"}
          className={cn(state?.rating === "up" && "text-success")}><ThumbsUp /></Button>
      </Tooltip>
      <Tooltip content="Something is wrong – correct it and get a revised answer">
        <Button variant="ghost" size="icon" onClick={onDown} aria-label="Something is wrong" aria-pressed={state?.rating === "down"}
          className={cn(state?.rating === "down" && "text-danger")}><ThumbsDown /></Button>
      </Tooltip>
    </>
  );
}

/** What was wrong, and optionally the correction: with a correction the question is re-run at once. */
export function CorrectionForm({ onSubmit, onCancel }: { onSubmit: SendFeedback; onCancel: () => void }) {
  const [reasons, setReasons] = useState<FeedbackReason[]>([]);
  const [correction, setCorrection] = useState("");
  const [busy, setBusy] = useState(false);
  const toggle = (id: FeedbackReason) => setReasons((r) => (r.includes(id) ? r.filter((x) => x !== id) : [...r, id]));
  const ready = reasons.length > 0 || correction.trim().length > 0;

  const submit = async () => {
    setBusy(true);
    try {
      await onSubmit("down", reasons, correction.trim());
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 rounded-xl border border-border bg-muted/50 p-4">
      <p className="text-sm font-semibold">What was wrong?</p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {REASONS.map((r) => (
          <button key={r.id} type="button" onClick={() => toggle(r.id)} aria-pressed={reasons.includes(r.id)}
            className={cn("rounded-full border px-2.5 py-1 text-xs transition-colors",
              reasons.includes(r.id) ? "border-primary bg-primary-soft text-primary-strong" : "border-border bg-card hover:bg-muted")}>
            {r.label}
          </button>
        ))}
      </div>
      <Textarea value={correction} onChange={(e) => setCorrection(e.target.value)} maxLength={500} rows={2} className="mt-3"
        placeholder="Correction (optional), e.g. “use fiscal years 2020–2024 only” or “include closed projects”" />
      <p className="mt-1.5 text-[11px] text-muted-foreground">
        With a correction, the question is answered again right away. If the revised answer is right, a thumbs-up saves the
        correction for your similar questions.
      </p>
      <div className="mt-3 flex justify-end gap-2">
        <Button size="sm" variant="outline" onClick={onCancel} disabled={busy}>Cancel</Button>
        <Button size="sm" onClick={submit} disabled={!ready || busy}>
          {busy && <Spinner />} {correction.trim() ? "Correct and re-run" : "Send feedback"}
        </Button>
      </div>
    </div>
  );
}

/** One line under the rating buttons that says what happened to the feedback. */
export function FeedbackNote({ state, revised }: { state?: FeedbackState; revised: boolean }) {
  const text = !state
    ? revised ? "Does this fix it? A thumbs-up saves your correction for similar questions." : null
    : state.rating === "up" ? "Thanks – noted."
    : state.status === "revising" ? "Re-running with your correction…"
    : state.status === "revised" ? "Corrected – see the revised answer below."
    : state.status === "validated" ? "Correction confirmed and saved to your memory."
    : "Thanks – your feedback was recorded.";
  return text ? <p className="mt-2 text-[11px] text-muted-foreground">{text}</p> : null;
}

/** Which saved corrections, approaches or team notes guided this answer (they never supply numbers). */
export function AppliedLearning({ items, onManage }: { items: LearnedItem[]; onManage: () => void }) {
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;
  return (
    <div className="mt-3">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1.5 text-xs font-medium text-primary-strong hover:underline">
        <Brain className="size-3.5" /> Guided by {items.length} saved item{items.length > 1 ? "s" : ""}
        <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="mt-2 space-y-1.5 rounded-lg bg-primary-soft/50 p-3 text-xs">
          {items.map((i) => (
            <p key={i.item_id} className="flex gap-1.5">
              {i.scope === "shared" ? <Users className="mt-0.5 size-3.5 shrink-0" /> : <Brain className="mt-0.5 size-3.5 shrink-0" />}
              <span><span className="font-semibold">{i.kind === "correction" ? "Correction" : "Approach"}:</span> {i.statement}</span>
            </p>
          ))}
          <button onClick={onManage} className="pt-1 font-medium text-primary-strong hover:underline">Manage memory</button>
        </div>
      )}
    </div>
  );
}
