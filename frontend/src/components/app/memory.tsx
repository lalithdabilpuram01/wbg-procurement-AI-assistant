"use client";

import { useCallback, useEffect, useState } from "react";
import { Brain, Route as RouteIcon, Trash2, Users } from "lucide-react";
import { api, type LearnedItem, type Memory, type Preferences } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, Card, Input, Label, Select, Spinner } from "@/components/ui/primitives";
import { Tooltip } from "@/components/ui/overlays";
import { cn, relativeTime } from "@/lib/utils";

/** The user's memory: saved preferences, and the corrections and approaches that guide similar questions. */
export function MemoryView({ reviewer, onError, onPreferences }:
  { reviewer: boolean; onError: (e: unknown) => void; onPreferences: (p: Preferences) => void }) {
  const [memory, setMemory] = useState<Memory | null>(null);

  const load = useCallback(async () => {
    try { setMemory(await api.memory()); } catch (e) { onError(e); }
  }, [onError]);
  useEffect(() => { void load(); }, [load]);

  const remove = async (item: LearnedItem) => {
    try { await api.removeItem(item.item_id); await load(); } catch (e) { onError(e); }
  };
  const corrections = memory?.items.filter((i) => i.kind === "correction") ?? [];
  const approaches = memory?.items.filter((i) => i.kind === "approach") ?? [];

  return (
    <div className="scrollbar-thin h-full flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl px-4 py-8">
        <h1 className="text-xl font-semibold tracking-tight">Memory</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          What the assistant has learned from your confirmed feedback. It guides the scope and method of similar questions and is
          never used as a source of numbers: every figure still comes from cited data or documents.
        </p>
        {!memory ? (
          <div className="mt-10 flex justify-center"><Spinner /></div>
        ) : (
          <>
            <PreferencesCard initial={memory.preferences} onSaved={(p) => { setMemory({ ...memory, preferences: p }); onPreferences(p); }}
              onError={onError} />
            <ItemSection title="Corrections" empty="Correct an answer and confirm the revised one with a thumbs-up to save a correction."
              items={corrections} reviewer={reviewer} onRemove={remove} />
            <ItemSection title="Approaches" empty="A thumbs-up on a good answer saves how it was found (steps, tables and filters)."
              items={approaches} reviewer={reviewer} onRemove={remove} />
          </>
        )}
      </div>
    </div>
  );
}

function PreferencesCard({ initial, onSaved, onError }:
  { initial: Preferences; onSaved: (p: Preferences) => void; onError: (e: unknown) => void }) {
  const [draft, setDraft] = useState(initial);
  const [busy, setBusy] = useState(false);
  const years = draft.default_fiscal_years;
  const setYear = (index: 0 | 1, value: string) => {
    const next: [number, number] = years ? [...years] : [2015, 2024];
    next[index] = Number(value);
    setDraft({ ...draft, default_fiscal_years: value ? next : null });
  };
  const save = async () => {
    setBusy(true);
    try { onSaved(await api.savePreferences(draft)); } catch (e) { onError(e); } finally { setBusy(false); }
  };

  return (
    <Card className="mt-6 p-5">
      <p className="text-sm font-semibold">Preferences</p>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label>Default period when a question names none (fiscal years)</Label>
          <div className="flex items-center gap-2">
            <Input type="number" min={1990} max={2035} placeholder="From" value={years?.[0] ?? ""} onChange={(e) => setYear(0, e.target.value)} />
            <span className="text-muted-foreground">–</span>
            <Input type="number" min={1990} max={2035} placeholder="To" value={years?.[1] ?? ""} onChange={(e) => setYear(1, e.target.value)} />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label>Amounts</Label>
          <Select value={draft.amount_unit ?? ""} onChange={(e) => setDraft({ ...draft, amount_unit: (e.target.value || null) as Preferences["amount_unit"] })}>
            <option value="">No preference</option>
            <option value="usd">US dollars</option>
            <option value="usd_millions">USD millions</option>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label>Answer length</Label>
          <Select value={draft.answer_length ?? ""} onChange={(e) => setDraft({ ...draft, answer_length: (e.target.value || null) as Preferences["answer_length"] })}>
            <option value="">No preference</option>
            <option value="brief">Brief: key figures first</option>
            <option value="detailed">Detailed: with breakdowns</option>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label>TableLens for PDF tables</Label>
          <button type="button" role="switch" aria-checked={!!draft.table_lens}
            onClick={() => setDraft({ ...draft, table_lens: !draft.table_lens })}
            className="flex h-9 items-center gap-2 text-sm">
            <span className={cn("relative inline-flex h-5 w-9 items-center rounded-full transition-colors", draft.table_lens ? "bg-primary" : "bg-muted")}>
              <span className={cn("size-4 rounded-full bg-card shadow transition-transform", draft.table_lens ? "translate-x-[18px]" : "translate-x-0.5")} />
            </span>
            {draft.table_lens ? "On by default" : "Off by default"}
          </button>
        </div>
      </div>
      <div className="mt-4 flex justify-end">
        <Button size="sm" onClick={save} disabled={busy}>{busy && <Spinner />} Save preferences</Button>
      </div>
    </Card>
  );
}

function ItemSection({ title, empty, items, reviewer, onRemove }:
  { title: string; empty: string; items: LearnedItem[]; reviewer: boolean; onRemove: (i: LearnedItem) => void }) {
  return (
    <section className="mt-8">
      <h2 className="text-sm font-semibold">{title} <span className="font-normal text-muted-foreground">({items.length})</span></h2>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">{empty}</p>
      ) : (
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {items.map((item) => (
            <Card key={item.item_id} className={cn("flex flex-col p-4", item.expired && "opacity-60")}>
              <div className="flex items-center gap-2">
                {item.scope === "shared"
                  ? <Badge tone="info"><Users /> Team</Badge>
                  : <Badge><Brain /> Personal</Badge>}
                {item.expired && <Badge tone="warning">Paused: unused for 90 days</Badge>}
                {(item.scope === "personal" || reviewer) && (
                  <Tooltip content={item.scope === "shared" ? "Remove for the whole team" : "Forget this"}>
                    <Button variant="ghost" size="icon" className="ml-auto" onClick={() => onRemove(item)} aria-label="Remove">
                      <Trash2 />
                    </Button>
                  </Tooltip>
                )}
              </div>
              <p className="mt-2 text-sm font-medium">{item.statement}</p>
              <p className="mt-1 flex gap-1.5 text-xs text-muted-foreground">
                <RouteIcon className="mt-0.5 size-3.5 shrink-0" /> For questions like “{item.question}”
              </p>
              <p className="mt-auto pt-3 text-[11px] text-muted-foreground">
                Used {item.use_count} time{item.use_count === 1 ? "" : "s"}
                {item.last_used_at ? ` · last ${relativeTime(item.last_used_at)}` : ""} · saved {relativeTime(item.created_at)}
              </p>
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}
