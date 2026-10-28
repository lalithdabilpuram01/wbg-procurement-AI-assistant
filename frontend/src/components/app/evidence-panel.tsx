import { BrainCircuit, Database, ExternalLink, FileText, ShieldCheck, TableProperties } from "lucide-react";
import type { Evidence } from "@/lib/api";
import { Badge } from "@/components/ui/primitives";
import { Sheet } from "@/components/ui/overlays";
import { cn } from "@/lib/utils";

const KIND: Record<string, { label: string; icon: React.ElementType }> = {
  structured_result: { label: "Databricks result", icon: Database },
  validated_query: { label: "Validated query", icon: Database },
  metric_definition: { label: "Metric definition", icon: Database },
  table_candidate: { label: "Data table", icon: Database },
  document_passage: { label: "Document passage", icon: FileText },
  document_table: { label: "Document table", icon: TableProperties },
  typed_plan_record: { label: "Procurement plan row", icon: TableProperties },
  model_prediction: { label: "Model estimate", icon: BrainCircuit },
  early_warning: { label: "Early warning", icon: TableProperties },
  tender_check: { label: "Tender check", icon: TableProperties },
  lesson_card: { label: "Reviewed lesson", icon: FileText },
};

const WARNING_LABELS: Record<string, string> = {
  synthetic_training_data: "Synthetic training data",
  pdf_table_value_unverified: "Table value not verified",
  conflicting_evidence: "Conflicting",
  page_not_checked: "Page not checked",
  pdf_extracted_typed_record: "Extracted from PDF",
  model_input_note: "See model notes",
};

export function EvidencePanel({ open, onOpenChange, evidence, focusId }:
  { open: boolean; onOpenChange: (open: boolean) => void; evidence: Evidence[]; focusId: string | null }) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title={`Sources (${evidence.length})`}>
      <ol className="space-y-3">
        {evidence.map((e, i) => {
          const kind = KIND[e.kind] ?? { label: e.kind, icon: FileText };
          return (
            <li key={e.evidence_id} id={`ev-${e.evidence_id}`}
              className={cn("rounded-xl border border-border p-3.5 transition-shadow",
                focusId === e.evidence_id && "border-primary ring-2 ring-primary/25")}>
              <div className="flex items-center gap-2">
                <span className="grid size-5 place-items-center rounded-md bg-primary-soft text-[11px] font-bold text-primary-strong">{i + 1}</span>
                <kind.icon className="size-4 text-muted-foreground" />
                <span className="text-xs font-semibold">{kind.label}</span>
                {e.validation_status === "verified" && <Badge tone="success" className="ml-auto"><ShieldCheck /> Verified</Badge>}
              </div>
              <p className="mt-2 text-xs font-medium text-muted-foreground">{e.citation}</p>
              {e.summary && <p className="mt-2 whitespace-pre-line text-[13px] leading-relaxed">{e.summary}</p>}
              {e.facts.length > 0 && e.kind !== "model_prediction" && (
                <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 rounded-lg bg-muted p-2 text-xs">
                  {e.facts.slice(0, 10).map((f) => (
                    <div key={f.name} className="contents">
                      <dt className="truncate text-muted-foreground">{f.name.replaceAll("_", " ")}</dt>
                      <dd className="truncate font-medium">{f.value}{f.unit ? ` ${f.unit}` : ""}</dd>
                    </div>
                  ))}
                </dl>
              )}
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {e.warnings.filter((w) => WARNING_LABELS[w]).map((w) => (
                  <Badge key={w} tone={w === "synthetic_training_data" ? "synthetic" : "warning"}>{WARNING_LABELS[w]}</Badge>
                ))}
                {e.source_url && (
                  <a href={e.source_url} target="_blank" rel="noreferrer"
                    className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">
                    Open document <ExternalLink className="size-3" />
                  </a>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </Sheet>
  );
}
