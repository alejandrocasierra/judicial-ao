"use client";

import Link from "next/link";
import { ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";

const STYLES: Record<string, string> = {
  PASSED: "border-emerald-600/40 text-emerald-700 dark:text-emerald-300",
  WARNING: "border-amber-600/40 text-amber-700 dark:text-amber-300",
  NEEDS_REVIEW: "border-amber-600/40 text-amber-700 dark:text-amber-300",
  BLOCKED: "border-rose-600/40 text-rose-700 dark:text-rose-300",
  ERROR: "border-rose-600/40 text-rose-700 dark:text-rose-300",
};

const LABELS: Record<string, string> = {
  PASSED: "Auditoría: verificada",
  WARNING: "Auditoría: advertencia",
  NEEDS_REVIEW: "Auditoría: revisión",
  BLOCKED: "Auditoría: bloqueada",
  ERROR: "Auditoría: error",
};

/** Estado de auditoría de una respuesta, con enlace al detalle del expediente. */
export function AuditBadge({
  status,
  auditId,
  caseId,
  blocked,
}: {
  status?: string | null;
  auditId?: string | null;
  caseId: string;
  blocked?: boolean;
}) {
  if (!status) return null;
  const label = blocked ? "Publicación retenida" : LABELS[status] || `Auditoría: ${status}`;
  const style = blocked ? STYLES.NEEDS_REVIEW : STYLES[status] || "";
  const href = `/dashboard/procesos/${caseId}/auditoria${auditId ? `?audit=${auditId}` : ""}`;
  return (
    <Link
      href={href}
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
        style,
      )}
      title={`${label}. Abrir detalle de auditoría.`}
      aria-label={`${label}. Abrir detalle de auditoría.`}
    >
      <ShieldCheck className="h-3 w-3 shrink-0" aria-hidden="true" />
      <span className="truncate">{label}</span>
    </Link>
  );
}
