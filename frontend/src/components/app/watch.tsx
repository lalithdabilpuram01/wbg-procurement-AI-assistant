"use client";

// The Project watch view (execution and monitoring). It lists projects from GET /projects and shows one project's
// summary, early warnings and packages from GET /projects/{id}/watch.

import { Fragment, useEffect, useMemo, useState } from "react";
import { ChevronRight, Eye, FileText } from "lucide-react";
import { api, type PackageView, type ProjectWatch, type WatchedProject, type WatchWarning } from "@/lib/api";
import { Badge, Card, Select, Spinner } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

// Badge tone and label per warning severity; "low" covers stale or out-of-date plan entries, hidden by default.
const SEVERITY = { high: "danger", medium: "warning", low: "neutral" } as const;
const SEVERITY_LABEL = { high: "High", medium: "Medium", low: "Stale" } as const;
const FILTERS = { all: "All", open: "Open", late: "Signed late", cancelled: "Cancelled" } as const;
type Filter = keyof typeof FILTERS;
// Plan statuses that count as "open" or "cancelled" for the package filter (as written in the procurement plans).
const OPEN = new Set(["under/pending implementation", "under review", "blank", "unclassified"]);
const CANCELLED = new Set(["cancelled", "terminated"]);

/** Execution & Monitoring: a project's milestones and early warnings, each citing the plan row, contract or ISR behind it. */
export function WatchView({ projectId, onProject, onError }:
  { projectId: string | null; onProject: (id: string) => void; onError: (e: unknown) => void }) {
  const [projects, setProjects] = useState<WatchedProject[] | null>(null);
  const [view, setView] = useState<ProjectWatch | null>(null);
  const [loading, setLoading] = useState(false);

  // The project list is loaded once per mount; a project picked elsewhere (an answer's "Watch" button) arrives as
  // `projectId`, and the next effect loads it.
  useEffect(() => { api.projects().then(setProjects).catch(onError); }, [onError]);

  useEffect(() => {
    if (!projectId) return;
    setLoading(true);
    setView(null);
    api.watch(projectId).then(setView).catch(onError).finally(() => setLoading(false));
  }, [projectId, onError]);

  return (
    <div className="scrollbar-thin h-full flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-8">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold tracking-tight">Project watch</h1>
          <Select className="ml-auto w-auto max-w-sm" value={projectId ?? ""} onChange={(e) => onProject(e.target.value)} aria-label="Project">
            <option value="" disabled>Choose a project…</option>
            {projects?.map((p) => (
              <option key={p.project_id} value={p.project_id}>{p.project_id} · {p.title ?? "untitled"} ({p.packages} packages)</option>
            ))}
          </Select>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Milestones and early warnings from the latest procurement plan, the World Bank contract records and the ISRs.
          Thresholds come from how late similar packages were actually signed.
        </p>
        {loading && <div className="mt-10 flex justify-center"><Spinner /></div>}
        {!projectId && !loading && (
          <Card className="mt-8 p-10 text-center text-sm text-muted-foreground">
            <Eye className="mx-auto mb-2 size-6" /> Pick a project to see its warnings.
          </Card>
        )}
        {view && <Watch view={view} />}
      </div>
    </div>
  );
}

