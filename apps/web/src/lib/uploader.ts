"use client";

/** Gestor de SUBIDAS global: sobrevive a la navegación entre páginas.
 *
 * El archivo se sube UNA sola vez: navegador → API. El servidor guarda una copia local
 * (volumen compartido api<->worker) para hacer OCR/ASR sin re-descargar de GCS, y sube el
 * original al bucket para el enlace del modal. Así se evita la doble transferencia lenta.
 * Estado en `useUploadStore` (lo muestra /dashboard/procesamiento) y toasts de sonner. */

import { api, apiUploadXhr } from "@/lib/api";
import { queryClient } from "@/lib/query-client";
import { useUploadStore } from "@/lib/upload-store";
import { toast } from "sonner";

const POLL_MS = 15000;
const POLL_MAX = 240; // ~60 min
const TERMINAL = new Set(["ASR_COMPLETE", "OCR_COMPLETE", "INDEXED", "REVIEW_REQUIRED", "FAILED"]);

const VIDEO_EXTS = ["mp4", "wav", "mp3", "webm", "m4a", "mov"];
const DOC_EXTS = ["pdf", "png", "jpg", "jpeg", "tiff", "tif"];

interface UploadResult {
  filename: string; status: string; kind?: string; code?: string;
  processing?: string; ocr_mode?: string | null; id?: string; job_id?: string;
}

function sleep(ms: number) { return new Promise((r) => setTimeout(r, ms)); }

/** Etiqueta del procesamiento según el tipo de archivo. */
function procLabel(name: string): { short: string; done: string } {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (VIDEO_EXTS.includes(ext)) return { short: "ASR (transcripción)", done: "transcripción (ASR) completada" };
  if (DOC_EXTS.includes(ext)) return { short: "OCR", done: "OCR completado" };
  return { short: "procesamiento", done: "procesamiento completado" };
}

async function uploadOneFile(caseId: string, file: File, ocrMode: string, folderId: string | null,
                            onProgress: (p: number) => void): Promise<UploadResult> {
  const form = new FormData();
  form.append("uploads", file);
  if (folderId) form.append("folder_id", folderId);
  if (ocrMode && ocrMode !== "none") form.append("ocr_mode", ocrMode);
  const res = await apiUploadXhr<{ results: UploadResult[] }>(`/cases/${caseId}/files`, form, onProgress);
  return res.results[0];
}

async function pollProcessing(caseId: string, filename: string, taskId: string): Promise<void> {
  const st = useUploadStore.getState();
  for (let i = 0; i < POLL_MAX; i++) {
    await sleep(POLL_MS);
    try {
      const res = await api.get<{ jobs: Array<{ items: Array<{ filename: string; status: string }> }> }>(
        `/cases/${caseId}/processing/ocr-status`);
      const items = (res.jobs || []).flatMap((j) => j.items || []);
      const it = items.find((x) => x.filename === filename);
      if (it && TERMINAL.has(it.status)) {
        if (it.status === "FAILED") {
          st.update(taskId, { stage: "error", detail: "Falló el procesamiento" });
          toast.error(`${filename}: ${procLabel(filename).short} falló`);
        } else {
          st.update(taskId, { stage: "done", pct: 100, detail: "Procesado" });
          toast.success(`${filename}: ${procLabel(filename).done}`);
        }
        queryClient.invalidateQueries({ queryKey: ["files", caseId] });
        queryClient.invalidateQueries({ queryKey: ["processing"] });
        setTimeout(() => st.remove(taskId), 8000);
        return;
      }
      if (it) st.update(taskId, { stage: "processing", detail: it.status });
    } catch {
      // ignora errores de red del sondeo
    }
  }
  st.remove(taskId);
}

async function runOne(id: string, caseId: string, file: File, ocrMode: string, folderId: string | null): Promise<void> {
  const st = useUploadStore.getState();
  const toastId = `up-${id}`;
  let lastPct = -1;
  const onProgress = (p: number) => {
    const pct = Math.round(p * 100);
    st.update(id, { pct });
    if (pct !== lastPct) {
      lastPct = pct;
      toast.loading(`Subiendo ${file.name}… ${pct}%`, { id: toastId });
    }
  };
  toast.loading(`Subiendo ${file.name}…`, { id: toastId });
  try {
    const outcome = await uploadOneFile(caseId, file, ocrMode, folderId, onProgress);
    queryClient.invalidateQueries({ queryKey: ["folders", caseId] });
    queryClient.invalidateQueries({ queryKey: ["files", caseId] });
    queryClient.invalidateQueries({ queryKey: ["processing"] });
    if (outcome.processing === "QUEUED") {
      st.update(id, { stage: "processing", pct: 100, detail: "En cola de procesamiento" });
      toast.success(`${file.name}: subido. En cola de ${procLabel(file.name).short}…`, { id: toastId });
      void pollProcessing(caseId, file.name, id);
    } else {
      st.update(id, { stage: "done", pct: 100 });
      toast.success(`${file.name}: subido`, { id: toastId });
      setTimeout(() => st.remove(id), 6000);
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : "error al subir";
    st.update(id, { stage: "error", detail: msg });
    toast.error(`${file.name}: ${msg}`, { id: toastId });
  }
}

export function enqueueUploads(caseId: string, files: File[], ocrMode: string, folderId: string | null): void {
  for (const file of files) {
    const id = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
    useUploadStore.getState().add({ id, caseId, filename: file.name, size: file.size, pct: 0, stage: "uploading" });
    void runOne(id, caseId, file, ocrMode, folderId);
  }
}
