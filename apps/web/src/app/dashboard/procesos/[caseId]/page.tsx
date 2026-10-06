"use client";

import { useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DocumentOcrViewer } from "@/components/document-ocr-viewer";
import { MediaTranscriptViewer } from "@/components/media-transcript-viewer";
import { PartiesButton } from "@/components/parties-panel";
import { useChatStore } from "@/lib/chat-store";
import {
  Folder, FolderOpen, FolderPlus, Upload, ChevronRight, ArrowLeft,
  FileText, FileSpreadsheet, FileImage, Video, File as FileIcon,
  Download, Pencil, Trash2, Eye, MessageSquare, Database,
} from "lucide-react";
import { toast } from "sonner";

interface Case { id: string; case_number: string; title: string; }
interface FolderItem { id: string; parent_id: string | null; name: string; subfolders: number; files: number; }
interface FileItem {
  kind: "file" | "document" | "media";
  id: string; filename: string; title?: string | null; mime_type: string;
  size_bytes: number; page_count?: number | null; processing_status?: string | null; created_at: string;
}
interface UploadResult { filename: string; status: string; kind?: string; code?: string; processing?: string; ocr_mode?: string | null; }

const ACCEPT = ".xlsx,.docx,.pdf,.jpg,.jpeg,.png,.svg,.mp4";

