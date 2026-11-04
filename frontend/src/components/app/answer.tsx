"use client";

// The assistant's answer card in the chat, and its "in progress" version (PendingAnswer). A finished answer is drawn
// from its investigation (claims, evidence, limitations) when one exists, else from the stored turn text.

import { useState } from "react";
import { Bookmark, BookmarkCheck, Calculator, ChevronDown, ClipboardList, Copy, Eye, FileSearch, Info, Library, PencilLine, Sparkles } from "lucide-react";
import type { FeedbackState, Investigation, LiveStep, Turn } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, Card, Spinner } from "@/components/ui/primitives";
import { Tooltip } from "@/components/ui/overlays";
import { StatusBadge } from "./status";
import { PredictionCards } from "./prediction-cards";
import { TenderChecks } from "./tender-checks";
import { BriefView } from "./brief";
import { EvidencePanel } from "./evidence-panel";
import { AppliedLearning, CorrectionForm, FeedbackNote, RateButtons, type SendFeedback } from "./feedback";
import { cn, relativeTime } from "@/lib/utils";

type Props = {
  turn: Turn;
  investigation?: Investigation;
  bookmarked: boolean;
  onToggleBookmark: () => void;
  onAssess: () => void;
  onPlan: () => void;
  onTender: () => void;
  onInvestigate: (question: string) => void;
  feedback?: FeedbackState;
  revised: boolean;                       // this answer re-ran the question after the user's correction
  onFeedback?: SendFeedback;              // absent when feedback is not enabled
  onWatch: (projectId: string) => void;
};

const ANSWER_BODY = "mt-3 space-y-2.5 text-[15px] leading-relaxed";
const ANSWER_FOOTER = "mt-4 flex min-h-11 items-center border-t border-border pt-3";
const OPENING = "text-muted-foreground";
// The opening the API puts before an answer that covers only part of the question; it is shown muted.
const GAP_OPENING = /^(No evidence found for part of the question:[\s\S]*?What the evidence shows:)\s*/;

/** An answer's text as the card lays it out: the no-evidence opening on its own, then one paragraph per blank line. */
export function paragraphs(text: string): string[] {
  const opening = text.match(GAP_OPENING);
  const rest = opening ? text.slice(opening[0].length) : text;
  return [...(opening ? [opening[1]] : []), ...rest.split(/\n{2,}/)].map((p) => p.trim()).filter(Boolean);
}

/** The round assistant icon left of an answer; it pulses while the answer is being worked on. */
function AnswerAvatar({ working = false }: { working?: boolean }) {
  return (
    <div className="mt-1 grid size-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-[#0078d4] to-[#005a9e] text-white shadow-sm">
      <Sparkles className={cn("size-4", working && "animate-pulse")} />
    </div>
  );
}

/** One finished answer: status, text with numbered citations (or a sectioned brief), model and tender-check cards,
 *  notes, applied learning, and the footer actions (sources, project watch, feedback, copy, bookmark). */
