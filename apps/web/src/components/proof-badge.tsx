"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const labels: Record<string, { label: string; className: string }> = {
  ADMITTED: { label: "Admitido", className: "border-emerald-600/40 text-emerald-700 dark:text-emerald-300" },
  JUDICIALLY_PROVEN: { label: "Probado", className: "border-emerald-600/40 text-emerald-700 dark:text-emerald-300" },
  EVIDENCE_SUPPORTED: { label: "Con prueba", className: "border-amber-600/40 text-amber-700 dark:text-amber-300" },
  DISPUTED: { label: "Discutido", className: "border-rose-600/40 text-rose-700 dark:text-rose-300" },
  REJECTED: { label: "Rechazado", className: "border-rose-600/40 text-rose-700 dark:text-rose-300" },
  ALLEGED: { label: "Alegado", className: "text-muted-foreground" },
  UNRESOLVED: { label: "Sin resolver", className: "text-muted-foreground" },
};

export function ProofBadge({ status }: { status?: string | null }) {
  const item = labels[status || "ALLEGED"] || labels.ALLEGED;
  return (
    <Badge variant="outline" className={cn("font-medium", item.className)}>
      {item.label}
    </Badge>
  );
}
