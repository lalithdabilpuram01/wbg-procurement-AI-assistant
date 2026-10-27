"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, Calculator, Database, FileSearch, Landmark, PanelLeftOpen, ShieldCheck, TableProperties } from "lucide-react";
import type { ConversationDetail, Investigation } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/primitives";
import { Tooltip } from "@/components/ui/overlays";
import { AnswerCard, PendingAnswer } from "./answer";
import { cn } from "@/lib/utils";

const SUGGESTIONS = [
  { icon: Database, title: "Contract awards by method", text: "How many contract awards were made per procurement method in Ethiopia?" },
  { icon: FileSearch, title: "Lessons from documents", text: "What lessons were learned about road works procurement in Sri Lanka?" },
  { icon: Landmark, title: "Cross-check data and documents",
    text: "Investigate the number of contract awards per procurement method in Ethiopia, and then summarize the procurement delays reported in the documents for the Ethiopia Expressway project." },
];

type ChatProps = {
  title: string | null;
  detail: ConversationDetail | null;
  investigations: Record<string, Investigation>;
  pending: string | null;
  bookmarkedSeqs: Set<number>;
  sidebarOpen: boolean;
  scrollToSeq: number | null;
  onOpenSidebar: () => void;
  onSend: (text: string, execute: boolean) => void;
  onAssess: () => void;
  onToggleBookmark: (seq: number) => void;
  onDecide: (requestId: string, approvalId: string, approved: boolean) => Promise<void>;
};

export function ChatView(props: ChatProps) {
  const { detail, investigations, pending } = props;
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (props.scrollToSeq !== null) {
      document.getElementById(`turn-${props.scrollToSeq}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      bottom.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [detail, pending, props.scrollToSeq]);

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-card/80 px-4 backdrop-blur">
        {!props.sidebarOpen && (
          <Button variant="ghost" size="icon" onClick={props.onOpenSidebar} aria-label="Show sidebar"><PanelLeftOpen /></Button>
        )}
        <h1 className="min-w-0 truncate text-sm font-semibold">{props.title ?? "New chat"}</h1>
        <div className="ml-auto flex items-center gap-3">
          <Tooltip content="TableLens (Card-to-Cell table retrieval): finds the right PDF table, then the exact cell. Demo pipeline – coming soon.">
            <label className="flex cursor-not-allowed items-center gap-2 text-xs text-muted-foreground">
              <TableProperties className="size-4" /> TableLens
              <span className="relative inline-flex h-5 w-9 items-center rounded-full bg-muted opacity-60">
                <span className="ml-0.5 size-4 rounded-full bg-card shadow" />
              </span>
            </label>
          </Tooltip>
          <Button size="sm" onClick={props.onAssess}><Calculator /> Assess package</Button>
        </div>
      </header>

      <div className="scrollbar-thin flex-1 overflow-y-auto">
        {!detail || (detail.turns.length === 0 && !pending) ? (
          <Welcome onSend={(t) => props.onSend(t, false)} onAssess={props.onAssess} />
        ) : (
          <div className="mx-auto max-w-4xl space-y-6 px-4 py-8">
            {detail.turns.map((turn) =>
              turn.role === "user" ? (
                <div key={turn.seq} id={`turn-${turn.seq}`} className="flex justify-end">
                  <div className="max-w-[80%] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-[15px] text-primary-foreground shadow-sm">
                    {turn.text}
                  </div>
                </div>
              ) : (
                <AnswerCard key={turn.seq} turn={turn}
                  investigation={turn.request_id ? investigations[turn.request_id] : undefined}
                  bookmarked={props.bookmarkedSeqs.has(turn.seq)} onToggleBookmark={() => props.onToggleBookmark(turn.seq)}
                  onDecide={(approvalId, approved) => props.onDecide(turn.request_id!, approvalId, approved)} />
              ),
            )}
            {pending && <PendingAnswer label={pending} />}
            <div ref={bottom} />
          </div>
        )}
      </div>

      <Composer disabled={pending !== null} onSend={props.onSend} />
    </div>
  );
}

function Composer({ disabled, onSend }: { disabled: boolean; onSend: (text: string, execute: boolean) => void }) {
  const [text, setText] = useState("");
  const [execute, setExecute] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);

  const send = () => {
    if (!text.trim() || disabled) return;
    onSend(text.trim(), execute);
    setText("");
  };
  useEffect(() => {
    if (area.current) {
      area.current.style.height = "auto";
      area.current.style.height = `${Math.min(area.current.scrollHeight, 200)}px`;
    }
  }, [text]);

  return (
    <div className="shrink-0 px-4 pb-4 pt-2">
      <div className="mx-auto max-w-4xl rounded-2xl border border-input bg-card shadow-sm focus-within:ring-2 focus-within:ring-ring/40">
        <textarea ref={area} value={text} rows={1} disabled={disabled} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder="Ask about procurement data or project documents…"
          className="block w-full resize-none bg-transparent px-4 pt-3.5 text-[15px] placeholder:text-muted-foreground/70 focus:outline-none" />
        <div className="flex items-center gap-2 px-3 pb-2.5 pt-1">
          <Tooltip content="Allow running validated, read-only SQL on Databricks. You approve every query before it runs.">
            <button onClick={() => setExecute(!execute)}
              className={cn("flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors",
                execute ? "border-primary bg-primary-soft text-primary-strong" : "border-border text-muted-foreground hover:bg-muted")}>
              <Database className="size-3.5" /> Run SQL {execute ? "on" : "off"}
            </button>
          </Tooltip>
          <span className="hidden items-center gap-1 text-[11px] text-muted-foreground sm:flex">
            <ShieldCheck className="size-3.5" /> Answers cite their sources
          </span>
          <Button size="icon" className="ml-auto rounded-full" onClick={send} disabled={disabled || !text.trim()} aria-label="Send">
            <ArrowUp />
          </Button>
        </div>
      </div>
    </div>
  );
}

function Welcome({ onSend, onAssess }: { onSend: (text: string) => void; onAssess: () => void }) {
  return (
    <div className="mx-auto flex max-w-4xl flex-col items-center px-4 py-14 text-center">
      <div className="grid size-14 place-items-center rounded-2xl bg-gradient-to-br from-[#2b88d8] to-[#005a9e] text-xl font-bold text-white shadow-lg">PI</div>
      <h2 className="mt-5 text-2xl font-semibold tracking-tight">How can I help with procurement today?</h2>
      <p className="mt-2 max-w-xl text-sm text-muted-foreground">
        Ask about contract awards and notices, search project appraisal, status and completion reports, or assess a new
        package with timeline and risk models. Every answer is checked against its sources.
      </p>
      <div className="mt-8 grid w-full gap-3 sm:grid-cols-2">
        {SUGGESTIONS.map((s) => (
          <button key={s.title} onClick={() => onSend(s.text)} className="text-left">
            <Card className="h-full p-4 transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md">
              <s.icon className="size-5 text-primary" />
              <p className="mt-2 text-sm font-semibold">{s.title}</p>
              <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{s.text}</p>
            </Card>
          </button>
        ))}
        <button onClick={onAssess} className="text-left">
          <Card className="h-full border-primary/30 bg-primary-soft/50 p-4 transition-all hover:-translate-y-0.5 hover:shadow-md">
            <Calculator className="size-5 text-primary" />
            <p className="mt-2 text-sm font-semibold">Assess a new package</p>
            <p className="mt-1 text-xs text-muted-foreground">Tender duration, amendment and re-bidding risk, plus lessons from past projects.</p>
          </Card>
        </button>
      </div>
    </div>
  );
}
