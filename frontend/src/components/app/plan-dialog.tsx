"use client";

// The "Plan a new project" dialog: a project profile and up to five planned packages. AppShell sends it to
// POST /conversations/{id}/plans, and the answer is shown as a planning brief (brief.tsx).

import { useState } from "react";
import { ClipboardList, Plus, Trash2 } from "lucide-react";
import type { PlanInput, PlannedPackage } from "@/lib/api";
import { COUNTRIES, SECTORS } from "@/lib/reference";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Spinner, Textarea } from "@/components/ui/primitives";
import { Dialog, DialogContent } from "@/components/ui/overlays";

const METHODS = ["Request for Bids", "Request for Quotations", "Request for Proposals", "Direct Selection",
  "Quality And Cost-Based Selection", "Quality Based Selection", "Consultant Qualification Selection",
  "Least Cost Selection", "Fixed Budget Selection", "Individual Consultant Selection"];
const CATEGORIES = ["Works", "Goods", "Consultant Services", "Non-consulting Services"];
const MAX_PACKAGES = 5;
// The select value for "Suggest from history": sent as a null method, and the API suggests one from past packages.
const SUGGEST = "";

/** The date `days` from today as YYYY-MM-DD, the value format of a date input. */
function inDays(days: number): string {
  return new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);
}

/** A blank planned package with defaults; the method is left for the API to suggest. */
const newPackage = (n: number): PlannedPackage => ({
  name: `Package ${n}`, procurement_category: "Works", procurement_method: null, estimated_amount_usd: null,
  review_type: "Prior", bid_window_days: 45, tender_date: inDays(270),
});