function fmtSize(bytes: number) {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function fileIcon(mime: string) {
  if (mime.includes("spreadsheet") || mime.includes("excel")) return FileSpreadsheet;
  if (mime.startsWith("image/")) return FileImage;
  if (mime.startsWith("video/")) return Video;
  if (mime.includes("pdf") || mime.includes("word")) return FileText;
  return FileIcon;
}

function typeLabel(mime: string) {
  if (mime.includes("spreadsheet")) return "Excel";
  if (mime.includes("word")) return "Word";
  if (mime.includes("pdf")) return "PDF";
  if (mime.includes("svg")) return "SVG";
  if (mime.startsWith("image/")) return "Imagen";
  if (mime.startsWith("video/")) return "Video";
  return "Archivo";
}

export default function ProcesoDetallePage() {
  const { caseId } = useParams<{ caseId: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const openChat = useChatStore((s) => s.openChat);
  const [currentFolder, setCurrentFolder] = useState<string | null>(null);
  const [folderDialog, setFolderDialog] = useState<"create" | "rename" | null>(null);
  const [folderName, setFolderName] = useState("");
  const [selectedFolder, setSelectedFolder] = useState<FolderItem | null>(null);
  const [viewerDoc, setViewerDoc] = useState<FileItem | null>(null);
  const [viewerMedia, setViewerMedia] = useState<FileItem | null>(null);
  const [ocrDialog, setOcrDialog] = useState<{ files: File[] | null; mode: string }>({ files: null, mode: "none" });
  const [exporting, setExporting] = useState(false);
  const [savingCkp, setSavingCkp] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  async function downloadCkp() {
    if (!caseId || exporting) return;
    setExporting(true);
    try {
      const blob = await api.blob(`/cases/${caseId}/export`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `CKP-${proc?.case_number || caseId}.zip`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success("CKP exportado (manifest + JSON/JSONL + Markdown + chunks + grafo)");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "No se pudo exportar el CKP");
    } finally {
      setExporting(false);
    }
  }

  async function persistCkp() {
    if (!caseId || savingCkp) return;
    setSavingCkp(true);
    try {
      const r = await api.post<{ snapshot: string; files: number; bytes: number; prefix: string }>(
        `/cases/${caseId}/ckp/persist`);
      const mb = (r.bytes / 1048576).toFixed(1);
      toast.success(`CKP guardado en storage: ${r.files} archivos (${mb} MB) · ${r.prefix}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "No se pudo guardar el CKP en storage");
    } finally {
      setSavingCkp(false);
    }
  }

  const { data: proc } = useQuery({
    queryKey: ["case", caseId],
    queryFn: () => api.get<Case>(`/cases/${caseId}`),
  });
  const { data: folders = [] } = useQuery({
    queryKey: ["folders", caseId],
    queryFn: () => api.get<FolderItem[]>(`/cases/${caseId}/folders`),
  });
  // Estados en los que el worker está procesando (OCR/ASR): la lista se auto-refresca.
  const ACTIVE_STATUSES = ["UPLOADED", "OCR_RUNNING", "ASR_RUNNING"];
  const { data: filesData, isLoading: loadingFiles } = useQuery({
    queryKey: ["files", caseId, currentFolder],
    queryFn: () => api.get<{ items: FileItem[] }>(`/cases/${caseId}/files`, currentFolder ? { folder_id: currentFolder } : {}),
    refetchInterval: (query) =>
      (query.state.data?.items ?? []).some((i) => ACTIVE_STATUSES.includes(i.processing_status ?? "")) ? 4000 : false,
  });
  const items = filesData?.items ?? [];

  // Subcarpetas de la carpeta actual y ruta de migas de pan.
  const subfolders = useMemo(
    () => folders.filter((f) => (f.parent_id ?? null) === currentFolder),
    [folders, currentFolder],
  );
  const breadcrumb = useMemo(() => {
    const byId = new Map(folders.map((f) => [f.id, f]));
    const path: FolderItem[] = [];
    let cur = currentFolder;
    while (cur) {
      const f = byId.get(cur);
      if (!f) break;
      path.unshift(f);
      cur = f.parent_id;
    }
    return path;
  }, [folders, currentFolder]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["folders", caseId] });
    qc.invalidateQueries({ queryKey: ["files", caseId] });
  };

  const saveFolder = useMutation({
    mutationFn: () =>
      folderDialog === "rename" && selectedFolder
        ? api.patch(`/cases/${caseId}/folders/${selectedFolder.id}`, { name: folderName.trim() })
        : api.post(`/cases/${caseId}/folders`, { name: folderName.trim(), parent_id: currentFolder }),
    onSuccess: () => { invalidate(); setFolderDialog(null); setFolderName(""); toast.success("Carpeta guardada"); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error"),
  });

  const deleteFolder = useMutation({
    mutationFn: (id: string) => api.delete(`/cases/${caseId}/folders/${id}`),
    onSuccess: () => { invalidate(); toast.success("Carpeta eliminada"); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error"),
  });

  async function sha256Hex(file: File): Promise<string> {
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  /** Sube un archivo grande directo al storage (GCS/S3) y lo registra. null si storage local. */
  async function uploadDirect(file: File, ocrMode: string, folderId: string | null): Promise<UploadResult | null> {
    const sha256 = await sha256Hex(file);
    const contentType = file.type || "application/octet-stream";
    const pre = await api.post<{ mode: string; upload_url?: string }>(`/cases/${caseId}/uploads/presign`,
      { filename: file.name, sha256, size_bytes: file.size, content_type: contentType });
    if (pre.mode !== "direct" || !pre.upload_url) return null;
    const put = await fetch(pre.upload_url, { method: "PUT", headers: { "Content-Type": contentType }, body: file });
    if (!put.ok) throw new Error(`No se pudo subir ${file.name} al storage (${put.status})`);
    return api.post<UploadResult>(`/cases/${caseId}/uploads/complete`, {
      filename: file.name, sha256, size_bytes: file.size, mime_type: contentType,
      folder_id: folderId || null, ocr_mode: ocrMode && ocrMode !== "none" ? ocrMode : null,
    });
  }

  const upload = useMutation({
    mutationFn: async ({ files, ocrMode }: { files: File[]; ocrMode: string }) => {
      // Archivos > 90 MB: subida DIRECTA al storage por URL prefirmada (evita el límite
      // de 100 MB de Cloudflare). Si el storage es local, cae a la subida normal.
      const direct: File[] = [];
      const normal: File[] = [];
      for (const f of files) (f.size > 90 * 1024 * 1024 ? direct : normal).push(f);
      const results: UploadResult[] = [];
      for (const f of direct) {
        try {
          const r = await uploadDirect(f, ocrMode, currentFolder);
          if (r) results.push(r); else normal.push(f);  // storage local -> multipart
        } catch (e) {
          results.push({ filename: f.name, status: "error", code: e instanceof Error ? e.message : "UPLOAD_ERROR" });
        }
      }
      if (normal.length) {
        const form = new FormData();
        for (const f of normal) form.append("uploads", f);
        if (currentFolder) form.append("folder_id", currentFolder);
        if (ocrMode && ocrMode !== "none") form.append("ocr_mode", ocrMode);
        const res = await api.upload<{ results: UploadResult[] }>(`/cases/${caseId}/files`, form);
        results.push(...res.results);
      }
      return { results };
    },
    onSuccess: (r) => {
      invalidate();
      const ok = r.results.filter((x) => x.status === "uploaded").length;
      const queued = r.results.filter((x) => x.processing === "QUEUED").length;
      const noOcr = r.results.filter((x) => x.status === "uploaded" && !x.processing).length;
      const failed = r.results.filter((x) => x.status === "error");
      if (ok) toast.success(`${ok} archivo(s) subido(s)`);
      if (queued) toast.info(`${queued} archivo(s) en procesamiento: OCR (PDF), transcripción (video) y análisis de índice (Excel). El estado se actualiza solo.`);
      if (noOcr) toast.info(`${noOcr} archivo(s) subidos sin OCR. Usa "Reprocesar" cuando quieras extraer texto.`);
      for (const f of failed) toast.error(`${f.filename}: ${f.code ?? "error"}`);
      // Los análisis de índice (AnalisisIA_*.md) aparecen al terminar el job: refresco diferido.
      if (queued) {
        setTimeout(invalidate, 15000);
        setTimeout(invalidate, 45000);
      }
      setOcrDialog({ files: null, mode: "none" });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error al subir"),
  });

  function handleFileSelect(files: FileList) {
    // Copiamos a un array ANTES de resetear el input: el FileList se vacía al
    // hacer `e.target.value = ""`, así que no se puede conservar la referencia.
    const picked = Array.from(files);
    // Detectar si hay archivos que necesitan OCR (PDF, imágenes, videos).
    const needsOcr = picked.some((f) => {
      const ext = f.name.split(".").pop()?.toLowerCase() ?? "";
      return ["pdf", "png", "jpg", "jpeg", "tiff", "mp4", "wav", "mp3", "webm"].includes(ext);
    });
    if (needsOcr) {
      setOcrDialog({ files: picked, mode: "none" });
    } else {
      upload.mutate({ files: picked, ocrMode: "none" });
    }
  }

  function confirmUpload(mode: string) {
    if (ocrDialog.files) {
      upload.mutate({ files: ocrDialog.files, ocrMode: mode });
    }
  }

  const renameFile = useMutation({
    mutationFn: (args: { id: string; filename: string }) =>
      api.patch(`/cases/${caseId}/files/${args.id}`, { filename: args.filename }),
    onSuccess: () => { invalidate(); toast.success("Archivo renombrado"); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error"),
  });

  const deleteFile = useMutation({
    mutationFn: (id: string) => api.delete(`/cases/${caseId}/files/${id}`),
    onSuccess: () => { invalidate(); toast.success("Archivo eliminado"); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error"),
  });

  // PDFs: la evidencia no se borra directamente; se registra una solicitud de
  // eliminación (bloqueada si el caso tiene medida de conservación / legal hold).
  const requestDocDeletion = useMutation({
    mutationFn: (args: { id: string; reason: string }) =>
      api.post(`/cases/${caseId}/documents/${args.id}/deletion-request`, { reason: args.reason }),
    onSuccess: () => { invalidate(); toast.success("Solicitud de eliminación registrada"); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo solicitar la eliminación"),
  });

  async function download(item: FileItem) {
    const path =
      item.kind === "file" ? `/cases/${caseId}/files/${item.id}/download`
      : item.kind === "document" ? `/cases/${caseId}/documents/${item.id}/download`
      : `/cases/${caseId}/media/${item.id}/download`;
    try {
      const blob = await api.blob(path);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = item.filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("No se pudo descargar el archivo");
    }
  }

  function openRenameFolder(f: FolderItem) {
    setSelectedFolder(f);
    setFolderName(f.name);
    setFolderDialog("rename");
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Button variant="outline" size="icon" onClick={() => router.push("/dashboard/procesos")} title="Volver a procesos">
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <h1 className="text-2xl font-bold tracking-tight">{proc?.title ?? "Proceso"}</h1>
            <p className="font-mono text-xs text-muted-foreground">{proc?.case_number}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <PartiesButton caseId={caseId} />
          <Button variant="outline" onClick={() => openChat(caseId)}>
            <MessageSquare className="mr-2 h-4 w-4" />Chat del proceso
          </Button>
          <Button variant="outline" onClick={downloadCkp} disabled={exporting}>
            <Download className="mr-2 h-4 w-4" />{exporting ? "Exportando…" : "Exportar CKP"}
          </Button>
          <Button variant="outline" onClick={persistCkp} disabled={savingCkp}>
            <Database className="mr-2 h-4 w-4" />{savingCkp ? "Guardando…" : "Guardar CKP"}
          </Button>
          <input
            ref={fileInput} type="file" multiple accept={ACCEPT} className="hidden"
            onChange={(e) => { if (e.target.files?.length) handleFileSelect(e.target.files); e.target.value = ""; }}
          />
          <Button variant="outline" onClick={() => { setSelectedFolder(null); setFolderName(""); setFolderDialog("create"); }}>
            <FolderPlus className="mr-2 h-4 w-4" />Nueva carpeta
          </Button>
          <Button onClick={() => fileInput.current?.click()} disabled={upload.isPending}>
            <Upload className="mr-2 h-4 w-4" />{upload.isPending ? "Subiendo…" : "Subir archivos"}
          </Button>
        </div>
      </div>

      <nav className="flex items-center gap-1 text-sm text-muted-foreground">
        <button className="hover:text-foreground" onClick={() => setCurrentFolder(null)}>Raíz del proceso</button>
        {breadcrumb.map((f) => (
          <span key={f.id} className="flex items-center gap-1">
            <ChevronRight className="h-3.5 w-3.5" />
            <button className="hover:text-foreground" onClick={() => setCurrentFolder(f.id)}>{f.name}</button>
          </span>
        ))}
      </nav>

      {subfolders.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {subfolders.map((f) => (
            <Card key={f.id} className="group transition-colors hover:border-primary/60">
              <CardContent className="flex items-center justify-between gap-2 p-4">
                <button type="button" className="flex min-w-0 flex-1 items-center gap-3 text-left"
                  onClick={() => setCurrentFolder(f.id)}>
                  <FolderOpen className="h-8 w-8 shrink-0 text-primary" />
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{f.name}</span>
                    <span className="block text-xs text-muted-foreground">
                      {f.subfolders} carpeta(s) · {f.files} archivo(s)
                    </span>
                  </span>
                </button>
                <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                  <Button variant="ghost" size="icon" title="Renombrar" onClick={() => openRenameFolder(f)}>
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button variant="ghost" size="icon" title="Eliminar"
                    onClick={() => {
                      if (window.confirm(`¿Eliminar la carpeta "${f.name}"? Sólo puede eliminarse si está vacía.`))
                        deleteFolder.mutate(f.id);
                    }}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Card>
        <CardContent className="p-4">
          {loadingFiles ? (
            <p className="text-muted-foreground">Cargando…</p>
          ) : items.length === 0 && subfolders.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12">
              <Folder className="mb-4 h-12 w-12 text-muted-foreground" />
              <p className="text-muted-foreground">Carpeta vacía. Sube archivos o crea una subcarpeta.</p>
            </div>
          ) : items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No hay archivos en esta carpeta.</p>
          ) : (
            <div className="space-y-2">
              {items.map((it) => {
                const Icon = fileIcon(it.mime_type);
                return (
                  <div key={`${it.kind}-${it.id}`} className="flex items-center justify-between rounded-md border p-3 hover:bg-muted/50">
                    <div className="flex min-w-0 items-center gap-3">
                      <Icon className="h-5 w-5 shrink-0 text-muted-foreground" />
                      <div className="min-w-0">
                        <p className="truncate font-medium">{it.title || it.filename}</p>
                        <p className="text-sm text-muted-foreground">
                          {fmtSize(it.size_bytes)}
                          {it.page_count ? ` · ${it.page_count} págs` : ""}
                          {it.processing_status ? ` · ${it.processing_status}` : ""}
                        </p>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <Badge variant="outline">{typeLabel(it.mime_type)}</Badge>
                      {it.kind === "file" && (
                        <>
                          <Button variant="ghost" size="icon" title="Renombrar"
                            onClick={() => {
                              const name = window.prompt("Nuevo nombre del archivo", it.filename);
                              if (name && name.trim() && name.trim() !== it.filename)
                                renameFile.mutate({ id: it.id, filename: name.trim() });
                            }}>
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button variant="ghost" size="icon" title="Eliminar"
                            onClick={() => {
                              if (window.confirm(`¿Eliminar "${it.filename}"?`)) deleteFile.mutate(it.id);
                            }}>
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </>
                      )}
                      {it.kind === "document" && (
                        <>
                          <Button variant="ghost" size="icon" title="Ver OCR"
                            onClick={() => setViewerDoc(it)}>
                            <Eye className="h-4 w-4" />
                          </Button>
                          <Button variant="ghost" size="icon" title="Solicitar eliminación"
                            onClick={() => {
                              const reason = window.prompt(
                                `Motivo de la solicitud de eliminación de "${it.filename}"\n` +
                                "(la evidencia no se borra directamente; queda marcada para eliminación):");
                              if (reason && reason.trim())
                                requestDocDeletion.mutate({ id: it.id, reason: reason.trim() });
                            }}>
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </>
                      )}
                      {it.kind === "media" && (
                        <Button variant="ghost" size="icon" title="Ver transcripción"
                          onClick={() => setViewerMedia(it)}>
                          <Eye className="h-4 w-4" />
                        </Button>
                      )}
                      <Button variant="outline" size="sm" onClick={() => download(it)}>
                        <Download className="mr-2 h-4 w-4" />Descargar
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={folderDialog !== null} onOpenChange={(o) => { if (!o) setFolderDialog(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{folderDialog === "rename" ? "Renombrar carpeta" : "Nueva carpeta"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={(e) => { e.preventDefault(); saveFolder.mutate(); }} className="space-y-4">
            <div>
              <label className="text-sm font-medium">Nombre</label>
              <Input value={folderName} onChange={(e) => setFolderName(e.target.value)} required
                placeholder="01PrimeraInstancia" />
            </div>
            <Button type="submit" className="w-full" disabled={saveFolder.isPending || !folderName.trim()}>
              {saveFolder.isPending ? "Guardando…" : "Guardar"}
            </Button>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={ocrDialog.files !== null} onOpenChange={(o) => { if (!o) setOcrDialog({ files: null, mode: "none" }); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Procesar con OCR</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Los archivos seleccionados incluyen PDFs, imágenes o videos que pueden procesarse con OCR para extraer texto.
              Elige el método de procesamiento:
            </p>
            <div className="space-y-2">
              <label className="flex items-start gap-3 rounded-lg border p-3 cursor-pointer hover:bg-muted/50">
                <input
                  type="radio"
                  name="ocr_mode"
                  value="none"
                  checked={ocrDialog.mode === "none"}
                  onChange={() => setOcrDialog((d) => ({ ...d, mode: "none" }))}
                  className="mt-1"
                />
                <div>
                  <p className="font-medium">Sin OCR</p>
                  <p className="text-sm text-muted-foreground">
                    Solo subir los archivos. Podrás procesarlos después con &quot;Reprocesar&quot;.
                  </p>
                </div>
              </label>
              <label className="flex items-start gap-3 rounded-lg border p-3 cursor-pointer hover:bg-muted/50">
                <input
                  type="radio"
                  name="ocr_mode"
                  value="basico"
                  checked={ocrDialog.mode === "basico"}
                  onChange={() => setOcrDialog((d) => ({ ...d, mode: "basico" }))}
                  className="mt-1"
                />
                <div>
                  <p className="font-medium">OCR Básico</p>
                  <p className="text-sm text-muted-foreground">
                    Procesamiento local con Tesseract/Docling. Rápido y privado, pero puede tener menor precisión en manuscritos.
                  </p>
                </div>
              </label>
              <label className="flex items-start gap-3 rounded-lg border p-3 cursor-pointer hover:bg-muted/50">
                <input
                  type="radio"
                  name="ocr_mode"
                  value="document_ai"
                  checked={ocrDialog.mode === "document_ai"}
                  onChange={() => setOcrDialog((d) => ({ ...d, mode: "document_ai" }))}
                  className="mt-1"
                />
                <div>
                  <p className="font-medium">OCR Document AI (Google Cloud)</p>
                  <p className="text-sm text-muted-foreground">
                    Procesamiento en la nube con Google Document AI. Mayor precisión, especialmente en formularios y manuscritos. Requiere credenciales configuradas.
                  </p>
                </div>
              </label>
            </div>
            <div className="flex gap-2 justify-end">
              <Button variant="outline" onClick={() => setOcrDialog({ files: null, mode: "none" })}>
                Cancelar
              </Button>
              <Button onClick={() => confirmUpload(ocrDialog.mode)} disabled={upload.isPending}>
                {upload.isPending ? "Subiendo…" : "Subir archivos"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <DocumentOcrViewer
        caseId={caseId}
        documentId={viewerDoc?.id ?? null}
        filename={viewerDoc?.title || viewerDoc?.filename}
        onClose={() => setViewerDoc(null)}
      />

      <MediaTranscriptViewer
        caseId={caseId}
        mediaId={viewerMedia?.id ?? null}
        filename={viewerMedia?.title || viewerMedia?.filename}
        onClose={() => setViewerMedia(null)}
      />
    </div>
  );
}
