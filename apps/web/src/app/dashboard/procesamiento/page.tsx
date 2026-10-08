"use client";

/** Vista dedicada de monitorización de procesos OCR/ASR de toda la organización.
 * Se accede desde el icono de monitor del header (layout superior). */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Loader2, CheckCircle2, XCircle, Clock, AlertTriangle, Ban,
  FileText, Folder, Video, ChevronDown, ChevronUp, ExternalLink,
  Activity, Upload,
} from "lucide-react";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { useUploadStore } from "@/lib/upload-store";

interface ProcessingItem {
  id: string;
  kind: "document" | "media";
  filename: string;
  folder_path?: string;
  status: string;
  progress: number;
  detail?: string | null;
  page_count?: number;
  pages_done?: number;
  human_corrected_pages?: number;
  confidence_avg?: number | null;
  duration_ms?: number;
  segments_done?: number;
}

interface ActiveJob {
  id: string;
  job_type: string;
  status: string;
  attempts: number;
  error_code?: string | null;
  created_at: string;
  updated_at?: string;
  case_id: string;
  case_number?: string;
  case_title?: string;
  progress: number;
  /** true = el job no publica % por archivo (legal_extraction, graph_build…): mostrar "En curso…". */
  indeterminate?: boolean;
  can_cancel?: boolean;
  items: ProcessingItem[];
}

interface ActiveProcessing {
  jobs: ActiveJob[];
  active_count: number;
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    QUEUED: "En cola",
    RUNNING: "Procesando",
    RETRYING: "Reintentando",
    SUCCEEDED: "Completado",
    FAILED: "Fallido",
    CANCELLED: "Cancelado",
    UPLOADED: "Subido",
    OCR_PENDING: "OCR pendiente",
    OCR_RUNNING: "OCR en curso",
    OCR_COMPLETE: "OCR completado",
    REVIEW_REQUIRED: "Revisión requerida",
    ASR_PENDING: "ASR pendiente",
    ASR_RUNNING: "ASR en curso",
    ASR_COMPLETE: "ASR completado",
  };
  return map[status] ?? status;
}

function statusIcon(status: string, size = "h-4 w-4") {
  if (status === "RUNNING" || status === "OCR_RUNNING" || status === "ASR_RUNNING")
    return <Loader2 className={`${size} animate-spin text-blue-500`} />;
  if (status === "QUEUED" || status === "OCR_PENDING" || status === "ASR_PENDING")
    return <Clock className={`${size} text-yellow-500`} />;
  if (status === "SUCCEEDED" || status === "OCR_COMPLETE" || status === "ASR_COMPLETE")
    return <CheckCircle2 className={`${size} text-green-500`} />;
  if (status === "FAILED")
    return <XCircle className={`${size} text-red-500`} />;
  if (status === "REVIEW_REQUIRED")
    return <AlertTriangle className={`${size} text-orange-500`} />;
  if (status === "CANCELLED")
    return <Ban className={`${size} text-muted-foreground`} />;
  return <Clock className={`${size} text-muted-foreground`} />;
}

function statusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "FAILED") return "destructive";
  if (status === "SUCCEEDED" || status === "OCR_COMPLETE" || status === "ASR_COMPLETE") return "default";
  if (status === "REVIEW_REQUIRED") return "secondary";
  return "outline";
}

