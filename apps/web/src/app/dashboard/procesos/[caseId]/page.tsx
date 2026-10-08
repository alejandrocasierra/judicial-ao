"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { enqueueUploads } from "@/lib/uploader";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DocumentOcrViewer } from "@/components/document-ocr-viewer";
import { MediaTranscriptViewer } from "@/components/media-transcript-viewer";
import { PartiesButton } from "@/components/parties-panel";
import {
  Folder, FolderOpen, FolderPlus, Upload, ChevronRight, ArrowLeft,
  FileText, FileSpreadsheet, FileImage, Video, File as FileIcon,
  Download, Pencil, Trash2, Eye, Plus, Database, Search, X,
} from "lucide-react";
import { toast } from "sonner";

interface Case { id: string; case_number: string; title: string; }
interface FolderItem { id: string; parent_id: string | null; name: string; subfolders: number; files: number; videos: number; }
interface FileItem {
  kind: "file" | "document" | "media";
  id: string; filename: string; title?: string | null; mime_type: string;
  size_bytes: number; page_count?: number | null; processing_status?: string | null; created_at: string;
}
interface SearchFolder { id: string; name: string; parent_id: string | null; path: string }
interface SearchItem extends FileItem { folder_id: string | null; folder_path: string }
interface SearchResults { query: string; folders: SearchFolder[]; items: SearchItem[] }
interface UploadResult { filename: string; status: string; kind?: string; code?: string; processing?: string; ocr_mode?: string | null; }

const ACCEPT = ".xlsx,.docx,.pdf,.jpg,.jpeg,.png,.svg,.mp4";

// Extensiones que requieren procesamiento: documentos -> OCR, videos/audio -> ASR.
const VIDEO_EXTS = ["mp4", "wav", "mp3", "webm", "m4a", "mov"];
const DOC_OCR_EXTS = ["pdf", "png", "jpg", "jpeg", "tiff", "tif"];
const PROCESS_EXTS = [...DOC_OCR_EXTS, ...VIDEO_EXTS];