export function AnswerCard(props: Props) {
  const { turn, investigation, bookmarked, onToggleBookmark, onAssess, onPlan, onInvestigate } = props;
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);
  const [notesOpen, setNotesOpen] = useState(false);
  const evidence = investigation?.evidence ?? [];
  // Citation numbers follow the order of the evidence list, so [2] in the text is the second source in the panel.
  const number = (id: string) => evidence.findIndex((e) => e.evidence_id === id) + 1;
  // A run the user stopped is shown as "Stopped", whatever status the partial investigation has.
  const stopped = investigation?.error_codes.includes("cancelled_by_user");
  const status = stopped ? "stopped" : investigation?.status ?? turn.status;
  // Claims with a section belong to a planning brief, which has its own layout.
  const brief = investigation?.claims.some((c) => c.section) ?? false;
  // Up to three cited projects get a "Watch" shortcut in the footer.
  const projects = [...new Set(evidence.flatMap((e) => e.project_ids))].slice(0, 3);
  const checked = new Set(evidence.filter((e) => e.kind === "tender_check").map((e) => e.evidence_id));
  const claims = (investigation?.claims ?? []).filter((c) => !c.evidence_ids.every((id) => checked.has(id)));   // shown as checks
  const opening = investigation?.message.match(GAP_OPENING)?.[1];
  /** The small numbered button after a claim; it opens the sources panel on that source. */
  const cite = (id: string) => number(id) > 0 && (
    <button key={id} onClick={() => openSource(id)}
      className="ml-1 inline-grid h-[18px] min-w-[18px] place-items-center rounded-md bg-primary-soft px-1 align-text-top text-[10px] font-bold text-primary-strong hover:bg-primary hover:text-white">
      {number(id)}
    </button>
  );

  const openSource = (id: string | null) => {
    setFocus(id);
    setSourcesOpen(true);
  };
  // The needs_*_details statuses mean the API wants structured input; their buttons open the matching form.
  // A thumbs-up is sent at once, while a thumbs-down first opens the correction form at the bottom of the card.
  return (
    <div id={`turn-${turn.seq}`} className="group flex gap-3">
      <AnswerAvatar />
      <Card className="min-w-0 flex-1 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={status} />
          {props.revised && <Badge tone="info"><PencilLine /> Revised answer</Badge>}
          {investigation && investigation.tasks.length > 0 && (
            <span className="text-[11px] text-muted-foreground">
              {investigation.tasks.map((t) => t.worker.replace("_", " ")).filter((w, i, a) => a.indexOf(w) === i).join(" · ")}
            </span>
          )}
          <span className="ml-auto text-[11px] text-muted-foreground">{relativeTime(turn.created_at)}</span>
        </div>

        {investigation && brief ? (
          <BriefView investigation={investigation} citation={cite} onInvestigate={onInvestigate} />
        ) : (
          <div className={ANSWER_BODY}>
            {investigation && investigation.claims.length > 0 ? (
              <>
                {opening && <p className={OPENING}>{opening}</p>}
                {claims.map((claim, i) => <p key={i}>{claim.text}{claim.evidence_ids.map(cite)}</p>)}
              </>
            ) : (
              paragraphs(investigation?.message || turn.text).map((p, i) => <p key={i} className="whitespace-pre-line">{p}</p>)
            )}
          </div>
        )}

        {investigation && !brief && <TenderChecks evidence={evidence} />}
        {investigation && !brief && <PredictionCards evidence={evidence} />}

        {turn.status === "needs_project_details" && (
          <Button size="sm" className="mt-4" onClick={onPlan}><ClipboardList /> Plan a new project</Button>
        )}

        {turn.status === "needs_tender_details" && (
          <Button size="sm" className="mt-4" onClick={props.onTender}><FileSearch /> Check tender design</Button>
        )}

        {turn.status === "needs_package_details" && (
          <Button size="sm" className="mt-4" onClick={onAssess}><Calculator /> Assess package</Button>
        )}

        {investigation && investigation.limitations.length > 0 && (
          <div className="mt-4">
            <button onClick={() => setNotesOpen(!notesOpen)} className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground">
              <Info className="size-3.5" /> Notes and limitations ({investigation.limitations.length})
              <ChevronDown className={cn("size-3.5 transition-transform", notesOpen && "rotate-180")} />
            </button>
            {notesOpen && (
              <ul className="mt-2 space-y-1 rounded-lg bg-muted p-3 text-xs text-muted-foreground">
                {investigation.limitations.map((l) => <li key={l}>• {l}</li>)}
              </ul>
            )}
          </div>
        )}

        <AppliedLearning items={investigation?.applied_learning ?? []} />

        <div className={cn(ANSWER_FOOTER, "gap-1")}>
          {evidence.length > 0 && (
            <Button variant="ghost" size="sm" onClick={() => openSource(null)}>
              <Library /> {evidence.length} source{evidence.length > 1 ? "s" : ""}
            </Button>
          )}
          {projects.map((p) => (
            <Button key={p} variant="ghost" size="sm" onClick={() => props.onWatch(p)}><Eye /> Watch {p}</Button>
          ))}
          <div className="ml-auto flex gap-1 opacity-70 transition-opacity group-hover:opacity-100">
            {props.onFeedback && (
              <RateButtons state={props.feedback} onUp={() => props.onFeedback?.("up")} onDown={() => setCorrecting(true)} />
            )}
            <Tooltip content="Copy answer">
              <Button variant="ghost" size="icon" onClick={() => navigator.clipboard.writeText(investigation?.message || turn.text)}><Copy /></Button>
            </Tooltip>
            <Tooltip content={bookmarked ? "Remove bookmark" : "Bookmark this answer"}>
              <Button variant="ghost" size="icon" onClick={onToggleBookmark} className={cn(bookmarked && "text-primary")}>
                {bookmarked ? <BookmarkCheck /> : <Bookmark />}
              </Button>
            </Tooltip>
          </div>
        </div>
        {props.onFeedback && <FeedbackNote state={props.feedback} revised={props.revised} />}
        {correcting && props.onFeedback && (
          <CorrectionForm onCancel={() => setCorrecting(false)}
            onSubmit={async (rating, reasons, correction) => { setCorrecting(false); await props.onFeedback?.(rating, reasons, correction); }} />
        )}
      </Card>
      <EvidencePanel open={sourcesOpen} onOpenChange={setSourcesOpen} evidence={evidence} focusId={focus} />
    </div>
  );
}

