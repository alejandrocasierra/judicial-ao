"use client";

/** Visor OCR reutilizable: muestra el PDF de una hoja y/o su texto extraído.
 * Se abre como modal sobre la vista actual (no navega), de modo que al cerrarlo
 * el usuario permanece donde estaba (p. ej. en el gestor de archivos del proceso). */

import React, { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Save, ChevronLeft, ChevronRight, Bot, ZoomIn, ZoomOut, Maximize,
  Columns2, Image as ImageIcon, Download, RefreshCw, FileText, FileCode,
} from "lucide-react";
import { toast } from "sonner";

interface PageMeta {
  page_number: number;
  folio?: string;
  needs_review: boolean;
  ocr_confidence: number;
  has_image: boolean;
  chars: number;
  human_corrected?: boolean;
}

interface OcrVersion {
  mode: string;
  pages: number;
  confidence_avg?: number | null;
  last_run: string;
}

interface DocumentInfo {
  id: string;
  filename: string;
  processing_status?: string;
  ocr_mode?: string | null;
}

interface OcrVersionsResponse {
  document_id: string;
  current_mode: string | null;
  processing_status: string;
  versions: OcrVersion[];
}

interface LayoutWord {
  text: string;
  confidence: number;
  bbox?: Record<string, unknown>;
}

interface StructuredFields {
  [key: string]: string | StructuredFields | StructuredFields[] | undefined;
}

interface PageLayout {
  words?: LayoutWord[];
  structured?: StructuredFields;
}

type ViewMode = "split" | "pdf" | "text";

