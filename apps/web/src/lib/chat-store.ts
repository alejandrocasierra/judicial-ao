"use client";

import { create } from "zustand";

/** Estado global del Chat IA: qué expediente y qué sesión está viendo el widget.
 * Lo usan el widget (layout), la página /dashboard/chats (continuar una sesión)
 * y la página del proceso (botón "Chat del proceso"). */
interface ChatState {
  open: boolean;
  caseId: string | null;
  sessionId: string | null;
  setOpen: (open: boolean) => void;
  openChat: (caseId?: string, sessionId?: string | null) => void;
  setSession: (sessionId: string | null) => void;
}

export const useChatStore = create<ChatState>()((set) => ({
  open: false,
  caseId: null,
  sessionId: null,
  setOpen: (open) => set({ open }),
  openChat: (caseId, sessionId = null) =>
    set((s) => ({ open: true, caseId: caseId ?? s.caseId, sessionId })),
  setSession: (sessionId) => set({ sessionId }),
}));
