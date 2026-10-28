import { CheckCircle2, CircleAlert, Info, XCircle } from "lucide-react";
import type { Evidence } from "@/lib/api";
import { Card } from "@/components/ui/primitives";
import { Tooltip } from "@/components/ui/overlays";
import { cn } from "@/lib/utils";

const STATUS = {
  ok: { icon: CheckCircle2, tone: "text-success", label: "In line" },
  warn: { icon: CircleAlert, tone: "text-warning", label: "Check" },
  fail: { icon: XCircle, tone: "text-danger", label: "Out of line" },
  info: { icon: Info, tone: "text-muted-foreground", label: "Note" },
} as const;

const fact = (e: Evidence, name: string) => e.facts.find((f) => f.name === name)?.value;

/** A tender check: the terms read from the draft (hover for the sentence) and each check with its status and source. */
export function TenderChecks({ evidence }: { evidence: Evidence[] }) {
  const records = evidence.filter((e) => e.kind === "tender_check");
  if (!records.length) return null;
  const terms = records.find((e) => !fact(e, "check"));
  const checks = records.filter((e) => fact(e, "check"));
  return (
    <Card className="mt-4 overflow-hidden">
      {terms && (
        <div className="border-b border-border bg-muted/50 px-4 py-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Found in your draft</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {terms.facts.filter((f) => !f.name.endsWith("_sentence")).map((f) => (
              <Tooltip key={f.name} content={fact(terms, `${f.name}_sentence`) ?? ""}>
                <span className="cursor-help rounded-md bg-card px-2 py-0.5 text-xs shadow-sm">
                  {f.name.replace(/_(usd|days|percent|months)$/, "").replaceAll("_", " ")}: <b>{f.value}</b>
                </span>
              </Tooltip>
            ))}
          </div>
        </div>
      )}
      <ul className="divide-y divide-border">
        {checks.map((e) => {
          const status = STATUS[(fact(e, "status") ?? "info") as keyof typeof STATUS] ?? STATUS.info;
          const [title, ...rest] = e.summary.split(": ");
          return (
            <li key={e.evidence_id} className="flex gap-3 px-4 py-3">
              <Tooltip content={status.label}><span><status.icon className={cn("mt-0.5 size-4 shrink-0", status.tone)} /></span></Tooltip>
              <div className="min-w-0 text-sm">
                <span className="font-semibold">{title}</span> <span>{rest.join(": ")}</span>
                <p className="mt-0.5 text-[11px] text-muted-foreground">{e.citation}</p>
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
