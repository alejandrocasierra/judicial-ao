"use client";

/** Monitor de procesos OCR/ASR en tiempo real.
 * Muestra jobs activos con progreso, carpeta, confianza y opción de cancelar.
 * Se auto-refresca cada 3 segundos cuando hay trabajos activos. */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Loader2, CheckCircle2, XCircle, Clock, AlertTriangle, Ban,
  FileText, Folder, Video, ChevronDown, ChevronUp,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

interface ProcessingItem {
  id: string;
  kind: "document" | "media";
  filename: string;
  folder_path: string;
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

interface ProcessingJob {
  id: string;
  job_type: string;
  status: string;
  attempts: number;
  error_code?: string | null;
  created_at: string;
  updated_at: string;
  progress: number;
  can_cancel: boolean;
  items: ProcessingItem[];
}

interface ProcessingStatus {
  case_id: string;
  jobs: ProcessingJob[];
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

function statusIcon(status: string) {
  if (status === "RUNNING" || status === "OCR_RUNNING" || status === "ASR_RUNNING")
    return <Loader2 className="h-4 w-4 animate-spin text-blue-500" />;
  if (status === "QUEUED" || status === "OCR_PENDING" || status === "ASR_PENDING")
    return <Clock className="h-4 w-4 text-yellow-500" />;
  if (status === "SUCCEEDED" || status === "OCR_COMPLETE" || status === "ASR_COMPLETE")
    return <CheckCircle2 className="h-4 w-4 text-green-500" />;
  if (status === "FAILED")
    return <XCircle className="h-4 w-4 text-red-500" />;
  if (status === "REVIEW_REQUIRED")
    return <AlertTriangle className="h-4 w-4 text-orange-500" />;
  if (status === "CANCELLED")
    return <Ban className="h-4 w-4 text-muted-foreground" />;
  return <Clock className="h-4 w-4 text-muted-foreground" />;
}

function statusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "FAILED") return "destructive";
  if (status === "SUCCEEDED" || status === "OCR_COMPLETE" || status === "ASR_COMPLETE") return "default";
  if (status === "REVIEW_REQUIRED") return "secondary";
  return "outline";
}

function fmtDuration(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h ${m % 60}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

function elapsedSince(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  return fmtDuration(ms);
}

export function ProcessingMonitor({ caseId }: { caseId: string }) {
  const qc = useQueryClient();
  const [expandedJobs, setExpandedJobs] = useState<Set<string>>(new Set());

  const { data, isLoading } = useQuery({
    queryKey: ["ocr-processing", caseId],
    queryFn: () => api.get<ProcessingStatus>(`/cases/${caseId}/processing/ocr-status`),
    refetchInterval: (query) => {
      const jobs = query.state.data?.jobs ?? [];
      const hasActive = jobs.some((j) => ["QUEUED", "RUNNING", "RETRYING"].includes(j.status));
      return hasActive ? 3000 : false;
    },
  });

  const cancel = useMutation({
    mutationFn: (jobId: string) => api.post(`/cases/${caseId}/jobs/${jobId}/cancel`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ocr-processing", caseId] });
      qc.invalidateQueries({ queryKey: ["files", caseId] });
      toast.success("Job cancelado");
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
  const recentJobs = jobs.filter((j) => !["QUEUED", "RUNNING", "RETRYING"].includes(j.status)).slice(0, 5);

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    );
  }

  if (jobs.length === 0) {
    return null;
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Loader2 className={`h-5 w-5 ${activeJobs.length > 0 ? "animate-spin" : ""}`} />
          Monitor de procesamiento
          {activeJobs.length > 0 && (
            <Badge variant="secondary">{activeJobs.length} activo(s)</Badge>
          )}
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          Los trabajos continúan en el servidor aunque cierres esta ventana.
          Puedes cancelar trabajos en cola o en ejecución.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {activeJobs.length > 0 && (
          <div className="space-y-3">
            <h4 className="text-sm font-medium text-muted-foreground">Trabajos activos</h4>
            {activeJobs.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                expanded={expandedJobs.has(job.id)}
                onToggle={() => toggleJob(job.id)}
                onCancel={() => cancel.mutate(job.id)}
                cancelling={cancel.isPending}
              />
            ))}
          </div>
        )}

        {recentJobs.length > 0 && (
          <div className="space-y-3">
            <h4 className="text-sm font-medium text-muted-foreground">Recientes</h4>
            {recentJobs.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                expanded={expandedJobs.has(job.id)}
                onToggle={() => toggleJob(job.id)}
                onCancel={() => cancel.mutate(job.id)}
                cancelling={cancel.isPending}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function JobCard({
  job,
  expanded,
  onToggle,
  onCancel,
  cancelling,
}: {
  job: ProcessingJob;
  expanded: boolean;
  onToggle: () => void;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const isActive = ["QUEUED", "RUNNING", "RETRYING"].includes(job.status);
  const isFailed = job.status === "FAILED";
  const isCancelled = job.status === "CANCELLED";
  const activeDetail = job.items.find((i) => i.detail)?.detail;

  return (
    <div className={`rounded-lg border ${isFailed ? "border-red-200 bg-red-50/50 dark:border-red-900 dark:bg-red-950/20" : ""} ${isCancelled ? "opacity-60" : ""}`}>
      <div className="flex items-center gap-3 p-3">
        <div className="shrink-0">{statusIcon(job.status)}</div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-medium">
              {job.job_type === "file_ingest" ? "Ingesta de archivos" :
               job.job_type === "document_ocr" ? "OCR de documentos" :
               job.job_type === "media_asr" ? "Transcripción ASR" : job.job_type}
            </span>
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
              <div className="h-2 w-full rounded-full bg-muted">
                <div
                  className="h-2 rounded-full bg-primary transition-all"
                  style={{ width: `${job.progress}%` }}
                />
              </div>
              <p className="mt-0.5 text-center text-xs text-muted-foreground">{job.progress}%</p>
            </div>
          )}

          {job.can_cancel && !isCancelled && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onCancel}
              disabled={cancelling}
              title="Cancelar este trabajo"
            >
              <Ban className="h-4 w-4" />
            </Button>
          )}

          <Button variant="ghost" size="sm" onClick={onToggle}>
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
                        {item.folder_path}
                      </span>
                    </div>
                  </td>
                  <td className="py-2 pr-2">
                    <div className="flex items-center gap-1.5">
                      {statusIcon(item.status)}
                      <span className="text-xs">{statusLabel(item.status)}</span>
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
