"use client";

// The "Check a tender design" dialog. An uploaded file is first turned into text by POST /drafts/text so the user can
// review it; AppShell then sends the form to POST /conversations/{id}/tenders.

import { useRef, useState } from "react";
import { FileSearch, Upload } from "lucide-react";
import { api, type TenderInput } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Spinner, Textarea } from "@/components/ui/primitives";
import { Dialog, DialogContent } from "@/components/ui/overlays";
import { CATEGORIES, METHODS, inDays } from "./package-dialog";

// The API accepts at most 20,000 characters of draft (TenderSpec.draft_text) and cuts extracted text to that length.
const MAX_DRAFT = 20_000;

/** The file's bytes as base64 for the JSON upload. It converts in 32 KB chunks, because spreading a whole large
 *  file into String.fromCharCode would exceed the argument limit. */
async function asBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

/** A tender design to check: the package basics and the draft (pasted, or read from an uploaded PDF / Word / text file). */
export function TenderDialog({ open, onOpenChange, onSubmit }:
  { open: boolean; onOpenChange: (open: boolean) => void; onSubmit: (input: TenderInput) => Promise<void> }) {
  const [input, setInput] = useState<TenderInput>({
    project_id: "", procurement_method: "Request for Bids", procurement_category: "Works", review_type: "Prior",
    bid_window_days: 45, tender_date: inDays(30), estimated_amount_usd: null, lots: 1, draft_text: "",
  });
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const set = <K extends keyof TenderInput>(key: K, value: TenderInput[K]) => setInput((p) => ({ ...p, [key]: value }));
  const validProject = /^P\d{6}$/.test(input.project_id);

  /** Reads an uploaded draft through the API (nothing is stored there) and puts the text in the box for review. */
  const upload = async (picked: File | undefined) => {
    if (!picked) return;
    setReading(true);
    setError(null);
    try {
      const { text } = await api.draftText(picked.name, await asBase64(picked));
      set("draft_text", text);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The file could not be read.");
    } finally {
      setReading(false);
      // Cleared so picking the same file again still fires onChange.
      if (file.current) file.current.value = "";
    }
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit(input);
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The check could not be started.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Check a tender design"
        description="The draft's bid window, method, review type, packaging and qualification terms are compared with similar past packages and the project's documents.">
        <form onSubmit={submit} className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="t-project">Project ID</Label>
            <Input id="t-project" placeholder="e.g. P148850" value={input.project_id} autoFocus
              onChange={(e) => set("project_id", e.target.value.trim().toUpperCase())} />
            {input.project_id && !validProject && <p className="text-xs text-danger">Use P followed by 6 digits.</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="t-amount">Estimated amount (US$)</Label>
            <Input id="t-amount" type="number" min={1} placeholder="e.g. 12000000" value={input.estimated_amount_usd ?? ""}
              onChange={(e) => set("estimated_amount_usd", e.target.value ? Number(e.target.value) : null)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="t-method">Procurement method</Label>
            <Select id="t-method" value={input.procurement_method} onChange={(e) => set("procurement_method", e.target.value)}>
              {METHODS.map((m) => <option key={m}>{m}</option>)}
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="t-category">Category</Label>
            <Select id="t-category" value={input.procurement_category} onChange={(e) => set("procurement_category", e.target.value)}>
              {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
            </Select>
          </div>
          <div className="grid grid-cols-3 gap-3 col-span-2">
            <div className="space-y-1.5">
              <Label htmlFor="t-review">Review type</Label>
              <Select id="t-review" value={input.review_type} onChange={(e) => set("review_type", e.target.value)}>
                <option>Prior</option><option>Post</option>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="t-window">Bid window (days)</Label>
              <Input id="t-window" type="number" min={0} max={365} value={input.bid_window_days}
                onChange={(e) => set("bid_window_days", Number(e.target.value))} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="t-lots">Lots</Label>
              <Input id="t-lots" type="number" min={1} max={50} value={input.lots} onChange={(e) => set("lots", Number(e.target.value))} />
            </div>
          </div>
          <div className="col-span-2 space-y-1.5">
            <Label htmlFor="t-date">Planned tender date</Label>
            <Input id="t-date" type="date" value={input.tender_date} onChange={(e) => set("tender_date", e.target.value)} />
          </div>
          <div className="col-span-2 space-y-1.5">
            <div className="flex items-center">
              <Label htmlFor="t-draft">Draft ToR, specification or qualification section</Label>
              <Button type="button" variant="ghost" size="sm" className="ml-auto" onClick={() => file.current?.click()} disabled={reading}>
                {reading ? <Spinner /> : <Upload />} Upload PDF / Word
              </Button>
              <input ref={file} type="file" accept=".pdf,.docx,.txt,.md" className="hidden" onChange={(e) => upload(e.target.files?.[0])} />
            </div>
            <Textarea id="t-draft" rows={7} maxLength={MAX_DRAFT} value={input.draft_text}
              placeholder="Paste the draft here, or upload it. Terms such as the turnover, similar-contract experience, bid validity and completion period are read from it."
              onChange={(e) => set("draft_text", e.target.value)} />
            <p className="text-[11px] text-muted-foreground">{input.draft_text.length.toLocaleString()} / {MAX_DRAFT.toLocaleString()} characters</p>
          </div>
          {error && <p className="col-span-2 text-sm text-danger">{error}</p>}
          <div className="col-span-2 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!validProject || busy || reading}>
              {busy ? <Spinner /> : <FileSearch />} Run check
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
