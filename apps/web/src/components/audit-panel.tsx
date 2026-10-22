"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, ShieldCheck, UserCheck } from "lucide-react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type Audit = {
  id: string;
  chat_message_id?: string | null;
  model_run_id?: string | null;
  status: string;
  mode: string;
  claims_total: number;
  claims_supported: number;
  claims_blocked: number;
  findings_critical: number;
  duration_ms?: number | null;
  created_at: string;
};
type Finding = {
  id: string;
  claim_index?: number | null;
  code: string;
  severity: string;
  message: string;
  blocking: boolean;
};
type Claim = {
  id: string;
  claim_index: number;
  claim_text: string;
  claim_type: string;
  citation_handles: string[];
  status: string;
  reasons: unknown[];
};
type AuditDetail = Audit & { claims: Claim[]; findings: Finding[]; citations: unknown[] };
type SecondOpinion = {
  id: string;
  status: string;
  verdict?: string | null;
  summary?: string | null;
  chat_message_id?: string | null;
  created_at: string;
  claims?: Array<{ claim_text: string; comparison: string }>;
  findings?: Array<{ code: string; severity: string; message: string }>;
};

const statusStyle: Record<string, string> = {
  PASSED: "border-emerald-600/40 text-emerald-700 dark:text-emerald-300",
  WARNING: "border-amber-600/40 text-amber-700 dark:text-amber-300",
  NEEDS_REVIEW: "border-amber-600/40 text-amber-700 dark:text-amber-300",
  BLOCKED: "border-rose-600/40 text-rose-700 dark:text-rose-300",
  ERROR: "border-rose-600/40 text-rose-700 dark:text-rose-300",
};
const statusLabel: Record<string, string> = {
  PASSED: "Verificado",
  WARNING: "Advertencia",
  NEEDS_REVIEW: "Revisión",
  BLOCKED: "Bloqueado",
  ERROR: "Error",
};

function AuditStatus({ status }: { status: string }) {
  return <Badge variant="outline" className={cn(statusStyle[status] || "", "font-medium")}>{statusLabel[status] || status}</Badge>;
}

