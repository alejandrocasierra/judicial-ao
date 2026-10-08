"use client";

import { create } from "zustand";

/** Estado global de SUBIDAS en curso (sobrevive a la navegación entre páginas).
 * Lo escribe el gestor de subidas (`lib/uploader.ts`) y lo lee la vista
 * /dashboard/procesamiento para mostrar la subida aunque el usuario cambie de página. */
export interface UploadTask {
  id: string;
  caseId: string;
  filename: string;
  size: number;
  pct: number; // 0..100 (progreso de subida)
  stage: "uploading" | "processing" | "done" | "error";
  detail?: string;
}

interface UploadState {
  tasks: UploadTask[];
  add: (t: UploadTask) => void;
  update: (id: string, patch: Partial<UploadTask>) => void;
  remove: (id: string) => void;
}

export const useUploadStore = create<UploadState>()((set) => ({
  tasks: [],
  add: (t) => set((s) => ({ tasks: [...s.tasks, t] })),
  update: (id, patch) => set((s) => ({ tasks: s.tasks.map((x) => (x.id === id ? { ...x, ...patch } : x)) })),
  remove: (id) => set((s) => ({ tasks: s.tasks.filter((x) => x.id !== id) })),
}));