function elapsedSince(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h ${m % 60}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

export default function ProcesamientoPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [expandedJobs, setExpandedJobs] = useState<Set<string>>(new Set());

  const { data, isLoading } = useQuery({
    queryKey: ["active-processing"],
    queryFn: () => api.get<ActiveProcessing>("/cases/processing/active"),
    refetchInterval: (query) => {
      const jobs = query.state.data?.jobs ?? [];
      return jobs.some((j) => ["QUEUED", "RUNNING", "RETRYING"].includes(j.status)) ? 3000 : 15000;
    },
    refetchIntervalInBackground: false,
  });

  const cancel = useMutation({
    mutationFn: (jobId: string) => api.post(`/cases/jobs/${jobId}/cancel`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["active-processing"] });
      toast.success("Trabajo cancelado");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error al cancelar"),
  });

  const toggleJob = (jobId: string) => {
    setExpandedJobs((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  };

  const jobs = data?.jobs ?? [];
  const activeJobs = jobs.filter((j) => ["QUEUED", "RUNNING", "RETRYING"].includes(j.status));
  const uploads = useUploadStore((s) => s.tasks);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
            <Activity className="h-7 w-7" />
            Monitor de procesamiento
          </h1>
          <p className="text-muted-foreground">
            Procesos OCR/ASR activos de toda la organización. Los trabajos continúan en el servidor
            aunque cierres el navegador.
          </p>
        </div>
        {activeJobs.length > 0 && (
          <Badge variant="secondary" className="text-sm">
            {activeJobs.length} activo(s)
          </Badge>
        )}
      </div>

      {uploads.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-lg">
              <Upload className="h-5 w-5" /> Subidas en curso ({uploads.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {uploads.map((u) => (
              <div key={u.id} className="space-y-1">
                <div className="flex items-center justify-between gap-2 text-sm">
                  <span className="min-w-0 flex-1 truncate">{u.filename}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {u.stage === "uploading" ? `${u.pct}%`
                      : u.stage === "processing" ? (u.detail || "Procesando…")
                      : u.stage === "done" ? "Listo"
                      : (u.detail || "Error")}
                  </span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded bg-muted">
                  <div className={`h-full transition-all ${u.stage === "error" ? "bg-destructive" : "bg-primary"}`}
                    style={{ width: `${u.stage === "error" ? 100 : u.pct}%` }} />
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-lg">
            <Loader2 className={`h-5 w-5 ${activeJobs.length > 0 ? "animate-spin" : ""}`} />
            Trabajos
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : jobs.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12">
              <CheckCircle2 className="mb-4 h-12 w-12 text-green-500" />
              <p className="text-muted-foreground">No hay procesos activos en este momento.</p>
            </div>
          ) : (
            jobs.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                expanded={expandedJobs.has(job.id)}
                onToggle={() => toggleJob(job.id)}
                onCancel={() => cancel.mutate(job.id)}
                cancelling={cancel.isPending}
                onOpenCase={() => router.push(`/dashboard/procesos/${job.case_id}`)}
              />
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function JobCard({
  job,
  expanded,
  onToggle,
  onCancel,
  cancelling,
  onOpenCase,
}: {
  job: ActiveJob;
  expanded: boolean;
  onToggle: () => void;
  onCancel: () => void;
  cancelling: boolean;
  onOpenCase: () => void;
}) {
  const isActive = ["QUEUED", "RUNNING", "RETRYING"].includes(job.status);
  const isFailed = job.status === "FAILED";
  const isCancelled = job.status === "CANCELLED";
  // Detalle del paso en curso (p. ej. "Diarizando hablantes… (tramo 2/9 · embeddings 30%)")
  // para mostrarlo en la fila del job SIN necesidad de expandirlo.
  const activeDetail = job.items.find((i) => i.detail)?.detail;

  return (
    <div className={`rounded-lg border ${isFailed ? "border-red-200 bg-red-50/50 dark:border-red-900 dark:bg-red-950/20" : ""} ${isCancelled ? "opacity-60" : ""}`}>
      <div className="flex items-center gap-3 p-3">
        <div className="shrink-0">{statusIcon(job.status)}</div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate font-medium">
              {job.case_number || job.case_title || "Caso"}
            </span>
            <Badge variant="outline">
              {job.job_type === "file_ingest" ? "Ingesta" :
               job.job_type === "document_ocr" ? "OCR" :
               job.job_type === "media_asr" ? "ASR" : job.job_type}
            </Badge>
            <Badge variant={statusVariant(job.status)}>{statusLabel(job.status)}</Badge>
            {job.attempts > 1 && (
              <Badge variant="outline" title="Reintentos">×{job.attempts}</Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {job.items.length} archivo(s) · Iniciado hace {elapsedSince(job.created_at)}
            {activeDetail && ` · ${activeDetail}`}
            {job.error_code && ` · Error: ${job.error_code}`}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {isActive && (
            <div className="w-24">
              <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                {job.items.length === 0 || job.indeterminate ? (
                  // Jobs sin progreso real (legal_extraction, graph_build…): indeterminado.
                  <div className="h-2 w-1/3 animate-pulse rounded-full bg-primary" />
                ) : (
                  <div
                    className="h-2 rounded-full bg-primary transition-all"
                    style={{ width: `${job.progress}%` }}
                  />
                )}
              </div>
              <p className="mt-0.5 text-center text-xs text-muted-foreground">
                {job.items.length === 0 || job.indeterminate ? "En curso…" : `${job.progress}%`}
              </p>
            </div>
          )}

          <Button variant="ghost" size="icon" onClick={onOpenCase} title="Ir al caso">
            <ExternalLink className="h-4 w-4" />
          </Button>

          {(job.can_cancel ?? isActive) && !isCancelled && (
            <Button
              variant="ghost"
              size="icon"
              onClick={onCancel}
              disabled={cancelling}
              title="Cancelar este trabajo"
            >
              <Ban className="h-4 w-4 text-destructive" />
            </Button>
          )}

          <Button variant="ghost" size="icon" onClick={onToggle}>
            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>
        </div>
      </div>

      {expanded && (
        <div className="border-t px-3 py-2">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground">
                <th className="pb-2 font-medium">Archivo</th>
                <th className="pb-2 font-medium">Carpeta</th>
                <th className="pb-2 font-medium">Estado</th>
                <th className="pb-2 font-medium text-right">Progreso</th>
                <th className="pb-2 font-medium text-right">Confianza</th>
              </tr>
            </thead>
            <tbody>
              {job.items.map((item) => (
                <tr key={item.id} className="border-t">
                  <td className="py-2 pr-2">
                    <div className="flex items-center gap-2">
                      {item.kind === "document" ? (
                        <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                      ) : (
                        <Video className="h-4 w-4 shrink-0 text-muted-foreground" />
                      )}
                      <span className="truncate max-w-[200px]" title={item.filename}>
                        {item.filename}
                      </span>
                    </div>
                  </td>
                  <td className="py-2 pr-2">
                    <div className="flex items-center gap-1 text-muted-foreground">
                      <Folder className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate max-w-[150px] text-xs" title={item.folder_path}>
                        {item.folder_path || "—"}
                      </span>
                    </div>
                  </td>
                  <td className="py-2 pr-2">
                    <div className="flex items-center gap-1.5">
                      {statusIcon(item.status, "h-3.5 w-3.5")}
                      <span className="text-xs">{statusLabel(item.status)}</span>
                      {item.detail ? (
                        <span className="text-xs text-muted-foreground">· {item.detail}</span>
                      ) : null}
                      {item.human_corrected_pages ? (
                        <Badge variant="secondary" className="text-[10px]">
                          {item.human_corrected_pages} corregida(s)
                        </Badge>
                      ) : null}
                    </div>
                  </td>
                  <td className="py-2 text-right">
                    <div className="flex items-center justify-end gap-2">
                      {item.page_count ? (
                        <span className="text-xs text-muted-foreground">
                          {item.pages_done}/{item.page_count} págs
                        </span>
                      ) : item.duration_ms ? (
                        <span className="text-xs text-muted-foreground">
                          {item.segments_done} segs
                        </span>
                      ) : null}
                      <div className="w-16">
                        <div className="h-1.5 w-full rounded-full bg-muted">
                          <div
                            className="h-1.5 rounded-full bg-primary transition-all"
                            style={{ width: `${item.progress}%` }}
                          />
                        </div>
                      </div>
                      <span className="w-8 text-right text-xs">{item.progress}%</span>
                    </div>
                  </td>
                  <td className="py-2 text-right">
                    {item.confidence_avg != null ? (
                      <Badge
                        variant={item.confidence_avg >= 0.85 ? "default" : item.confidence_avg >= 0.6 ? "secondary" : "destructive"}
                      >
                        {(item.confidence_avg * 100).toFixed(0)}%
                      </Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