/** The planning form. Like the other dialogs, it closes once `onSubmit` has handed the request over. */
export function PlanDialog({ open, onOpenChange, onSubmit }:
  { open: boolean; onOpenChange: (open: boolean) => void; onSubmit: (input: PlanInput) => Promise<void> }) {
  const [input, setInput] = useState<PlanInput>({
    profile: { country_code: "ET", sector: "Transportation", description: "", approval_date: inDays(180), total_cost_usd: null },
    packages: [newPackage(1)],
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const profile = input.profile;
  const setProfile = (patch: Partial<PlanInput["profile"]>) => setInput((p) => ({ ...p, profile: { ...p.profile, ...patch } }));
  const setPackage = (i: number, patch: Partial<PlannedPackage>) =>
    setInput((p) => ({ ...p, packages: p.packages.map((pkg, j) => (j === i ? { ...pkg, ...patch } : pkg)) }));
  const country = COUNTRIES.find((c) => c.code === profile.country_code);
  const described = profile.description.trim().length;
  // The description is used to find similar past projects, so a very short one is refused before sending.
  const missing = described < 20 ? `Describe what the project will do (at least 20 characters; ${described} so far).`
    : !input.packages.every((p) => p.name.trim()) ? "Give every package a name." : null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (missing) {                                                    // say what is missing instead of a silent grey button
      setError(missing);
      if (described < 20) document.getElementById("description")?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSubmit({ ...input, profile: { ...profile, description: profile.description.trim() } });
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The brief could not be started.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Plan a new project" className="w-[min(96vw,880px)] max-h-[92vh] overflow-y-auto"
        description="Describe the project you are preparing and the packages you plan to procure. The brief draws on similar past projects: their data, reports and outcomes.">
        <form onSubmit={submit} className="space-y-6">
          <fieldset className="grid gap-4 sm:grid-cols-2">
            <legend className="mb-3 text-xs font-semibold uppercase tracking-wide text-primary">Project profile</legend>
            <div className="space-y-1.5">
              <Label htmlFor="country">Country</Label>
              <Select id="country" value={profile.country_code} onChange={(e) => setProfile({ country_code: e.target.value })}>
                {COUNTRIES.map((c) => <option key={c.code} value={c.code}>{c.name}{c.documents ? " ★" : ""}</option>)}
              </Select>
              <p className="text-[11px] text-muted-foreground">
                {country?.documents ? "★ Reports of past projects available: lessons and risks will be cited."
                  : "Data benchmarks and models only: no project reports for this country in the corpus."}
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sector">Sector</Label>
              <Select id="sector" value={profile.sector} onChange={(e) => setProfile({ sector: e.target.value })}>
                {SECTORS.map((s) => <option key={s}>{s}</option>)}
              </Select>
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="description">What will the project do?</Label>
              <Textarea id="description" rows={3} maxLength={1500} value={profile.description}
                placeholder="e.g. Rehabilitate 120 km of rural roads and build three bridges in Oromia, with road maintenance capacity building."
                onChange={(e) => { setProfile({ description: e.target.value }); setError(null); }} />
              <p className={cn("text-[11px]", described > 0 && described < 20 ? "text-danger" : "text-muted-foreground")}>
                Used to find projects with similar content: at least 20 characters ({described}/20).
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="approval">Planned Board approval</Label>
              <Input id="approval" type="date" value={profile.approval_date} onChange={(e) => setProfile({ approval_date: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cost">Estimated total cost (USD, optional)</Label>
              <Input id="cost" type="number" min={0} step={1_000_000} value={profile.total_cost_usd ?? ""} placeholder="e.g. 150000000"
                onChange={(e) => setProfile({ total_cost_usd: e.target.value ? Number(e.target.value) : null })} />
            </div>
          </fieldset>

          <fieldset className="space-y-3">
            <legend className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">Planned packages</legend>
            {input.packages.map((pkg, i) => (
              // Keyed by position because packages have no id; safe here since every field is controlled by `input`.
              <div key={i} className="grid grid-cols-2 gap-3 rounded-xl border border-border bg-muted/40 p-3 md:grid-cols-6">
                <div className="col-span-2 space-y-1">
                  <Label htmlFor={`name-${i}`}>Package name</Label>
                  <Input id={`name-${i}`} value={pkg.name} maxLength={80} onChange={(e) => setPackage(i, { name: e.target.value })} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`cat-${i}`}>Category</Label>
                  <Select id={`cat-${i}`} value={pkg.procurement_category} onChange={(e) => setPackage(i, { procurement_category: e.target.value })}>
                    {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
                  </Select>
                </div>
                <div className="col-span-2 space-y-1 md:col-span-1">
                  <Label htmlFor={`method-${i}`}>Method</Label>
                  <Select id={`method-${i}`} value={pkg.procurement_method ?? SUGGEST}
                    onChange={(e) => setPackage(i, { procurement_method: e.target.value || null })}>
                    <option value={SUGGEST}>Suggest from history</option>
                    {METHODS.map((m) => <option key={m}>{m}</option>)}
                  </Select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`review-${i}`}>Review</Label>
                  <Select id={`review-${i}`} value={pkg.review_type} onChange={(e) => setPackage(i, { review_type: e.target.value })}>
                    <option>Prior</option><option>Post</option>
                  </Select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`window-${i}`}>Bid window (days)</Label>
                  <Input id={`window-${i}`} type="number" min={0} max={365} value={pkg.bid_window_days}
                    onChange={(e) => setPackage(i, { bid_window_days: Number(e.target.value) })} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`tender-${i}`}>Tender date</Label>
                  <Input id={`tender-${i}`} type="date" value={pkg.tender_date} onChange={(e) => setPackage(i, { tender_date: e.target.value })} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`amount-${i}`}>Amount (USD)</Label>
                  <Input id={`amount-${i}`} type="number" min={0} value={pkg.estimated_amount_usd ?? ""} placeholder="optional"
                    onChange={(e) => setPackage(i, { estimated_amount_usd: e.target.value ? Number(e.target.value) : null })} />
                </div>
                <div className="flex items-end justify-end md:col-span-3">
                  {input.packages.length > 1 && (
                    <Button type="button" variant="ghost" size="sm" onClick={() => setInput((p) => ({ ...p, packages: p.packages.filter((_, j) => j !== i) }))}>
                      <Trash2 /> Remove
                    </Button>
                  )}
                </div>
              </div>
            ))}
            {input.packages.length < MAX_PACKAGES && (
              <Button type="button" variant="outline" size="sm"
                onClick={() => setInput((p) => ({ ...p, packages: [...p.packages, newPackage(p.packages.length + 1)] }))}>
                <Plus /> Add package
              </Button>
            )}
          </fieldset>

          <p className="rounded-lg bg-synthetic-soft px-3 py-2 text-xs text-synthetic">
            Amendment and re-bidding risks come from models trained on synthetic data and are shown for demonstration only.
          </p>
          {error && <p className="text-sm text-danger">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy}>{busy ? <Spinner /> : <ClipboardList />} Build planning brief</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