function extOf(name: string) {
  return name.split(".").pop()?.toLowerCase() ?? "";
}

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
  const [currentFolder, setCurrentFolder] = useState<string | null>(null);
  const [folderDialog, setFolderDialog] = useState<"create" | "rename" | null>(null);
  const [folderName, setFolderName] = useState("");
  const [selectedFolder, setSelectedFolder] = useState<FolderItem | null>(null);
  const [viewerDoc, setViewerDoc] = useState<FileItem | null>(null);
  const [viewerMedia, setViewerMedia] = useState<FileItem | null>(null);
  const [ocrDialog, setOcrDialog] = useState<{ files: File[] | null; mode: string }>({ files: null, mode: "none" });
  const [deleteTarget, setDeleteTarget] = useState<{ kind: "document" | "media" | "file" | "folder"; id: string; filename: string } | null>(null);
  const [renamingFile, setRenamingFile] = useState<{ id: string; filename: string } | null>(null);
  const [renameValue, setRenameValue] = useState("");
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
      const r = await api.post<{ snapshot: string; files: number; bytes: number; prefix: string; uri?: string }>(
        `/cases/${caseId}/ckp/persist`);
      const mb = (r.bytes / 1048576).toFixed(1);
      const where = r.uri || r.prefix;
      toast.success(
        `CKP guardado en el storage (no se descarga): ${r.files} archivos · ${mb} MB`,
        { description: where, duration: 10000 });
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

  // Flags de la selección en el diálogo de subida: documentos (OCR) vs videos (ASR).
  const dialogFiles = ocrDialog.files ?? [];
  const dialogHasVideo = dialogFiles.some((f) => VIDEO_EXTS.includes(extOf(f.name)));
  const dialogHasDoc = dialogFiles.some((f) => DOC_OCR_EXTS.includes(extOf(f.name)));

  // Buscador del proceso: filtra carpetas, archivos, documentos y videos en TODAS
  // las subcarpetas (debounce 300 ms).
  const [searchQ, setSearchQ] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setSearchTerm(searchQ.trim()), 300);
    return () => clearTimeout(t);
  }, [searchQ]);
  const searchActive = searchTerm.length >= 2;
  const { data: searchData, isFetching: searching } = useQuery({
    queryKey: ["search", caseId, searchTerm],
    queryFn: () => api.get<SearchResults>(`/cases/${caseId}/search`, { q: searchTerm }),
    enabled: !!caseId && searchActive,
  });

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

  // La subida (directa con progreso + fallback) vive en el gestor global `lib/uploader.ts`.

  function handleFileSelect(files: FileList) {
    // Copiamos a un array ANTES de resetear el input: el FileList se vacía al
    // hacer `e.target.value = ""`, así que no se puede conservar la referencia.
    const picked = Array.from(files);
    // Archivos que necesitan procesamiento: documentos (OCR) o videos/audio (ASR).
    const exts = picked.map((f) => extOf(f.name));
    const hasVideo = exts.some((e) => VIDEO_EXTS.includes(e));
    const needsProcess = exts.some((e) => PROCESS_EXTS.includes(e));
    if (needsProcess) {
      // Si hay videos, por defecto se transcriben (ASR); si no, se pregunta.
      setOcrDialog({ files: picked, mode: hasVideo ? "basico" : "none" });
    } else {
      enqueueUploads(caseId, picked, "none", currentFolder);
    }
  }

  function confirmUpload(mode: string) {
    if (ocrDialog.files) {
      enqueueUploads(caseId, ocrDialog.files, mode, currentFolder);
      setOcrDialog({ files: null, mode: "none" });
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

  // PDFs y videos/audios: se eliminan INMEDIATAMENTE (BD + pgvector + grafo + storage).
  // Bloqueado solo si el proceso tiene medida de conservación (legal hold).
  const deleteDoc = useMutation({
    mutationFn: (id: string) => api.delete(`/cases/${caseId}/documents/${id}`),
    onSuccess: () => { invalidate(); toast.success("Documento eliminado"); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo eliminar"),
  });

  const deleteMedia = useMutation({
    mutationFn: (id: string) => api.delete(`/cases/${caseId}/media/${id}`),
    onSuccess: () => { invalidate(); toast.success("Video/audio eliminado"); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo eliminar"),
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
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Button variant="outline" size="icon" onClick={() => router.push("/dashboard/procesos")} title="Volver a procesos">
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <h1 className="text-2xl font-bold tracking-tight">{proc?.title ?? "Proceso"}</h1>
            <p className="font-mono text-xs text-muted-foreground">{proc?.case_number}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="icon" title="Más acciones">
                <Plus className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-56">
              <DropdownMenuLabel>Opciones del proceso</DropdownMenuLabel>
              <DropdownMenuItem onSelect={() => downloadCkp()} disabled={exporting}>
                <Download className="h-4 w-4" />
                {exporting ? "Exportando…" : "Exportar CKP"}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => persistCkp()} disabled={savingCkp}
                title="Guarda el paquete como archivos en el storage del servidor (no descarga nada)">
                <Database className="h-4 w-4" />
                {savingCkp ? "Guardando…" : "Guardar en storage"}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <PartiesButton caseId={caseId} />
          <input
            ref={fileInput} type="file" multiple accept={ACCEPT} className="hidden"
            onChange={(e) => { if (e.target.files?.length) handleFileSelect(e.target.files); e.target.value = ""; }}
          />
          <Button variant="outline" onClick={() => { setSelectedFolder(null); setFolderName(""); setFolderDialog("create"); }}>
            <FolderPlus className="mr-2 h-4 w-4" />Nueva carpeta
          </Button>
          <Button onClick={() => fileInput.current?.click()}>
            <Upload className="mr-2 h-4 w-4" />Subir archivos
          </Button>
        </div>
      </div>

      <nav className="flex flex-wrap items-center gap-1 text-sm text-muted-foreground">
        <button className="hover:text-foreground" onClick={() => setCurrentFolder(null)}>Raíz del proceso</button>
        {breadcrumb.map((f) => (
          <span key={f.id} className="flex items-center gap-1">
            <ChevronRight className="h-3.5 w-3.5" />
            <button className="hover:text-foreground" onClick={() => setCurrentFolder(f.id)}>{f.name}</button>
          </span>
        ))}
      </nav>

      <div className="relative max-w-md">
        <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input
          value={searchQ}
          onChange={(e) => setSearchQ(e.target.value)}
          placeholder="Buscar carpetas, archivos, documentos y videos…"
          className="pl-9 pr-9"
        />
        {searchQ && (
          <button type="button" onClick={() => setSearchQ("")} title="Limpiar búsqueda"
            className="absolute right-2 top-2 text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {searchActive ? (
        <Card>
          <CardContent className="space-y-4 p-4">
            {searching && !searchData ? (
              <p className="text-muted-foreground">Buscando…</p>
            ) : (
              <>
                {(searchData?.folders.length ?? 0) > 0 && (
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Carpetas ({searchData!.folders.length})
                    </p>
                    <div className="space-y-1">
                      {searchData!.folders.map((f) => (
                        <button key={f.id} type="button"
                          onClick={() => { setCurrentFolder(f.id); setSearchQ(""); }}
                          className="flex w-full items-center gap-2 rounded-md border p-2 text-left text-sm hover:bg-muted/50">
                          <FolderOpen className="h-4 w-4 shrink-0 text-primary" />
                          <span className="truncate font-medium">{f.name}</span>
                          <span className="ml-auto truncate text-xs text-muted-foreground">{f.path}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {(searchData?.items.length ?? 0) > 0 && (
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Archivos ({searchData!.items.length})
                    </p>
                    <div className="space-y-1">
                      {searchData!.items.map((it) => {
                        const Icon = fileIcon(it.mime_type);
                        return (
                          <div key={`${it.kind}-${it.id}`}
                            className="flex items-center gap-2 rounded-md border p-2 text-sm hover:bg-muted/50">
                            <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                            <Badge variant="outline" className="shrink-0">{typeLabel(it.mime_type)}</Badge>
                            <span className="truncate">{it.title || it.filename}</span>
                            <span className="ml-auto truncate text-xs text-muted-foreground">{it.folder_path}</span>
                            {it.kind === "document" && (
                              <Button variant="ghost" size="icon" title="Ver OCR" onClick={() => setViewerDoc(it)}>
                                <Eye className="h-4 w-4" />
                              </Button>
                            )}
                            {it.kind === "media" && (
                              <Button variant="ghost" size="icon" title="Ver transcripción" onClick={() => setViewerMedia(it)}>
                                <Eye className="h-4 w-4" />
                              </Button>
                            )}
                            <Button variant="ghost" size="icon" title="Descargar" onClick={() => download(it)}>
                              <Download className="h-4 w-4" />
                            </Button>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
                {searchData && searchData.folders.length === 0 && searchData.items.length === 0 && (
                  <p className="text-sm text-muted-foreground">Sin resultados para «{searchTerm}».</p>
                )}
              </>
            )}
          </CardContent>
        </Card>
      ) : (
        <>
      {subfolders.length > 0 && (
        <div className="grid min-w-0 gap-4 [&>*]:min-w-0 md:grid-cols-2 lg:grid-cols-3">
          {subfolders.map((f) => (
            <Card key={f.id} className="group transition-colors hover:border-primary/60">
              <CardContent className="flex flex-col gap-2 p-4 sm:flex-row sm:items-center sm:justify-between">
                <button type="button" className="flex min-w-0 flex-1 items-center gap-3 text-left"
                  onClick={() => setCurrentFolder(f.id)}>
                  <FolderOpen className="h-8 w-8 shrink-0 text-primary" />
                  <span className="min-w-0">
                    <span className="block break-words font-medium sm:truncate">{f.name}</span>
                    <span className="block text-xs text-muted-foreground">
                      {f.subfolders} carpeta(s) · {f.files} archivo(s) · {f.videos} video(s)
                    </span>
                  </span>
                </button>
                <div className="flex shrink-0 items-center gap-1 sm:opacity-0 sm:transition-opacity sm:group-hover:opacity-100">
                  <Button variant="ghost" size="icon" title="Renombrar" onClick={() => openRenameFolder(f)}>
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button variant="ghost" size="icon" title="Eliminar"
                    onClick={() => setDeleteTarget({ kind: "folder", id: f.id, filename: f.name })}>
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
                  <div key={`${it.kind}-${it.id}`} className="flex flex-col gap-3 rounded-md border p-3 hover:bg-muted/50 sm:flex-row sm:items-center sm:justify-between">
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
                    <div className="flex flex-wrap items-center gap-2 sm:shrink-0 sm:justify-end">
                      <Badge variant="outline">{typeLabel(it.mime_type)}</Badge>
                      {it.kind === "file" && (
                        <>
                          <Button variant="ghost" size="icon" title="Renombrar"
                            onClick={() => { setRenameValue(it.filename); setRenamingFile({ id: it.id, filename: it.filename }); }}>
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button variant="ghost" size="icon" title="Eliminar"
                            onClick={() => setDeleteTarget({ kind: "file", id: it.id, filename: it.filename })}>
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
                          <Button variant="ghost" size="icon" title="Eliminar documento"
                            onClick={() => setDeleteTarget({ kind: "document", id: it.id, filename: it.filename })}>
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </>
                      )}
                      {it.kind === "media" && (
                        <>
                          <Button variant="ghost" size="icon" title="Ver transcripción"
                            onClick={() => setViewerMedia(it)}>
                            <Eye className="h-4 w-4" />
                          </Button>
                          <Button variant="ghost" size="icon" title="Eliminar video/audio"
                            onClick={() => setDeleteTarget({ kind: "media", id: it.id, filename: it.filename })}>
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </>
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
        </>
      )}

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

      <Dialog open={deleteTarget !== null} onOpenChange={(o) => { if (!o) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {deleteTarget?.kind === "folder" ? "Eliminar carpeta"
                : deleteTarget?.kind === "file" ? "Eliminar archivo"
                : deleteTarget?.kind === "document" ? "Eliminar documento"
                : "Eliminar video/audio"}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            {deleteTarget?.kind === "folder" ? (
              <p className="text-sm text-muted-foreground">
                ¿Eliminar la carpeta <b className="break-all text-foreground">{deleteTarget.filename}</b>?
                Sólo puede eliminarse si está vacía.
              </p>
            ) : deleteTarget?.kind === "file" ? (
              <p className="text-sm text-muted-foreground">
                ¿Eliminar el archivo <b className="break-all text-foreground">{deleteTarget.filename}</b>?
                Esta acción no se puede deshacer.
              </p>
            ) : (
              <p className="text-sm text-muted-foreground">
                ¿Eliminar definitivamente <b className="break-all text-foreground">{deleteTarget?.filename}</b>?
                Se borrarán también {deleteTarget?.kind === "document"
                  ? "su texto OCR y sus páginas"
                  : "su transcripción, hablantes y segmentos"}, los fragmentos de búsqueda (embeddings)
                y su relación en el grafo, y el original se elimina del almacenamiento.{" "}
                <b className="text-foreground">Esta acción no se puede deshacer.</b>
              </p>
            )}
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="outline" onClick={() => setDeleteTarget(null)}>
                Cancelar
              </Button>
              {deleteTarget?.kind === "folder" ? (
                <Button variant="destructive" disabled={deleteFolder.isPending}
                  onClick={() => { if (deleteTarget) deleteFolder.mutate(deleteTarget.id); setDeleteTarget(null); }}>
                  {deleteFolder.isPending ? "Eliminando…" : "Eliminar carpeta"}
                </Button>
              ) : deleteTarget?.kind === "file" ? (
                <Button variant="destructive" disabled={deleteFile.isPending}
                  onClick={() => { if (deleteTarget) deleteFile.mutate(deleteTarget.id); setDeleteTarget(null); }}>
                  {deleteFile.isPending ? "Eliminando…" : "Eliminar archivo"}
                </Button>
              ) : deleteTarget?.kind === "document" ? (
                <Button variant="destructive" disabled={deleteDoc.isPending}
                  onClick={() => { if (deleteTarget) deleteDoc.mutate(deleteTarget.id); setDeleteTarget(null); }}>
                  {deleteDoc.isPending ? "Eliminando…" : "Eliminar documento"}
                </Button>
              ) : (
                <Button variant="destructive" disabled={deleteMedia.isPending}
                  onClick={() => { if (deleteTarget) deleteMedia.mutate(deleteTarget.id); setDeleteTarget(null); }}>
                  {deleteMedia.isPending ? "Eliminando…" : "Eliminar video/audio"}
                </Button>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={renamingFile !== null} onOpenChange={(o) => { if (!o) setRenamingFile(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Renombrar archivo</DialogTitle>
          </DialogHeader>
          <form className="space-y-4" onSubmit={(e) => {
            e.preventDefault();
            const name = renameValue.trim();
            if (renamingFile && name && name !== renamingFile.filename) renameFile.mutate({ id: renamingFile.id, filename: name });
            setRenamingFile(null);
          }}>
            <div className="space-y-1">
              <label className="text-sm font-medium">Nuevo nombre</label>
              <Input value={renameValue} onChange={(e) => setRenameValue(e.target.value)} autoFocus />
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setRenamingFile(null)}>Cancelar</Button>
              <Button type="submit" disabled={!renameValue.trim() || renameFile.isPending}>
                {renameFile.isPending ? "Guardando…" : "Guardar"}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={ocrDialog.files !== null} onOpenChange={(o) => { if (!o) setOcrDialog({ files: null, mode: "none" }); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {dialogHasVideo && !dialogHasDoc ? "Procesar video(s)"
                : dialogHasDoc && !dialogHasVideo ? "Procesar documento(s)"
                : "Procesar archivos"}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              {dialogHasVideo && (
                <>Los videos/audios se transcriben automáticamente con ASR (Whisper local), no con OCR. </>
              )}
              {dialogHasDoc && (
                <>Los PDFs e imágenes se procesan con OCR para extraer texto. </>
              )}
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
                  <p className="font-medium">Sin procesar</p>
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
                  <p className="font-medium">
                    {dialogHasVideo && !dialogHasDoc ? "Transcribir (ASR local con Whisper)"
                      : dialogHasDoc && dialogHasVideo ? "Procesar local (OCR + ASR)"
                      : "OCR Básico (Tesseract/Docling)"}
                  </p>
                  <p className="text-sm text-muted-foreground">
                    {dialogHasVideo && !dialogHasDoc
                      ? "Convierte el audio de los videos a texto con Whisper local. Rápido y privado."
                      : dialogHasDoc && dialogHasVideo
                        ? "OCR local para PDFs/imágenes y transcripción (ASR) de los videos, todo en el servidor."
                        : "Procesamiento local con Tesseract/Docling. Rápido y privado, pero puede tener menor precisión en manuscritos."}
                  </p>
                </div>
              </label>
              {dialogHasDoc && (
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
                      {dialogHasVideo && "Solo aplica a PDFs/imágenes; los videos se transcriben igual con ASR local. "}
                      Mayor precisión, especialmente en formularios y manuscritos. Requiere credenciales configuradas.
                    </p>
                  </div>
                </label>
              )}
            </div>
            <div className="flex gap-2 justify-end">
              <Button variant="outline" onClick={() => setOcrDialog({ files: null, mode: "none" })}>
                Cancelar
              </Button>
              <Button onClick={() => confirmUpload(ocrDialog.mode)}>
                Subir archivos
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
        processingStatus={viewerMedia?.processing_status ?? null}
        onClose={() => setViewerMedia(null)}
      />
    </div>
  );
}
