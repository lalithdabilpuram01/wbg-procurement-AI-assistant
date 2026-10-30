"use client";

import { useState } from "react";
import { Calculator } from "lucide-react";
import type { PackageAssessmentInput } from "@/lib/api";
import { COUNTRIES, REGIONS, SECTORS } from "@/lib/reference";
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
  { open: boolean; onOpenChange: (open: boolean) => void; onSubmit: (input: PackageAssessmentInput) => Promise<void> }) {
  const [input, setInput] = useState<PackageAssessmentInput>({
    project_id: null, region_name: null, country_code: "ET", sector: "Transportation",
    board_approval_date: inDays(-180), income_group: null, prior_tender_notices: 0,
    procurement_method: "Request for Bids", procurement_category: "Works", review_type: "Prior",
    bid_window_days: 45, tender_date: inDays(30),
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof PackageAssessmentInput>(key: K, value: PackageAssessmentInput[K]) => setInput((p) => ({ ...p, [key]: value }));
  const validProject = input.project_id === null || /^P\d{6}$/.test(input.project_id);
  const regional = input.country_code === "REGIONAL";                // no single country: the user gives the region
  const ready = validProject && (!regional || input.region_name !== null);

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
        description="Describe the project and the package; a project with no World Bank ID yet can be assessed. The country's region and income group are looked up from the Gold data.">
        <form onSubmit={submit} className="grid grid-cols-2 gap-x-4 gap-y-3">
          <p className="col-span-2 text-xs font-semibold uppercase tracking-wide text-primary-strong">The project</p>
          <div className="col-span-2 space-y-1.5">
            <Label htmlFor="project">Project ID (optional)</Label>
            <Input id="project" placeholder="Leave empty for a new project, or e.g. P148850" value={input.project_id ?? ""} autoFocus
              onChange={(e) => set("project_id", e.target.value.trim().toUpperCase() || null)} />
            {!validProject && <p className="text-xs text-danger">Use the format P followed by 6 digits, or leave it empty.</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="country">Country</Label>
            <Select id="country" value={input.country_code}
              onChange={(e) => setInput((p) => ({ ...p, country_code: e.target.value, region_name: e.target.value === "REGIONAL" ? REGIONS[0] : null }))}>
              <option value="REGIONAL">Regional (multi-country)</option>
              {COUNTRIES.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="sector">Sector</Label>
            <Select id="sector" value={input.sector} onChange={(e) => set("sector", e.target.value)}>
              {SECTORS.map((s) => <option key={s}>{s}</option>)}
            </Select>
          </div>
          {regional && (
            <div className="col-span-2 space-y-1.5">
              <Label htmlFor="region">Region</Label>
              <Select id="region" value={input.region_name ?? REGIONS[0]} onChange={(e) => set("region_name", e.target.value)}>
                {REGIONS.map((r) => <option key={r}>{r}</option>)}
              </Select>
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="approval">Board approval date</Label>
            <Input id="approval" type="date" value={input.board_approval_date} onChange={(e) => set("board_approval_date", e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="prior">Earlier tenders in the project</Label>
            <Input id="prior" type="number" min={0} max={5000} value={input.prior_tender_notices}
              onChange={(e) => set("prior_tender_notices", Math.max(0, Number(e.target.value)))} />
          </div>
          <p className="col-span-2 mt-2 text-xs font-semibold uppercase tracking-wide text-primary-strong">The package</p>
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
            <Button type="submit" disabled={!ready || busy}>
              {busy ? <Spinner /> : <Calculator />} Run assessment
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