export function DocumentOcrViewer({
  caseId,
  documentId,
  filename,
  initialPage,
  onClose,
}: {
  caseId: string;
  documentId: string | null;
  filename?: string | null;
  /** Página inicial al abrir (p. ej. una cita del chat: "doc p.12"). */
  initialPage?: number | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const open = !!documentId;

  const [page, setPage] = useState(initialPage || 1);
  const [textDraft, setTextDraft] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("split");
  const [reprocessDialog, setReprocessDialog] = useState(false);
  const [selectedMode, setSelectedMode] = useState<string>("basico");
  const [viewOcrMode, setViewOcrMode] = useState<string>("current");

  // Zoom / pan de la imagen renderizada
  const [zoom, setZoom] = useState(1);
  const [nat, setNat] = useState<{ w: number; h: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const viewRef = useRef<HTMLDivElement>(null);
  const dragStart = useRef({ x: 0, y: 0, sl: 0, st: 0 });
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  const { data: pages } = useQuery({
    queryKey: ["pages", caseId, documentId, viewOcrMode],
    queryFn: () => {
      const params = viewOcrMode !== "current" ? `?mode=${viewOcrMode}` : "";
      return api.get<{ pages: PageMeta[] }>(`/cases/${caseId}/documents/${documentId}/pages${params}`).then((r) => r.pages);
    },
    enabled: !!documentId,
  });
  const { data: pageData, refetch: refetchPage } = useQuery({
    queryKey: ["page", caseId, documentId, page, viewOcrMode],
    queryFn: () => {
      const params = viewOcrMode !== "current" ? `?mode=${viewOcrMode}` : "";
      return api.get<{ text: string; folio?: string; needs_review: boolean; ocr_confidence?: number | null; mode?: string; layout_json?: string | PageLayout }>(
        `/cases/${caseId}/documents/${documentId}/pages/${page}${params}`
      );
    },
    enabled: !!documentId,
  });
  const { data: docInfo } = useQuery({
    queryKey: ["document", caseId, documentId],
    queryFn: () => api.get<DocumentInfo>(`/cases/${caseId}/documents/${documentId}`),
    enabled: !!documentId,
  });
  const { data: ocrVersions } = useQuery({
    queryKey: ["ocr-versions", caseId, documentId],
    queryFn: () => api.get<OcrVersionsResponse>(`/cases/${caseId}/documents/${documentId}/ocr-versions`),
    enabled: !!documentId,
  });

  // Ajuste en render al cambiar de documento o de cita (sin efecto de estado):
  // hoja inicial (o de la cita), vista dividida y zoom/encuadre reiniciados.
  const [prevKey, setPrevKey] = useState(`${documentId}|${initialPage}`);
  if (`${documentId}|${initialPage}` !== prevKey) {
    setPrevKey(`${documentId}|${initialPage}`);
    if (documentId) { setPage(initialPage || 1); setViewMode("split"); setZoom(1); setNat(null); }
  }
  const [prevPage, setPrevPage] = useState(page);
  if (page !== prevPage) { setPrevPage(page); setZoom(1); setNat(null); }

  useEffect(() => {
    let revoke: string | null = null;
    (async () => {
      setImageUrl(null);
      if (!documentId) return;
      try {
        const b = await api.blob(`/cases/${caseId}/documents/${documentId}/pages/${page}/image`);
        const u = URL.createObjectURL(b);
        revoke = u;
        setImageUrl(u);
      } catch { setImageUrl(null); }
    })();
    return () => { if (revoke) URL.revokeObjectURL(revoke); };
  }, [caseId, documentId, page]);

  // El texto mostrado es un borrador del usuario o, si no editó, el de la página.
  const text = textDraft ?? pageData?.text ?? "";
  const [prevPageData, setPrevPageData] = useState(pageData);
  if (pageData !== prevPageData) { setPrevPageData(pageData); setTextDraft(null); }

  // Al cambiar de hoja, reinicia el scroll del visor (solo DOM).
  useEffect(() => {
    if (viewRef.current) { viewRef.current.scrollLeft = 0; viewRef.current.scrollTop = 0; }
  }, [page, documentId]);

  // Zoom con la rueda del mouse (no pasivo para poder prevenir el scroll de la página).
  useEffect(() => {
    const el = viewRef.current;
    if (!el || !open) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const offsetX = e.clientX - rect.left;
      const offsetY = e.clientY - rect.top;
      setZoom((z) => {
        const r = e.deltaY < 0 ? 1.12 : 1 / 1.12;
        const nz = Math.min(8, Math.max(0.2, z * r));
        const factor = nz / z;
        requestAnimationFrame(() => {
          if (viewRef.current) {
            viewRef.current.scrollLeft = (viewRef.current.scrollLeft + offsetX) * factor - offsetX;
            viewRef.current.scrollTop = (viewRef.current.scrollTop + offsetY) * factor - offsetY;
          }
        });
        return nz;
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [open]);

  function onImgLoad(e: React.SyntheticEvent<HTMLImageElement>) {
    const w = e.currentTarget.naturalWidth;
    const h = e.currentTarget.naturalHeight;
    setNat({ w, h });
    const cw = viewRef.current?.clientWidth || w;
    setZoom(Math.min(1, cw / w));
  }
  function zoomBy(factor: number) { setZoom((z) => Math.min(8, Math.max(0.2, z * factor))); }
  function fitWidth() {
    const cw = viewRef.current?.clientWidth || 0;
    if (nat && cw) setZoom(Math.max(0.2, Math.min(8, cw / nat.w)));
  }
  function onMouseDown(e: React.MouseEvent) {
    const el = viewRef.current;
    if (!el) return;
    setDragging(true);
    dragStart.current = { x: e.clientX, y: e.clientY, sl: el.scrollLeft, st: el.scrollTop };
  }
  function onMouseMove(e: React.MouseEvent) {
    if (!dragging) return;
    const el = viewRef.current;
    if (!el) return;
    el.scrollLeft = dragStart.current.sl - (e.clientX - dragStart.current.x);
    el.scrollTop = dragStart.current.st - (e.clientY - dragStart.current.y);
  }

  const save = useMutation({
    mutationFn: () => {
      // El modo editado es el que se está viendo: una versión concreta o el modo actual del documento.
      const mode = viewOcrMode === "current" ? docInfo?.ocr_mode ?? null : viewOcrMode;
      return api.patch<{ confidence?: number }>(
        `/cases/${caseId}/documents/${documentId}/pages/${page}`,
        mode ? { text, mode } : { text },
      );
    },
    onSuccess: (data) => {
      const pct = typeof data?.confidence === "number" ? ` Confianza del modo: ${Math.round(data.confidence * 100)}%.` : "";
      toast.success(`OCR corregido: diccionario actualizado, vector reindexado y grafo encolado.${pct}`);
      qc.invalidateQueries({ queryKey: ["pages", caseId, documentId] });
      qc.invalidateQueries({ queryKey: ["ocr-versions", caseId, documentId] });
      refetchPage();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error al guardar"),
  });

  const reprocess = useMutation<{ job_id?: string }, Error, { ocr_mode: string }>({
    mutationFn: async (variables) => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 30000);
      try {
        return await api.post<{ job_id?: string }>(`/cases/${caseId}/documents/${documentId}/reprocess?ocr_mode=${variables.ocr_mode}`);
      } finally {
        clearTimeout(timeout);
      }
    },
    onSuccess: (data) => {
      toast.success(`Reprocesamiento encolado${data.job_id ? ` (job ${data.job_id.slice(0, 8)}...)` : ""}. El estado se actualiza automáticamente.`);
      setReprocessDialog(false);
      qc.invalidateQueries({ queryKey: ["documents", caseId] });
      qc.invalidateQueries({ queryKey: ["ocr-processing", caseId] });
      qc.invalidateQueries({ queryKey: ["document", caseId, documentId] });
      // Refrescar el documento tras unos segundos para ver el nuevo estado.
      setTimeout(() => qc.invalidateQueries({ queryKey: ["documents", caseId] }), 5000);
    },
    onError: (e) => {
      if (e instanceof Error && e.name === "AbortError") {
        toast.error("El servidor tardó demasiado en responder. Verifica que el worker esté corriendo.");
      } else {
        toast.error(e instanceof Error ? e.message : "Error al reprocesar");
      }
    },
  });

  async function downloadDoc() {
    if (!documentId) return;
    try {
      const blob = await api.blob(`/cases/${caseId}/documents/${documentId}/download`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename || "documento.pdf";
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("No se pudo descargar el PDF");
    }
  }

  async function downloadMarkdown() {
    if (!documentId) return;
    try {
      const r = await api.get<{ markdown: string }>(`/cases/${caseId}/documents/${documentId}/markdown`);
      const blob = new Blob([r.markdown], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${(filename || "documento").replace(/\.[^.]+$/, "")}.md`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("No se pudo generar el Markdown");
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-6xl">
        <DialogHeader><DialogTitle>{filename}</DialogTitle></DialogHeader>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Button variant="outline" size="icon" onClick={() => setPage((p) => Math.max(1, p - 1))}><ChevronLeft className="h-4 w-4" /></Button>
            <Select value={String(page)} onValueChange={(v) => setPage(Number(v))}>
              <SelectTrigger className="w-[230px]"><SelectValue placeholder={`Hoja ${page}`} /></SelectTrigger>
              <SelectContent>
                {pages?.map((pg) => {
                  const pct = Math.round((pg.ocr_confidence ?? 1) * 100);
                  return (
                    <SelectItem key={pg.page_number} value={String(pg.page_number)}>
                      Hoja {pg.page_number} · {pct}%{pg.human_corrected ? " ✎ editada" : ""}
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
            <span className="text-xs text-muted-foreground">de {pages?.length ?? 1}</span>
            <Button variant="outline" size="icon" onClick={() => setPage((p) => Math.min(pages?.length ?? 1, p + 1))}><ChevronRight className="h-4 w-4" /></Button>
            {pageData?.ocr_confidence != null && (
              <Badge variant={pageData.ocr_confidence >= 0.999 ? "secondary" : pageData.ocr_confidence >= 0.8 ? "default" : "destructive"}>
                Confianza {Math.round(pageData.ocr_confidence * 100)}%
              </Badge>
            )}
            {docInfo?.ocr_mode && (
              <Badge variant={docInfo.ocr_mode === "document_ai" ? "default" : "secondary"}>
                {docInfo.ocr_mode === "document_ai" ? "Document AI" : "OCR Básico"}
              </Badge>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {ocrVersions && ocrVersions.versions.length > 0 && (
              <Select value={viewOcrMode} onValueChange={setViewOcrMode}>
                <SelectTrigger className="w-[160px]">
                  <SelectValue placeholder="Versión OCR" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="current">
                    Actual ({docInfo?.ocr_mode === "document_ai" ? "Document AI" : "Básico"})
                  </SelectItem>
                  {ocrVersions.versions.map((v) => (
                    <SelectItem key={v.mode} value={v.mode}>
                      {v.mode === "document_ai" ? "OCR Document AI" : "OCR Básico"}
                      {v.confidence_avg != null && ` (${(v.confidence_avg * 100).toFixed(0)}%)`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <div className="flex items-center rounded-md border">
              <Button variant={viewMode === "pdf" ? "default" : "ghost"} size="sm" className="rounded-r-none"
                onClick={() => setViewMode("pdf")} title="Ver solo el PDF, sin texto">
                <ImageIcon className="mr-1 h-4 w-4" />Solo PDF
              </Button>
              <Button variant={viewMode === "text" ? "default" : "ghost"} size="sm" className="rounded-none border-x"
                onClick={() => setViewMode("text")} title="Ver solo el texto extraído">
                <FileText className="mr-1 h-4 w-4" />Solo texto
              </Button>
              <Button variant={viewMode === "split" ? "default" : "ghost"} size="sm" className="rounded-l-none"
                onClick={() => setViewMode("split")} title="Ver el texto extraído por hoja junto al PDF">
                <Columns2 className="mr-1 h-4 w-4" />Texto por hoja
              </Button>
            </div>
            {viewMode !== "pdf" && (
              <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}><Save className="mr-1 h-4 w-4" />Guardar</Button>
            )}
            <Button variant="outline" size="sm" onClick={downloadDoc} title="Descargar el PDF original">
              <Download className="mr-1 h-4 w-4" />Descargar
            </Button>
            <Button variant="outline" size="sm" onClick={downloadMarkdown} title="Descargar el texto en Markdown (con jerarquía)">
              <FileCode className="mr-1 h-4 w-4" />Markdown
            </Button>
            <Button variant="outline" size="sm" onClick={() => setReprocessDialog(true)} disabled={reprocess.isPending}
              title="Volver a ejecutar el OCR con otro método">
              <RefreshCw className="mr-1 h-4 w-4" />{reprocess.isPending ? "Encolando…" : "Reprocesar"}
            </Button>
          </div>
        </div>

        <div className={viewMode === "split" ? "grid gap-4 md:grid-cols-2" : "grid gap-4"}>
          {viewMode !== "pdf" && (
            <div className="space-y-2">
              <Textarea value={text} onChange={(e) => setTextDraft(e.target.value)}
                rows={viewMode === "text" ? 26 : 18} className="font-mono text-xs leading-relaxed" />
              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                <Bot className="h-3.5 w-3.5" />Al guardar, la corrección alimenta el diccionario y reindexa el vector y el grafo.
              </p>
            </div>
          )}

          {viewMode !== "text" && (
            <div className="space-y-2">
              <div className="flex items-center gap-1">
                <Button variant="outline" size="icon" onClick={() => zoomBy(1 / 1.2)} title="Alejar"><ZoomOut className="h-4 w-4" /></Button>
                <span className="w-14 text-center text-xs">{Math.round(zoom * 100)}%</span>
                <Button variant="outline" size="icon" onClick={() => zoomBy(1.2)} title="Acercar"><ZoomIn className="h-4 w-4" /></Button>
                <Button variant="outline" size="sm" onClick={fitWidth} title="Ajustar al ancho"><Maximize className="mr-1 h-4 w-4" />Ajustar</Button>
                <span className="ml-2 text-xs text-muted-foreground">Rueda = zoom · arrastrar = desplazar</span>
              </div>
              <div
                ref={viewRef}
                className={`${viewMode === "pdf" ? "h-[70vh]" : "h-[520px]"} overflow-auto rounded-md border bg-muted/30 ${dragging ? "select-none" : ""}`}
                style={{ cursor: dragging ? "grabbing" : "grab" }}
                onMouseDown={onMouseDown}
                onMouseMove={onMouseMove}
                onMouseUp={() => setDragging(false)}
                onMouseLeave={() => setDragging(false)}
              >
                {imageUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element -- la página llega como blob (object URL) autenticado; next/image no aplica a blobs
                  <img
                    src={imageUrl}
                    alt={`Hoja ${page}`}
                    onLoad={onImgLoad}
                    draggable={false}
                    style={{ width: nat ? nat.w * zoom : "100%", maxWidth: "none", display: "block" }}
                  />
                ) : <p className="p-8 text-sm text-muted-foreground">Sin imagen renderizada para esta hoja.</p>}
              </div>
            </div>
          )}
        </div>

        <Dialog open={reprocessDialog} onOpenChange={setReprocessDialog}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Reprocesar documento</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Elige el método de OCR para reprocesar este documento.
                {docInfo?.ocr_mode && (
                  <span className="block mt-1">
                    Método actual: <strong>{docInfo.ocr_mode === "document_ai" ? "Document AI" : "OCR Básico"}</strong>
                  </span>
                )}
              </p>
              <div className="space-y-2">
                <label className="flex items-start gap-3 rounded-lg border p-3 cursor-pointer hover:bg-muted/50">
                  <input
                    type="radio"
                    name="reprocess_mode"
                    value="basico"
                    checked={selectedMode === "basico"}
                    onChange={() => setSelectedMode("basico")}
                    className="mt-1"
                  />
                  <div>
                    <p className="font-medium">OCR Básico</p>
                    <p className="text-sm text-muted-foreground">
                      Procesamiento local con Tesseract/Docling. Rápido y privado.
                    </p>
                  </div>
                </label>
                <label className="flex items-start gap-3 rounded-lg border p-3 cursor-pointer hover:bg-muted/50">
                  <input
                    type="radio"
                    name="reprocess_mode"
                    value="document_ai"
                    checked={selectedMode === "document_ai"}
                    onChange={() => setSelectedMode("document_ai")}
                    className="mt-1"
                  />
                  <div>
                    <p className="font-medium">OCR Document AI (Google Cloud)</p>
                    <p className="text-sm text-muted-foreground">
                      Procesamiento en la nube con Google Document AI. Mayor precisión en formularios y manuscritos.
                    </p>
                  </div>
                </label>
              </div>
              <div className="flex gap-2 justify-end">
                <Button variant="outline" onClick={() => setReprocessDialog(false)}>
                  Cancelar
                </Button>
                <Button
                  onClick={() => reprocess.mutate({ ocr_mode: selectedMode })}
                  disabled={reprocess.isPending}
                >
                  {reprocess.isPending ? "Encolando…" : "Reprocesar"}
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </DialogContent>
    </Dialog>
  );
}
