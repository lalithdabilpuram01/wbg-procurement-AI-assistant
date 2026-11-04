// Cards for the three prediction models of a package: tender duration (trained on real tenders), and amendment and
// re-bidding risk (trained on synthetic outcomes, so always labelled as such). Fed by "model_prediction" evidence.
import { CalendarClock, FlaskConical, Gauge, RefreshCcw, Timer } from "lucide-react";
import type { Evidence } from "@/lib/api";
import { Badge, Card } from "@/components/ui/primitives";
import { Tooltip } from "@/components/ui/overlays";

/** The value of one named fact on an evidence item, or undefined. */
const fact = (e: Evidence, name: string) => e.facts.find((f) => f.name === name)?.value;

/** Up to three driver phrases parsed from the evidence summary ("Drivers: a; b; c."); none when no driver dominates. */
function drivers(summary: string): string[] {
  const match = summary.match(/(?:Simulated )?[Dd]rivers: (.*?)\.(?:\s|$)/);
  return match && match[1] !== "no dominant driver" ? match[1].split("; ").slice(0, 3) : [];
}

/** The short list of what pushed the estimate up or down. */
function Drivers({ items, synthetic }: { items: string[]; synthetic?: boolean }) {
  if (!items.length) return null;
  return (
    <div className="mt-3 border-t border-border pt-3">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {synthetic ? "Simulated drivers" : "Main drivers"}
      </p>
      <ul className="mt-1.5 space-y-1 text-xs">
        {items.map((d) => (
          <li key={d} className="flex gap-1.5"><span className="text-primary">•</span>{d}</li>
        ))}
      </ul>
    </div>
  );
}

/** Model 1: estimated days from first tender to contract signing, its P10 to P90 range on a bar with a one-year mark,
 *  the chance of taking over a year and the estimated signing month. */
function DurationCard({ e }: { e: Evidence }) {
  const days = Number(fact(e, "estimated_duration"));
  const low = Number(fact(e, "likely_range_p10"));
  const high = Number(fact(e, "likely_range_p90"));
  const overYear = fact(e, "probability_over_365_days");
  const signing = fact(e, "estimated_signing_date");
  // The bar always reaches past one year, so the 365-day mark stays visible for short estimates.
  const scale = Math.max(high * 1.15, 420);
  const pos = (v: number) => `${Math.min(100, (v / scale) * 100)}%`;
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold"><Timer className="size-4 text-primary" /> Tender duration</div>
        <Tooltip content="Ridge regression on real World Bank tenders (first tender to contract signing). Typical error ±77 days.">
          <span><Badge tone="info">Real data</Badge></span>
        </Tooltip>
      </div>
      <p className="mt-3 text-3xl font-semibold tracking-tight">{days}<span className="ml-1 text-base font-normal text-muted-foreground">days</span></p>
      <p className="text-xs text-muted-foreground">Likely {low}–{high} days (P10–P90)</p>
      <div className="relative mt-4 h-2 rounded-full bg-muted" aria-hidden>
        <div className="absolute h-2 rounded-full bg-primary/30" style={{ left: pos(low), width: `calc(${pos(high)} - ${pos(low)})` }} />
        <div className="absolute -top-1 h-4 w-1 rounded-full bg-primary" style={{ left: pos(days) }} />
        <div className="absolute -top-1.5 h-5 border-l border-dashed border-danger" style={{ left: pos(365) }} />
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted-foreground"><span>0</span><span className="text-danger">1 year</span></div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {overYear && <Badge tone={Number(overYear) >= 25 ? "warning" : "neutral"}><Gauge /> {overYear}% chance over 1 year</Badge>}
        {signing && <Badge tone="neutral"><CalendarClock /> Signing ≈ {new Date(signing).toLocaleDateString(undefined, { month: "short", year: "numeric" })}</Badge>}
      </div>
      <Drivers items={drivers(e.summary)} />
    </Card>
  );
}

/** Models 2 and 3: a simulated probability with its assessment and drivers, clearly marked as synthetic. */
function RiskCard({ e, title, icon: Icon }: { e: Evidence; title: string; icon: React.ElementType }) {
  const probability = fact(e, "simulated_probability");
  const assessment = fact(e, "assessment") ?? "";
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold"><Icon className="size-4 text-synthetic" /> {title}</div>
        <Tooltip content="Trained on synthetic (simulated) outcomes. Shows how the model would work; not a validated World Bank risk estimate.">
          <span><Badge tone="synthetic"><FlaskConical /> Synthetic</Badge></span>
        </Tooltip>
      </div>
      <p className="mt-3 text-3xl font-semibold tracking-tight">{probability}<span className="ml-0.5 text-base font-normal text-muted-foreground">%</span></p>
      <p className="text-xs text-muted-foreground first-letter:uppercase">{assessment}</p>
      <Drivers items={drivers(e.summary)} synthetic />
      <p className="mt-3 text-[11px] leading-snug text-synthetic">Trained on synthetic data – not validated.</p>
    </Card>
  );
}

// The package name, and the method suggested from history when the user gave none, both read from the citation text.
const packageOf = (e: Evidence) => e.citation.match(/package '([^']+)'(?: \(method suggested: ([^)]+)\))?/);

/** Three cards per package: one group for a single package, a labelled group per package in a Planning Brief. */
export function PredictionCards({ evidence }: { evidence: Evidence[] }) {
  const models = evidence.filter((e) => e.kind === "model_prediction");
  if (!models.length) return null;
  const groups = new Map<string, Evidence[]>();
  for (const e of models) {
    const key = packageOf(e)?.[1] ?? "";
    groups.set(key, [...(groups.get(key) ?? []), e]);
  }
  return (
    <div className="mt-4 space-y-4">
      {[...groups.entries()].map(([name, items]) => {
        // Each model is found by its citation prefix ("Model 1", "Model 2", "Model 3"); a missing one is just skipped.
        const byModel = (n: number) => items.find((e) => e.citation.startsWith(`Model ${n}`));
        const [duration, amendment, rebid] = [byModel(1), byModel(2), byModel(3)];
        const suggested = packageOf(items[0])?.[2];
        return (
          <div key={name || "package"}>
            {name && (
              <p className="mb-2 text-sm font-semibold">
                {name}
                {suggested && <span className="ml-2 text-xs font-normal text-muted-foreground">method suggested from history: {suggested}</span>}
              </p>
            )}
            <div className="grid gap-3 md:grid-cols-3">
              {duration && <DurationCard e={duration} />}
              {amendment && <RiskCard e={amendment} title="Amendment risk" icon={RefreshCcw} />}
              {rebid && <RiskCard e={rebid} title="Re-bidding risk" icon={RefreshCcw} />}
            </div>
          </div>
        );
      })}
    </div>
  );
}