/** One project: the header card with summary tiles and latest ISR ratings, then warnings and packages. */
function Watch({ view }: { view: ProjectWatch }) {
  const s = view.summary;
  const latest = latestRatings(view);
  return (
    <>
      <Card className="mt-6 p-5">
        <p className="text-base font-semibold">{view.title ?? view.project_id}</p>
        <p className="text-xs text-muted-foreground">
          {view.project_id}{view.country ? ` · ${view.country}` : ""}{view.as_of ? ` · statuses as of the plan of ${view.as_of}` : ""}
        </p>
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Tile label="Packages" value={s.packages} note={`${s.signed} signed · ${s.open} open · ${s.cancelled} cancelled`} />
          <Tile label="High warnings" value={s.warnings.high} tone="text-danger" />
          <Tile label="Medium warnings" value={s.warnings.medium} tone="text-warning" />
          <Tile label="Median slippage" value={s.median_slippage_days === null ? "—" : `${Math.round(s.median_slippage_days)} d`}
            note={`${s.stale_plan_entries} stale plan entries`} />
        </div>
        {latest.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2 border-t border-border pt-3 text-xs">
            <span className="font-semibold text-muted-foreground">Latest ISR{latest[0].date ? ` (${latest[0].date})` : ""}:</span>
            {latest.map((r) => (
              <span key={r.indicator}>
                {r.indicator}: {r.previous && r.previous !== r.current ? `${r.previous} → ` : ""}<b>{r.current}</b>
              </span>
            ))}
          </div>
        )}
      </Card>
      <Warnings warnings={view.warnings} />
      <Packages packages={view.packages} />
    </>
  );
}

/** A summary number with a label and an optional note below it. */
function Tile({ label, value, note, tone }: { label: string; value: number | string; note?: string; tone?: string }) {
  return (
    <div className="rounded-lg bg-muted p-3">
      <p className="text-[11px] font-semibold text-muted-foreground">{label}</p>
      <p className={cn("text-2xl font-semibold tabular-nums", tone)}>{value}</p>
      {note && <p className="text-[11px] text-muted-foreground">{note}</p>}
    </div>
  );
}

/** The early warnings; a click on one shows the citations behind it and, when given, its benchmark group. */
function Warnings({ warnings }: { warnings: WatchWarning[] }) {
  const [showStale, setShowStale] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const shown = warnings.filter((w) => showStale || w.severity !== "low");
  const stale = warnings.length - warnings.filter((w) => w.severity !== "low").length;
  return (
    <section className="mt-6">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">Early warnings</h2>
        {stale > 0 && (
          <button onClick={() => setShowStale(!showStale)} className="ml-auto text-xs text-primary hover:underline">
            {showStale ? "Hide" : "Show"} {stale} low / stale
          </button>
        )}
      </div>
      {shown.length === 0 ? (
        <Card className="mt-2 p-6 text-center text-sm text-muted-foreground">No warnings.</Card>
      ) : (
        <Card className="mt-2 divide-y divide-border">
          {shown.map((w, i) => (
            <div key={i} className="px-4 py-3">
              <button onClick={() => setOpen(open === i ? null : i)} className="flex w-full items-start gap-3 text-left">
                <Badge tone={SEVERITY[w.severity]} className="mt-0.5 w-16 justify-center">{SEVERITY_LABEL[w.severity]}</Badge>
                <span className="min-w-0 flex-1 text-sm">
                  {w.reference && <span className="mr-2 font-mono text-xs font-semibold">{w.reference}</span>}
                  {w.message}
                </span>
                <ChevronRight className={cn("mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform", open === i && "rotate-90")} />
              </button>
              {open === i && (
                <ul className="ml-[76px] mt-2 space-y-1 text-xs text-muted-foreground">
                  {w.citations.map((c) => <li key={c} className="flex gap-1.5"><FileText className="mt-0.5 size-3 shrink-0" /> {c}</li>)}
                  {w.benchmark && "n" in w.benchmark && (
                    <li>Benchmark: {String(w.benchmark.group)}, {String(w.benchmark.n)} signed packages</li>
                  )}
                </ul>
              )}
            </div>
          ))}
        </Card>
      )}
    </section>
  );
}

