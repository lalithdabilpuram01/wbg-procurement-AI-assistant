import { AlertTriangle, CheckCircle2, CircleHelp, CircleStop, ClipboardList, Clock, ShieldAlert, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/primitives";

type Tone = "success" | "warning" | "danger" | "info" | "neutral";
const STATUS: Record<string, { label: string; tone: Tone; icon: React.ElementType }> = {
  answered: { label: "Answered", tone: "success", icon: CheckCircle2 },
  partial: { label: "Partial answer", tone: "warning", icon: AlertTriangle },
  conflicting_evidence: { label: "Conflicting evidence", tone: "warning", icon: AlertTriangle },
  insufficient_evidence: { label: "Not enough evidence", tone: "neutral", icon: CircleHelp },
  awaiting_approval: { label: "Needs your approval", tone: "info", icon: Clock },
  rejected: { label: "Not run", tone: "neutral", icon: XCircle },
  clarification_required: { label: "Clarification needed", tone: "info", icon: CircleHelp },
  blocked: { label: "Blocked by policy", tone: "danger", icon: ShieldAlert },
  blocked_by_guardrail: { label: "Blocked by policy", tone: "danger", icon: ShieldAlert },
  failed: { label: "Failed", tone: "danger", icon: XCircle },
  stopped: { label: "Stopped", tone: "neutral", icon: CircleStop },
  needs_package_details: { label: "Package details needed", tone: "info", icon: ClipboardList },
  needs_project_details: { label: "Project details needed", tone: "info", icon: ClipboardList },
};

export function StatusBadge({ status }: { status: string | null }) {
  if (!status) return null;
  const { label, tone, icon: Icon } = STATUS[status] ?? { label: status.replaceAll("_", " "), tone: "neutral", icon: CircleHelp };
  return (
    <Badge tone={tone}>
      <Icon /> {label}
    </Badge>
  );
}
