"use client";

// The Dashboard view: KPI tiles and charts from GET /dashboards/{key}/kpis, which the API reads from the vw_kpi_*
// Gold views on the Databricks SQL warehouse. Charts are plain HTML and CSS, with a table view for each.

import { useEffect, useState } from "react";
import { Database, ExternalLink, RefreshCw, Table2, Workflow } from "lucide-react";
import { api, type Dashboard, type KpiBar, type Kpis } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, Select } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

// Number formats for tiles and charts: compact dollars (US$1.2B), grouped counts and days.
const usd = (v: number) => "US$" + Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(v);
const count = (v: number) => Intl.NumberFormat("en").format(v);
const days = (v: number) => `${count(v)} d`;

/** The procurement KPIs from the Gold views, drawn in the app; the full AI/BI dashboard is one click away in Databricks. */
export function DashboardView({ onError }: { onError: (e: unknown) => void }) {
  const [boards, setBoards] = useState<Dashboard[] | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const [kpis, setKpis] = useState<Kpis | null>(null);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);          // bumped by "Try again" to re-run the KPI effect

  // The list of dashboards is loaded once; the first one is selected.
  useEffect(() => { api.dashboards().then((b) => { setBoards(b); setKey(b[0]?.key ?? null); }).catch(onError); }, [onError]);
  // Loads the KPIs of the selected dashboard, again on every retry. `current` is cleared when the selection changes
  // or the view closes, so a slow, outdated response cannot overwrite newer state.
  useEffect(() => {
    if (!key) return;
    let current = true;
    setLoading(true);
    setKpis(null);
    api.kpis(key).then((k) => current && setKpis(k)).catch((e) => current && onError(e)).finally(() => current && setLoading(false));
    return () => { current = false; };
  }, [key, attempt, onError]);
  const board = boards?.find((b) => b.key === key);

  return (
    <div className="scrollbar-thin h-full flex-1 overflow-y-auto">
      <div className="mx-auto max-w-6xl px-4 py-8">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold tracking-tight">Procurement KPIs</h1>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {boards && boards.length > 1 && (
              <Select className="w-auto" value={key ?? ""} onChange={(e) => setKey(e.target.value)} aria-label="Data source">
                {boards.map((b) => <option key={b.key} value={b.key}>{b.title}</option>)}
              </Select>
            )}
            {board && (
              <Button variant="outline" size="sm" asChild>
                <a href={board.open_url} target="_blank" rel="noreferrer"><ExternalLink /> Full dashboard in Databricks</a>
              </Button>
            )}
          </div>
        </div>
        {board && <p className="mt-1 text-sm text-muted-foreground">Live from {board.source}.</p>}

        {loading && <Loading />}
        {kpis?.status === "not_built" && (
          <Card className="mt-8 p-8 text-sm">
            <Workflow className="mb-2 size-6 text-primary" />
            <p className="font-medium">The pipeline catalog <code>{kpis.catalog}</code> has not been built yet.</p>
            <p className="mt-1 text-muted-foreground">
              Run <code>uv run wbg-etl-seed</code> once, keep <code>uv run wbg-etl-watch</code> running, and this view fills in when the
              first ETL run finishes. Every later upload updates it.
            </p>
          </Card>
        )}
        {kpis?.status === "unavailable" && (
          <Card className="mt-8 p-8 text-sm">
            <p className="font-medium">The SQL warehouse did not answer.</p>
            <p className="mt-1 text-muted-foreground">{kpis.detail}</p>
            <Button size="sm" className="mt-3" onClick={() => setAttempt((a) => a + 1)}><RefreshCw /> Try again</Button>
          </Card>
        )}
        {kpis?.status === "ok" && <Board kpis={kpis} />}
      </div>
    </div>
  );
}

/** Placeholder tiles while the KPIs load. */
function Loading() {
  return (
    <div className="mt-6 space-y-4" aria-busy>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {Array.from({ length: 6 }, (_, i) => <div key={i} className="h-20 animate-pulse rounded-xl bg-muted" />)}
      </div>
      <p className="text-center text-xs text-muted-foreground">Reading the Gold views (the SQL warehouse can take up to 30 s to wake up)…</p>
    </div>
  );
}