// The mark before each finished live step; a running step shows a spinner instead.
const STEP_ICON: Record<LiveStep["state"], string> = { running: "", done: "✓", failed: "✕", no_result: "–" };

/** The running answer, in the same card as the finished one: the steps while the work runs, then the answer's paragraphs
 *  as they are written (a cursor at the end), so the card only gains its citations and buttons when it is checked. */
export function PendingAnswer({ label, steps = [], text = "" }: { label: string; steps?: LiveStep[]; text?: string }) {
  const parts = paragraphs(text);
  const opening = GAP_OPENING.test(text);
  const finished = steps.filter((s) => s.state !== "running");
  const seconds = finished.reduce((total, s) => total + (s.ms ?? 0), 0) / 1000;
  return (
    <div className="flex gap-3">
      <AnswerAvatar working />
      <Card className="min-w-0 flex-1 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="info"><Spinner className="size-3" /> {text ? "Writing the answer" : steps.find((s) => s.state === "running")?.label ?? label}</Badge>
          {text && finished.length > 0 && (
            <span className="text-[11px] text-muted-foreground">{finished.length} step{finished.length > 1 ? "s" : ""} · {seconds.toFixed(1)}s</span>
          )}
        </div>
        {!text && steps.length > 0 && (
          <ul className="mt-3 space-y-1 text-sm">
            {steps.map((s) => (
              <li key={s.key} className={cn("flex items-center gap-2", s.state === "running" ? "text-primary-strong" : "text-muted-foreground")}>
                <span className="grid w-4 place-items-center">{s.state === "running" ? <Spinner className="size-3" /> : STEP_ICON[s.state]}</span>
                <span>{s.label}{s.state === "no_result" ? " (found nothing)" : s.state === "failed" ? " (failed)" : ""}</span>
                {s.ms !== undefined && s.state !== "running" && <span className="ml-auto tabular-nums text-xs">{(s.ms / 1000).toFixed(1)}s</span>}
              </li>
            ))}
          </ul>
        )}
        {text ? (
          <div className={ANSWER_BODY}>
            {parts.map((p, i) => (
              <p key={i} className={cn("whitespace-pre-line", opening && i === 0 && OPENING)}>
                {p}{i === parts.length - 1 && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-primary align-middle" />}
              </p>
            ))}
          </div>
        ) : steps.length === 0 && (
          <div className="mt-3 space-y-2">
            {[92, 76, 54].map((w) => <div key={w} className="h-2.5 animate-pulse rounded-full bg-muted" style={{ width: `${w}%` }} />)}
          </div>
        )}
        <div className={cn(ANSWER_FOOTER, "text-xs text-muted-foreground")}>
          {text ? "Checking each statement against its sources…" : "\u00a0"}
        </div>
      </Card>
    </div>
  );
}
