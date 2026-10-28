"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { BookOpen, Check, ChevronDown, X } from "lucide-react";
import { api, type LessonCard, type LessonQueue, type LessonStatus } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, Card, Input, Select, Spinner } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

const STATUSES: Record<LessonStatus, string> = { proposed: "Proposed", approved: "Approved", rejected: "Rejected" };
const STAGES: Record<string, string> = { all: "All stages", planning: "Planning", tender_design: "Tender design",
  execution: "Execution", learning: "Learning" };
const SOURCE: Record<LessonCard["doc_type"], string> = { ICRR: "IEG review", ICR: "Completion report", PAD: "Appraisal" };

/** Lesson cards from completion reports, IEG reviews and appraisals: only approved cards are used in answers. */
export function LessonReviews({ onError }: { onError: (e: unknown) => void }) {
  const [status, setStatus] = useState<LessonStatus>("proposed");
  const [stage, setStage] = useState("all");
  const [queue, setQueue] = useState<LessonQueue | null>(null);

  const load = useCallback(async () => {
    try { setQueue(await api.lessons(status)); } catch (e) { onError(e); }
  }, [status, onError]);
  useEffect(() => { setQueue(null); void load(); }, [load]);

  const shown = useMemo(() => (queue?.cards ?? []).filter((c) => stage === "all" || c.stages.includes(stage)), [queue, stage]);

  return (
    <div className="mt-6">
      <div className="flex flex-wrap items-center gap-1">
        {(Object.keys(STATUSES) as LessonStatus[]).map((s) => (
          <button key={s} onClick={() => setStatus(s)}
            className={cn("rounded-full px-2.5 py-1 text-xs", status === s ? "bg-primary-soft font-semibold text-primary-strong" : "text-muted-foreground hover:bg-muted")}>
            {STATUSES[s]} {queue && <span className="tabular-nums">{queue.counts[s]}</span>}
          </button>
        ))}
        <Select className="ml-auto h-8 w-auto text-xs" value={stage} onChange={(e) => setStage(e.target.value)} aria-label="Stage">
          {Object.entries(STAGES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
      </div>
      {!queue ? (
        <div className="mt-10 flex justify-center"><Spinner /></div>
      ) : shown.length === 0 ? (
        <Card className="mt-4 p-10 text-center text-sm text-muted-foreground">
          <BookOpen className="mx-auto mb-2 size-6" /> No {STATUSES[status].toLowerCase()} lesson cards{stage !== "all" ? " for this stage" : ""}.
        </Card>
      ) : (
        <div className="mt-4 space-y-3">
          {shown.map((c) => <LessonItem key={c.card_id} card={c} onDone={load} onError={onError} />)}
        </div>
      )}
    </div>
  );
}

function LessonItem({ card, onDone, onError }: { card: LessonCard; onDone: () => void; onError: (e: unknown) => void }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const decide = async (approved: boolean) => {
    setBusy(true);
    try {
      await api.decideLesson(card.card_id, approved, note);
      onDone();
    } catch (e) { onError(e); } finally { setBusy(false); }
  };

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
        <Badge tone={card.doc_type === "ICRR" ? "info" : "neutral"}>{SOURCE[card.doc_type]}</Badge>
        <span>{card.citation}</span>
        <span className="ml-auto flex gap-1">
          {card.stages.map((s) => <Badge key={s} tone="neutral">{STAGES[s] ?? s}</Badge>)}
        </span>
      </div>
      <p className="mt-3 text-sm font-medium">{card.headline}</p>
      {card.text !== card.headline && (
        <button onClick={() => setOpen(!open)} className="mt-1 flex items-center gap-1 text-xs text-primary hover:underline">
          {open ? "Hide" : "Show"} full paragraph <ChevronDown className={cn("size-3 transition-transform", open && "rotate-180")} />
        </button>
      )}
      {open && <p className="mt-2 whitespace-pre-line text-sm text-muted-foreground">{card.text}</p>}
      {card.topics.length > 0 && (
        <p className="mt-2 text-xs text-muted-foreground">Topics: {card.topics.map((t) => t.replaceAll("_", " ")).join(" · ")}</p>
      )}
      {card.status === "proposed" ? (
        <div className="mt-3 flex items-center gap-2">
          <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} placeholder="Note (optional)" />
          <Button size="sm" variant="outline" onClick={() => decide(false)} disabled={busy}><X /> Reject</Button>
          <Button size="sm" onClick={() => decide(true)} disabled={busy}>{busy ? <Spinner /> : <Check />} Approve</Button>
        </div>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">
          {card.status === "approved" ? "Approved" : "Rejected"} by {card.decided_by ?? "unknown"}{card.note ? ` · “${card.note}”` : ""}
        </p>
      )}
    </Card>
  );
}