export function AuditPanel({ caseId }: { caseId: string }) {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const audits = useQuery({
    queryKey: ["audits", caseId],
    queryFn: () => api.get<{ audits: Audit[] }>(`/cases/${caseId}/audits`, { limit: "50" }),
  });
  const detail = useQuery({
    queryKey: ["audit", caseId, selectedId],
    queryFn: () => api.get<AuditDetail>(`/cases/${caseId}/audits/${selectedId}`),
    enabled: !!selectedId,
  });
  const runs = useQuery({
    queryKey: ["second-opinions", caseId],
    queryFn: () => api.get<{ runs: SecondOpinion[] }>(`/cases/${caseId}/second-opinions`, { limit: "20" }),
    refetchInterval: (q) => (q.state.data?.runs ?? []).some((r) => ["QUEUED", "RUNNING"].includes(r.status)) ? 3000 : false,
  });
  const requestSecond = useMutation({
    mutationFn: (messageId: string) => api.post(`/cases/${caseId}/messages/${messageId}/second-opinion`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["second-opinions", caseId] }),
  });

  useEffect(() => {
    if (!selectedId && audits.data?.audits?.length) setSelectedId(audits.data.audits[0].id);
  }, [audits.data, selectedId]);

  const selected = detail.data;
  const latestRun = selected?.chat_message_id
    ? runs.data?.runs?.find((r) => r.chat_message_id === selected.chat_message_id && r.status !== "CANCELLED")
    : undefined;

  return (
    <div className="space-y-4">
      <div>
        <h2 className="flex items-center gap-2 text-xl font-semibold">
          <ShieldCheck className="h-5 w-5 text-primary" /> Auditoría legal
        </h2>
        <p className="text-sm text-muted-foreground">
          Cada respuesta publicada queda registrada con sus claims, citas y hallazgos. Un hallazgo crítico bloquea el claim.
        </p>
      </div>

      {audits.isLoading && <p className="text-sm text-muted-foreground">Cargando auditorías…</p>}
      {audits.error && <p className="text-sm text-rose-600">No se pudieron cargar las auditorías.</p>}
      {!audits.isLoading && !audits.error && (audits.data?.audits.length ?? 0) === 0 && (
        <Card><CardContent className="p-8 text-center">
          <ShieldCheck className="mx-auto mb-2 h-8 w-8 text-muted-foreground" />
          <p className="font-medium">Aún no hay respuestas auditadas.</p>
          <p className="text-sm text-muted-foreground">Haz una pregunta en el chat; el gate legal se ejecuta antes de publicar.</p>
        </CardContent></Card>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(260px,360px)_1fr]">
        <div className="space-y-2">
          {audits.data?.audits.map((a) => (
            <button
              key={a.id}
              onClick={() => setSelectedId(a.id)}
              className={cn(
                "w-full rounded-lg border p-3 text-left text-sm transition-colors hover:bg-muted/40",
                selectedId === a.id && "border-primary bg-muted/30",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <AuditStatus status={a.status} />
                <span className="text-xs text-muted-foreground">
                  {a.claims_supported}/{a.claims_total} claims
                </span>
              </div>
              <p className="mt-2 line-clamp-2 text-muted-foreground">
                {a.findings_critical > 0 ? `${a.findings_critical} hallazgo(s) crítico(s)` : "Sin hallazgos críticos"}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">{new Date(a.created_at).toLocaleString()}</p>
            </button>
          ))}
        </div>

        <div className="min-w-0">
          {detail.isLoading && <p className="text-sm text-muted-foreground">Cargando detalle…</p>}
          {selected && (
            <div className="space-y-4">
              <Card><CardContent className="space-y-3 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <AuditStatus status={selected.status} />
                  <Badge variant="outline">{selected.mode}</Badge>
                  <span className="text-xs text-muted-foreground">
                    {selected.claims_supported} soportados · {selected.claims_blocked} bloqueados · {selected.findings_critical} críticos
                  </span>
                </div>
                {selected.chat_message_id ? (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => requestSecond.mutate(selected.chat_message_id!)}
                    disabled={requestSecond.isPending}
                  >
                    {requestSecond.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <UserCheck className="mr-2 h-4 w-4" />}
                    Solicitar segunda opinión
                  </Button>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    La segunda opinión se solicita desde una respuesta del chat.
                  </p>
                )}
                {requestSecond.isError && <p className="text-xs text-rose-600">No se pudo solicitar. Revisa el modelo de segunda opinión en Verificación.</p>}
                {latestRun && (
                  <div className="rounded-md border p-3 text-sm">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline">{latestRun.status}</Badge>
                      {latestRun.verdict && <Badge variant="secondary">{latestRun.verdict}</Badge>}
                    </div>
                    <p className="mt-1 text-muted-foreground">{latestRun.summary || "Segunda opinión en curso."}</p>
                  </div>
                )}
              </CardContent></Card>

              <div>
                <h3 className="mb-2 text-sm font-semibold">Claims</h3>
                <div className="space-y-2">
                  {selected.claims.map((c) => (
                    <div key={c.id} className="rounded-md border p-3 text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={c.status === "SUPPORTED" ? "outline" : "destructive"}>{c.status}</Badge>
                        <Badge variant="secondary">{c.claim_type}</Badge>
                        <span className="text-xs text-muted-foreground">{c.citation_handles.join(", ") || "sin cita"}</span>
                      </div>
                      <p className="mt-2">{c.claim_text}</p>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
                  <AlertTriangle className="h-4 w-4" /> Hallazgos
                </h3>
                {selected.findings.length === 0 ? (
                  <p className="flex items-center gap-2 text-sm text-emerald-700 dark:text-emerald-300">
                    <CheckCircle2 className="h-4 w-4" /> Sin hallazgos.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {selected.findings.map((f) => (
                      <div key={f.id} className="rounded-md border p-3 text-sm">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant={f.severity === "CRITICAL" ? "destructive" : "outline"}>{f.severity}</Badge>
                          <span className="font-mono text-xs">{f.code}</span>
                          {f.blocking && <Badge variant="outline">bloquea</Badge>}
                        </div>
                        <p className="mt-1 text-muted-foreground">{f.message}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