/** The KPI tiles, the data freshness line and the four charts. */
function Board({ kpis }: { kpis: Extract<Kpis, { status: "ok" }> }) {
  const o = kpis.overview;
  const tiles: [string, string][] = [
    ["Projects", count(o.total_projects)], ["Contract awards", count(o.contract_awards)],
    ["Contract value", usd(o.total_contract_award_value_usd)], ["Procurement notices", count(o.procurement_notices)],
    ["IBRD loans", count(o.ibrd_loan_instruments)], ["IDA credits", count(o.ida_credit_instruments)],
  ];
  return (
    <>
      <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {tiles.map(([label, value]) => (
          <Card key={label} className="p-4">
            <div className="text-xs text-muted-foreground">{label}</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums tracking-tight">{value}</div>
          </Card>
        ))}
      </div>
      <p className="mt-2 flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <Database className="size-3" /> Contracts as of {o.contracts_source_as_of_date} (last signing {o.last_contract_signing_date}) · Gold run {o.contracts_gold_run_id}
      </p>
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <ColumnChart title="Contract value by fiscal year" bars={kpis.value_by_year} format={usd} />
        <BarChart title="Contract awards by procurement method" bars={kpis.awards_by_method} format={count} />
        <BarChart title="Top 10 countries by contract value" bars={kpis.top_countries} format={usd} />
        <BarChart title="Median published bid window by notice type" bars={kpis.bid_windows} format={days} />
      </div>
    </>
  );
}

/** A card with a title and a Chart/Table toggle; the table lists the same values exactly. */
function ChartCard({ title, bars, format, children }: { title: string; bars: KpiBar[]; format: (v: number) => string; children: React.ReactNode }) {
  const [table, setTable] = useState(false);
  return (
    <Card className="p-5">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setTable(!table)} aria-pressed={table}>
          <Table2 /> {table ? "Chart" : "Table"}
        </Button>
      </div>
      {table ? (
        <table className="mt-3 w-full text-sm">
          <tbody>
            {bars.map((b) => (
              <tr key={String(b.label)} className="border-t border-border">
                <td className="py-1.5 pr-3">{b.label}</td>
                <td className="py-1.5 text-right tabular-nums">{format(b.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : children}
    </Card>
  );
}

/** Horizontal bars, one hue, values labelled at the bar end; hover shows the exact value. */
function BarChart({ title, bars, format }: { title: string; bars: KpiBar[]; format: (v: number) => string }) {
  const max = Math.max(...bars.map((b) => b.value), 1);
  return (
    <ChartCard title={title} bars={bars} format={format}>
      <ul className="mt-4 space-y-2.5">
        {bars.map((b) => (
          <li key={String(b.label)} className="group grid grid-cols-[minmax(0,11rem)_1fr] items-center gap-3 text-xs">
            <span className="truncate text-muted-foreground" title={String(b.label)}>{b.label}</span>
            <div className="relative flex items-center gap-2">
              <div className="h-4 rounded-r-[4px] bg-primary transition-opacity group-hover:opacity-80" style={{ width: `${(b.value / max) * 82}%` }} />
              <span className="tabular-nums text-foreground">{format(b.value)}</span>
              <Tip text={`${b.label}: ${format(b.value)}`} />
            </div>
          </li>
        ))}
      </ul>
    </ChartCard>
  );
}

/** Vertical columns for a time series (fiscal years), anchored to a shared baseline. */
function ColumnChart({ title, bars, format }: { title: string; bars: KpiBar[]; format: (v: number) => string }) {
  const max = Math.max(...bars.map((b) => b.value), 1);
  return (
    <ChartCard title={title} bars={bars} format={format}>
      <div className="mt-4 flex h-56 items-end gap-2 border-b border-border pb-px">
        {bars.map((b, i) => (
          <div key={String(b.label)} className="group relative flex h-full flex-1 flex-col items-center justify-end">
            {(i === bars.length - 1 || b.value === max) && (
              <span className="mb-1 text-[10px] tabular-nums text-muted-foreground">{format(b.value)}</span>
            )}
            <div className="w-full max-w-10 rounded-t-[4px] bg-primary transition-opacity group-hover:opacity-80"
              style={{ height: `${(b.value / max) * 85}%` }} />
            <Tip text={`FY${b.label}: ${format(b.value)}${b.count !== undefined ? ` · ${count(b.count)} awards` : ""}`} above />
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex gap-2">
        {bars.map((b) => <span key={String(b.label)} className="flex-1 text-center text-[10px] text-muted-foreground">{b.label}</span>)}
      </div>
    </ChartCard>
  );
}

/** A CSS-only hover label for a bar or column (shown by its parent's `group-hover`). */
function Tip({ text, above = false }: { text: string; above?: boolean }) {
  return (
    <span role="tooltip" className={cn(
      "pointer-events-none absolute z-10 hidden whitespace-nowrap rounded-md bg-foreground px-2 py-1 text-[11px] text-background shadow group-hover:block",
      above ? "bottom-full left-1/2 mb-1 -translate-x-1/2" : "left-0 top-full mt-1")}>
      {text}
    </span>
  );
}
