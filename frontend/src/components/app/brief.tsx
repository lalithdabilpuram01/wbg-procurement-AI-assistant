"use client";

// The planning brief: the layout of a "Plan a new project" answer. Claims are grouped by their `section`, and some
// sections also show cards built from the evidence (similar projects, model predictions, market benchmarks).

import { Activity, CalendarClock, FileCog, FileText, Landmark, Lightbulb, Search, ShieldAlert, Store } from "lucide-react";
import type { Evidence, Investigation } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, Card } from "@/components/ui/primitives";
import { PredictionCards } from "./prediction-cards";
import { cn } from "@/lib/utils";

// The sections in display order; `key` matches the claim's section name set by the API.
const SECTIONS: { key: string; title: string; icon: React.ElementType; lead?: string }[] = [
  { key: "similar_projects", title: "Similar past projects", icon: Landmark },
  { key: "lessons", title: "Lessons learned", icon: Lightbulb, lead: "What similar projects learned, and what to do differently." },
  { key: "risks", title: "Risks and mitigations", icon: ShieldAlert },
  { key: "schedule", title: "Realistic schedule", icon: CalendarClock },
  { key: "market_approach", title: "Market approach", icon: Store },
  { key: "tender_design", title: "Tender design and packaging", icon: FileCog },
  { key: "monitoring", title: "What to monitor during execution", icon: Activity },
];

/** The value of one named fact on an evidence item, or undefined. */
const fact = (e: Evidence, name: string) => e.facts.find((f) => f.name === name)?.value;

type Props = {
  investigation: Investigation;
  citation: (id: string) => React.ReactNode;
  onInvestigate: (question: string) => void;
};

/** Renders a planning brief. `citation` draws the numbered source buttons; `onInvestigate` sends a follow-up. */
export function BriefView({ investigation, citation, onInvestigate }: Props) {
  const evidence = investigation.evidence;
  const similar = evidence.filter((e) => e.kind === "similar_project");
  const benchmarks = evidence.filter((e) => e.kind === "structured_result");
  const claimsIn = (key: string) => investigation.claims.filter((c) => c.section === key);

  return (
    <div className="mt-2 space-y-7">
      {SECTIONS.map(({ key, title, icon: Icon, lead }) => {
        const claims = claimsIn(key);
        // Cards that come from evidence rather than claims, so a section with no claims can still show.
        // "schedule" always counts as having content because its prediction cards decide for themselves.
        const extra = key === "similar_projects" ? similar.length : key === "schedule" ? 1 : key === "market_approach" ? benchmarks.length : 0;
        if (!claims.length && !extra) return null;
        const main = key === "lessons";
        return (
          <section key={key} className={cn(main && "rounded-2xl border border-primary/30 bg-primary-soft/40 p-4")}>
            <h3 className="flex items-center gap-2 text-base font-semibold">
              <Icon className="size-5 text-primary" /> {title}
              {main && <Badge tone="info">Main focus</Badge>}
            </h3>
            {lead && <p className="mt-0.5 text-xs text-muted-foreground">{lead}</p>}
            {claims.length > 0 && (
              <ul className="mt-3 space-y-2.5 text-[15px] leading-relaxed">
                {claims.map((c, i) => (
                  <li key={i} className="flex gap-2.5">
                    <span className="mt-2.5 size-1.5 shrink-0 rounded-full bg-primary" />
                    <span>{c.text}{c.evidence_ids.map((id) => citation(id))}</span>
                  </li>
                ))}
              </ul>
            )}
            {key === "similar_projects" && <SimilarProjects items={similar} onInvestigate={onInvestigate} />}
            {key === "schedule" && <PredictionCards evidence={evidence} />}
            {key === "market_approach" && <Benchmarks items={benchmarks} />}
          </section>
        );
      })}
    </div>
  );
}

/** One card per similar past project, with a button that asks for an investigation of its procurement history. */
function SimilarProjects({ items, onInvestigate }: { items: Evidence[]; onInvestigate: (question: string) => void }) {
  if (!items.length) return null;
  return (
    <div className="mt-3 grid gap-3 sm:grid-cols-2">
      {items.map((e) => {
        const id = fact(e, "project_id") ?? "";
        const name = fact(e, "project_name") ?? id;
        return (
          <Card key={e.evidence_id} className="flex flex-col p-3.5">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <p className="line-clamp-2 text-sm font-semibold">{name}</p>
                <p className="text-xs text-muted-foreground">{id} · {fact(e, "country")} · {fact(e, "sector")} · FY{fact(e, "approval_fiscal_year")}</p>
              </div>
              <Badge tone="info">{fact(e, "similarity")}% match</Badge>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">{fact(e, "match")}</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <Badge tone="neutral">{fact(e, "status")}</Badge>
              {fact(e, "ieg_outcome_rating") !== "not rated" && <Badge tone="neutral">IEG: {fact(e, "ieg_outcome_rating")}</Badge>}
              {fact(e, "documents") === "yes" && <Badge tone="success"><FileText /> Reports</Badge>}
            </div>
            <Button variant="outline" size="sm" className="mt-3 self-start"
              onClick={() => onInvestigate(`Investigate the procurement history of ${name} (${id}): which methods were used, how long the tenders took, what problems occurred and what lessons were recorded.`)}>
              <Search /> Investigate
            </Button>
          </Card>
        );
      })}
    </div>
  );
}

/** Small tables of market benchmarks from the Gold data (first eight facts each); the Gold table name is cut. */
function Benchmarks({ items }: { items: Evidence[] }) {
  if (!items.length) return null;
  return (
    <div className="mt-3 grid gap-3 md:grid-cols-2">
      {items.map((e) => (
        <Card key={e.evidence_id} className="overflow-hidden">
          <p className="border-b border-border bg-muted/60 px-3 py-2 text-xs font-semibold">{e.citation.replace(/ \(.*?gold_.*?\)$/, "")}</p>
          <table className="w-full text-xs">
            <tbody>
              {e.facts.slice(0, 8).map((f) => (
                <tr key={f.name} className="border-b border-border last:border-0">
                  <td className="px-3 py-1.5 font-medium">{f.name.replaceAll("_", " ")}</td>
                  <td className="px-3 py-1.5 text-muted-foreground">{f.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}
    </div>
  );
}