/** The package table with status filters; a click on a row opens its stage timeline below it. */
function Packages({ packages }: { packages: PackageView[] }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<string | null>(null);
  const shown = useMemo(() => packages.filter((p) =>
    filter === "all" || (filter === "open" && OPEN.has(p.status) && !p.signed)
    || (filter === "late" && (p.slippage_days ?? 0) > 0) || (filter === "cancelled" && CANCELLED.has(p.status))), [packages, filter]);
  return (
    <section className="mt-6">
      <div className="flex flex-wrap items-center gap-1">
        <h2 className="mr-auto text-sm font-semibold">Packages</h2>
        {(Object.keys(FILTERS) as Filter[]).map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={cn("rounded-full px-2.5 py-1 text-xs", filter === f ? "bg-primary-soft font-semibold text-primary-strong" : "text-muted-foreground hover:bg-muted")}>
            {FILTERS[f]}
          </button>
        ))}
      </div>
      <Card className="mt-2 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <tr className="border-b border-border">
              <th className="px-4 py-2 font-semibold">Reference</th><th className="px-2 py-2 font-semibold">Method</th>
              <th className="px-2 py-2 font-semibold">Status</th><th className="px-2 py-2 font-semibold">Planned</th>
              <th className="px-2 py-2 font-semibold">Signed</th><th className="px-4 py-2 text-right font-semibold">Slip</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((p) => (
              <Fragment key={p.activity_id}>
                <tr onClick={() => setOpen(open === p.activity_id ? null : p.activity_id)}
                  className="cursor-pointer border-b border-border last:border-0 hover:bg-muted/60">
                  <td className="px-4 py-2">
                    <p className="font-mono text-xs font-semibold">{p.reference}</p>
                    <p className="line-clamp-1 text-xs text-muted-foreground">{p.description}</p>
                  </td>
                  <td className="px-2 py-2 text-xs">{p.method ?? "—"}</td>
                  <td className="px-2 py-2 text-xs">{p.status}</td>
                  <td className="px-2 py-2 text-xs tabular-nums">{p.planned_signing ?? "—"}</td>
                  <td className="px-2 py-2 text-xs tabular-nums">{p.signed ?? "—"}</td>
                  <td className={cn("px-4 py-2 text-right text-xs tabular-nums", (p.slippage_days ?? 0) > 0 && "font-semibold text-warning")}>
                    {p.slippage_days === null ? "—" : `${p.slippage_days > 0 ? "+" : ""}${p.slippage_days}`}
                  </td>
                </tr>
                {open === p.activity_id && (
                  <tr className="border-b border-border bg-muted/40"><td colSpan={6} className="px-4 py-3"><Timeline pkg={p} /></td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        {shown.length === 0 && <p className="p-6 text-center text-sm text-muted-foreground">No packages.</p>}
      </Card>
    </section>
  );
}

/** A package's planned and actual stage dates, and the plan row, contract and amounts it comes from. */
function Timeline({ pkg }: { pkg: PackageView }) {
  return (
    <div className="text-xs">
      {pkg.stages.length === 0 ? <p className="text-muted-foreground">The plan gives no stage dates for this package.</p> : (
        <ol className="space-y-1">
          {pkg.stages.map((s) => (
            <li key={s.name} className="flex items-center gap-2">
              <span className={cn("size-2 shrink-0 rounded-full", s.actual ? "bg-success" : "border border-muted-foreground")} />
              <span className="flex-1">{s.name}</span>
              <span className="tabular-nums text-muted-foreground">planned {s.planned ?? "—"}</span>
              <span className="w-28 text-right tabular-nums">{s.actual ? `done ${s.actual}` : "not done"}</span>
            </li>
          ))}
        </ol>
      )}
      <p className="mt-2 flex items-center gap-1.5 text-muted-foreground">
        <FileText className="size-3" /> Procurement plan {pkg.plan}{pkg.page ? `, p. ${pkg.page}` : ""}
        {pkg.contract_number && ` · Contract ${pkg.contract_number}`}
        {pkg.estimated_usd !== null && ` · estimate US$ ${Math.round(pkg.estimated_usd).toLocaleString()}`}
        {pkg.contract_amount_usd !== null && ` · contract US$ ${Math.round(pkg.contract_amount_usd).toLocaleString()}`}
      </p>
    </div>
  );
}

/** The newest rating per ISR indicator. The API lists ratings oldest first, so later entries replace earlier ones. */
function latestRatings(view: ProjectWatch) {
  const latest = new Map<string, ProjectWatch["ratings"][number]>();
  for (const r of view.ratings) latest.set(r.indicator, r);               // oldest first
  return [...latest.values()];
}
