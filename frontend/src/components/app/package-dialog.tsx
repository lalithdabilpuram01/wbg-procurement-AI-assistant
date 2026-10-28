"use client";

import { useState } from "react";
import { Calculator } from "lucide-react";
import type { PackageInput } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Spinner } from "@/components/ui/primitives";
import { Dialog, DialogContent } from "@/components/ui/overlays";

export const METHODS = ["Request for Bids", "Request for Quotations", "Request for Proposals", "Direct Selection",
  "Quality And Cost-Based Selection", "Quality Based Selection", "Consultant Qualification Selection",
  "Least Cost Selection", "Fixed Budget Selection", "Individual Consultant Selection"];
export const CATEGORIES = ["Works", "Goods", "Consultant Services", "Non-consulting Services"];

export function inDays(days: number): string {
  const date = new Date(Date.now() + days * 86_400_000);
  return date.toISOString().slice(0, 10);
}

export function PackageDialog({ open, onOpenChange, onSubmit }:
  { open: boolean; onOpenChange: (open: boolean) => void; onSubmit: (input: PackageInput) => Promise<void> }) {
  const [input, setInput] = useState<PackageInput>({
    project_id: "", procurement_method: "Request for Bids", procurement_category: "Works", review_type: "Prior",
    bid_window_days: 45, tender_date: inDays(30),
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof PackageInput>(key: K, value: PackageInput[K]) => setInput((p) => ({ ...p, [key]: value }));
  const validProject = /^P\d{6}$/.test(input.project_id);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit(input);
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The assessment could not be started.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Assess a procurement package"
        description="Enter the project and the package basics. Region, country, sector, income group and earlier tenders are looked up from the project records.">
        <form onSubmit={submit} className="grid grid-cols-2 gap-4">
          <div className="col-span-2 space-y-1.5">
            <Label htmlFor="project">Project ID</Label>
            <Input id="project" placeholder="e.g. P148850" value={input.project_id} autoFocus
              onChange={(e) => set("project_id", e.target.value.trim().toUpperCase())} />
            {input.project_id && !validProject && <p className="text-xs text-danger">Use the format P followed by 6 digits.</p>}
          </div>
          <div className="col-span-2 space-y-1.5 sm:col-span-1">
            <Label htmlFor="method">Procurement method</Label>
            <Select id="method" value={input.procurement_method} onChange={(e) => set("procurement_method", e.target.value)}>
              {METHODS.map((m) => <option key={m}>{m}</option>)}
            </Select>
          </div>
          <div className="col-span-2 space-y-1.5 sm:col-span-1">
            <Label htmlFor="category">Category</Label>
            <Select id="category" value={input.procurement_category} onChange={(e) => set("procurement_category", e.target.value)}>
              {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="review">Review type</Label>
            <Select id="review" value={input.review_type} onChange={(e) => set("review_type", e.target.value)}>
              <option>Prior</option><option>Post</option>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="window">Bid window (days)</Label>
            <Input id="window" type="number" min={0} max={365} value={input.bid_window_days}
              onChange={(e) => set("bid_window_days", Number(e.target.value))} />
          </div>
          <div className="col-span-2 space-y-1.5">
            <Label htmlFor="date">Planned tender date</Label>
            <Input id="date" type="date" value={input.tender_date} onChange={(e) => set("tender_date", e.target.value)} />
          </div>
          <p className="col-span-2 rounded-lg bg-synthetic-soft px-3 py-2 text-xs text-synthetic">
            Amendment and re-bidding risks come from models trained on synthetic data and are shown for demonstration only.
          </p>
          {error && <p className="col-span-2 text-sm text-danger">{error}</p>}
          <div className="col-span-2 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!validProject || busy}>
              {busy ? <Spinner /> : <Calculator />} Run assessment
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
