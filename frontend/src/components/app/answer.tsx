"use client";

import { useState } from "react";
import { Bookmark, BookmarkCheck, Calculator, ChevronDown, ClipboardList, Copy, Eye, FileSearch, Info, Library, PencilLine, ShieldQuestion, Sparkles } from "lucide-react";
import type { FeedbackState, Investigation, Turn } from "@/lib/api";
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
  onDecide: (approvalId: string, approved: boolean) => Promise<void>;
  onAssess: () => void;
  onPlan: () => void;
  onTender: () => void;
  onInvestigate: (question: string) => void;
  feedback?: FeedbackState;
  revised: boolean;                       // this answer re-ran the question after the user's correction
  onFeedback?: SendFeedback;              // absent when feedback is not enabled
  onManageMemory: () => void;
  onWatch: (projectId: string) => void;
};

export function AnswerCard(props: Props) {
  const { turn, investigation, bookmarked, onToggleBookmark, onDecide, onAssess, onPlan, onInvestigate } = props;
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);
  const [notesOpen, setNotesOpen] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const evidence = investigation?.evidence ?? [];
  const number = (id: string) => evidence.findIndex((e) => e.evidence_id === id) + 1;
  const stopped = investigation?.error_codes.includes("cancelled_by_user");
  const status = stopped ? "stopped" : investigation?.status ?? turn.status;
  const brief = investigation?.claims.some((c) => c.section) ?? false;
  const projects = [...new Set(evidence.flatMap((e) => e.project_ids))].slice(0, 3);
  const checked = new Set(evidence.filter((e) => e.kind === "tender_check").map((e) => e.evidence_id));
  const claims = (investigation?.claims ?? []).filter((c) => !c.evidence_ids.every((id) => checked.has(id)));   // shown as checks
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
  const decide = async (approved: boolean) => {
    if (!investigation?.pending_approval) return;
    setDeciding(true);
    try {
      await onDecide(investigation.pending_approval.approval_id, approved);
    } finally {
      setDeciding(false);
    }
  };

  return (
    <div id={`turn-${turn.seq}`} className="group flex gap-3">
      <div className="mt-1 grid size-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-[#0078d4] to-[#005a9e] text-white shadow-sm">
        <Sparkles className="size-4" />
      </div>
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
          <div className="mt-3 space-y-2.5 text-[15px] leading-relaxed">
            {investigation && investigation.claims.length > 0 ? (
              claims.map((claim, i) => <p key={i}>{claim.text}{claim.evidence_ids.map(cite)}</p>)
            ) : (
              <p className="whitespace-pre-line">{investigation?.message || turn.text}</p>
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

        {investigation?.pending_approval && (
          <div className="mt-4 rounded-xl border border-primary/30 bg-primary-soft/60 p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-primary-strong">
              <ShieldQuestion className="size-4" /> Approval needed to run validated SQL on Databricks
            </div>
            <p className="mt-1.5 text-sm text-muted-foreground">{investigation.pending_approval.summary}</p>
            <div className="mt-3 flex gap-2">
              <Button size="sm" onClick={() => decide(true)} disabled={deciding}>{deciding ? <Spinner /> : null} Approve and run</Button>
              <Button size="sm" variant="outline" onClick={() => decide(false)} disabled={deciding}>Reject</Button>
            </div>
          </div>
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

        <AppliedLearning items={investigation?.applied_learning ?? []} onManage={props.onManageMemory} />

        <div className="mt-4 flex items-center gap-1 border-t border-border pt-3">
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

export function PendingAnswer({ label }: { label: string }) {
  return (
    <div className="flex gap-3">
      <div className="mt-1 grid size-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-[#0078d4] to-[#005a9e] text-white">
        <Spinner className="size-3.5" />
      </div>
      <Card className="flex-1 p-5">
        <p className="text-sm font-medium text-primary-strong">{label}</p>
        <div className="mt-3 space-y-2">
          {[92, 76, 54].map((w) => <div key={w} className="h-2.5 animate-pulse rounded-full bg-muted" style={{ width: `${w}%` }} />)}
        </div>
      </Card>
    </div>
  );
}
