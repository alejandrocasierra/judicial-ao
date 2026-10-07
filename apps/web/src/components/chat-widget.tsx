"use client";

/** Chat IA multi-turn del expediente.
 * - "@" crea chips removibles con el ID real del archivo (no texto plano).
 * - Citas clicables: "doc p.12" abre el visor OCR en esa página; "video 14:32"
 *   abre el video en ese segundo (y desde ahí se corrige OCR/ASR).
 * - File cards con botones Ver/Descargar cuando el agente trae un archivo.
 * - Sesiones persistentes (backend Fase 3): historial sobrevive recargas.
 * - Respeta el proceso activo en pantalla (/dashboard/procesos/[caseId]). */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import {
  MessageSquare, X, Send, Loader2, Bot, FileText, Video, Cpu, Plus,
  Download, Eye, Archive, AlertTriangle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { useChatStore } from "@/lib/chat-store";
import { DocumentOcrViewer } from "@/components/document-ocr-viewer";
import { MediaTranscriptViewer } from "@/components/media-transcript-viewer";
import { Markdown } from "@/components/markdown";
import { toast } from "sonner";

interface Agent { id: string; name: string; is_system: boolean }
interface Model { id: string; provider: string; model_name: string; is_default: boolean }
interface FileItem { id: string; name: string; kind: "pdf" | "video" }
interface Attachment { kind: "document" | "media"; id: string; name?: string }
interface Citation {
  citation_id: string; source_type: string; filename?: string; folio?: string;
  document_id?: string; page?: number; media_id?: string; start_ms?: number; end_ms?: number; speaker?: string;
}interface FileCard {
  kind: string; name: string; document_id?: string | null; media_id?: string | null;
  mime_type?: string; size_bytes?: number; page_count?: number | null; duration_ms?: number | null;
  download_path: string; view_path: string;
}
interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  attachments?: Attachment[];
  citations?: Citation[];
  occurrences?: Citation[];
  file_cards?: FileCard[];
  uncertainties?: string[];
}
interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
  attachments?: unknown[] | null;
  citations?: Citation[] | null;
  occurrences?: Citation[] | null;
}
interface Session {
  id: string; title: string; agent_id: string | null; model_id: string | null;
  message_count: number; updated_at: string;
}
interface SendResponse {
  answer: string; citations?: Citation[]; occurrences?: Citation[]; file_cards?: FileCard[]; uncertainties?: string[];
  pending_correction?: { tool: string; summary: string; requires_confirmation?: boolean } | null;
  user_message: { id: string }; assistant_message: { id: string };
}

function mmss(ms: number) {
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
    : `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

function formatSize(bytes?: number) {
  if (!bytes) return "";
  if (bytes > 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

/** El chat vive DENTRO de un proceso: sin expediente abierto no hay botón flotante.
 * Queda anclado al expediente de la URL, así que nunca responde sobre otro proceso. */
export function ChatWidget() {
  const pathname = usePathname();
  const activeProcessId = useMemo(() => {
    const m = pathname?.match(/\/dashboard\/procesos\/([0-9a-f-]{36})/i);
    return m?.[1] ?? null;
  }, [pathname]);
  // Fuera de un proceso (dashboard, usuarios, agentes…) no se muestra el chat.
  if (!activeProcessId) return null;
  // `key` remonta el chat al cambiar de expediente: nunca arrastra estado del anterior.
  return <ChatWidgetInner key={activeProcessId} caseId={activeProcessId} />;
}

function ChatWidgetInner({ caseId }: { caseId: string }) {
  const { open, setOpen, sessionId, setSession } = useChatStore();

  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [models, setModels] = useState<Model[]>([]);
  const [selectedModel, setSelectedModel] = useState<Model | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [menu, setMenu] = useState<{ type: "agent" | "file"; filter: string } | null>(null);
  const [docView, setDocView] = useState<{ id: string; page: number; name?: string } | null>(null);
  const [mediaView, setMediaView] = useState<{ id: string; ms: number; name?: string } | null>(null);
  const [pendingCorrection, setPendingCorrection] = useState<{ tool: string; summary: string } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const sendingRef = useRef(false);

  // Carga inicial: agentes y modelos (el expediente está fijado a la URL).
  useEffect(() => {
    if (!open) return;
    (async () => {
      try { setAgents(await api.get<Agent[]>("/agents")); } catch { setAgents([]); }
      try {
        const ms = await api.get<Model[]>("/models");
        setModels(ms);
        setSelectedModel((prev) => prev || ms.find((m) => m.is_default) || ms[0] || null);
      } catch { setModels([]); }
    })();
  }, [open]);

  // Al cambiar de expediente: sesiones y archivos (para el "@").
  useEffect(() => {
    if (!open || !caseId) return;
    (async () => {
      try {
        const ss = await api.get<Session[]>(`/cases/${caseId}/chats`);
        setSessions(ss);
        if (!sessionId || !ss.some((s) => s.id === sessionId)) {
          setSession(ss[0]?.id ?? null);
        }
      } catch { setSessions([]); }
      try {
        const [docs, media] = await Promise.all([
          api.get<{ id: string; filename: string; title?: string }[]>(`/cases/${caseId}/documents`),
          api.get<{ id: string; filename: string; title?: string }[]>(`/cases/${caseId}/media`),
        ]);
        setFiles([
          ...docs.map((d) => ({ id: d.id, name: d.title || d.filename, kind: "pdf" as const })),
          ...media.map((m) => ({ id: m.id, name: m.title || m.filename, kind: "video" as const })),
        ]);
      } catch { setFiles([]); }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, caseId]);

  // Al cambiar de sesión: cargar su historial persistido (salvo mientras se envía).
  useEffect(() => {
    if (!open || !caseId) return;
    let cancelled = false;
    (async () => {
      if (sendingRef.current) return;          // no pisar el mensaje que se está enviando
      if (!sessionId) {
        if (!cancelled) setMessages([]);
        return;
      }
      try {
        const r = await api.get<{ messages: HistoryMessage[] }>(
          `/cases/${caseId}/chats/${sessionId}/messages`, { limit: "100" });
        if (cancelled) return;
        setMessages(r.messages.map((m) => m.role === "assistant"
          ? { role: "assistant" as const, content: m.content, citations: m.citations || undefined,
              occurrences: m.occurrences || undefined,
              file_cards: (m.attachments as unknown as FileCard[]) || undefined }
          : { role: "user" as const, content: m.content,
              attachments: (m.attachments as unknown as Attachment[]) || undefined }));
      } catch { if (!cancelled) setMessages([]); }
    })();
    return () => { cancelled = true; };
  }, [open, caseId, sessionId]);

  // Auto-scroll al último mensaje.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  const agentOptions = useMemo(() => {
    const f = (menu?.type === "agent" ? menu.filter : "").toLowerCase();
    return agents.filter((a) => a.name.toLowerCase().includes(f)).slice(0, 8);
  }, [agents, menu]);

  const fileOptions = useMemo(() => {
    const f = (menu?.type === "file" ? menu.filter : "").toLowerCase();
    return files.filter((x) => x.name.toLowerCase().includes(f)).slice(0, 8);
  }, [files, menu]);

  function handleChange(value: string) {
    setInput(value);
    if (value.startsWith("/")) {
      setMenu({ type: "agent", filter: value.slice(1) });
      return;
    }
    const at = value.lastIndexOf("@");
    if (at >= 0 && !/\s/.test(value.slice(at + 1))) {
      setMenu({ type: "file", filter: value.slice(at + 1) });
      return;
    }
    setMenu(null);
  }

  function pickAgent(a: Agent) {
    setSelectedAgent(a);
    setInput("");
    setMenu(null);
    inputRef.current?.focus();
  }

  function pickFile(f: FileItem) {
    // "@" crea un CHIP con el id real (nunca texto plano en la pregunta).
    setAttachments((prev) => prev.some((a) => a.id === f.id) ? prev
      : [...prev, { kind: f.kind === "pdf" ? "document" : "media", id: f.id, name: f.name }]);
    const at = input.lastIndexOf("@");
    setInput(at >= 0 ? input.slice(0, at) : input);
    setMenu(null);
    inputRef.current?.focus();
  }

  async function loadMessages(sid: string) {
    try {
      const r = await api.get<{ messages: HistoryMessage[] }>(
        `/cases/${caseId}/chats/${sid}/messages`, { limit: "100" });
      setMessages(r.messages.map((m) => m.role === "assistant"
        ? { role: "assistant" as const, content: m.content, citations: m.citations || undefined,
            occurrences: m.occurrences || undefined,
            file_cards: (m.attachments as unknown as FileCard[]) || undefined }
        : { role: "user" as const, content: m.content,
            attachments: (m.attachments as unknown as Attachment[]) || undefined }));
    } catch { /* se conserva lo que haya en pantalla */ }
  }

  async function send() {
    if ((!input.trim() && attachments.length === 0) || loading || !caseId) return;
    const q = input.trim() || `Analiza ${attachments.map((a) => "@" + a.name).join(", ")}`;
    setInput("");
    await sendText(q, attachments);
  }

  async function sendText(q: string, atts: Attachment[] = []) {
    if (loading || !caseId) return;
    sendingRef.current = true;
    setAttachments([]);
    setMenu(null);
    setPendingCorrection(null);
    setMessages((m) => [...m, { role: "user", content: q, attachments: atts }]);
    setLoading(true);
    try {
      let sid = sessionId;
      if (!sid) {
        const created = await api.post<Session>(`/cases/${caseId}/chats`, {
          agent_id: selectedAgent?.id ?? null,
          model_id: selectedModel?.id ?? null,
        });
        sid = created.id;
        setSession(sid);
        setSessions((ss) => [{ ...created, message_count: created.message_count ?? 0,
                               updated_at: created.updated_at ?? new Date().toISOString() }, ...ss]);
      }
      const res = await api.post<SendResponse>(`/cases/${caseId}/chats/${sid}/messages`, {
        content: q,
        attachments: atts,
        strategy: "agent",
      });
      // El servidor es la fuente de verdad: recarga el historial (ambos mensajes)
      // en vez de añadirlos a mano (evita que la carga del historial los pise).
      await loadMessages(sid);
      if (res.occurrences && res.occurrences.length > 0) {
        const occ = res.occurrences;
        setMessages((m) => {
          const copy = [...m];
          for (let i = copy.length - 1; i >= 0; i--) {
            if (copy[i].role === "assistant") { copy[i] = { ...copy[i], occurrences: occ }; break; }
          }
          return copy;
        });
      }
      if (res.pending_correction) {
        setPendingCorrection({ tool: res.pending_correction.tool, summary: res.pending_correction.summary });
      }
      setSessions((ss) => ss.map((s) => s.id === sid
        ? { ...s, title: s.title || q.slice(0, 80), message_count: s.message_count + 2 } : s));
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Error al procesar la pregunta.";
      setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${msg}` }]);
      toast.error(msg);
    } finally {
      sendingRef.current = false;
      setLoading(false);
    }
  }

  function openCitation(c: Citation) {
    if (c.source_type === "document_page" && c.document_id) {
      setDocView({ id: c.document_id, page: c.page || 1, name: c.filename });
    } else if (c.source_type === "transcript_segment" && c.media_id) {
      setMediaView({ id: c.media_id, ms: c.start_ms || 0, name: c.filename });
    }
  }

  async function download(card: FileCard) {
    try {
      const blob = await api.blob(card.download_path);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = card.name || "archivo";
      a.click();
      URL.revokeObjectURL(url);
    } catch { toast.error("No se pudo descargar el archivo"); }
  }

  function viewCard(card: FileCard) {
    if (card.kind === "document" && card.document_id) setDocView({ id: card.document_id, page: 1, name: card.name });
    else if (card.media_id) setMediaView({ id: card.media_id, ms: 0, name: card.name });
  }

  const archiveSession = useCallback(async (sid: string) => {
    try {
      await api.delete(`/cases/${caseId}/chats/${sid}`);
      setSessions((ss) => ss.filter((s) => s.id !== sid));
      if (sid === sessionId) setSession(null);
      toast.success("Conversación archivada");
    } catch (e) { toast.error(e instanceof Error ? e.message : "No se pudo archivar"); }
  }, [caseId, sessionId, setSession]);

  return (
    <>
      <button
        onClick={() => setOpen(!open)}
        className={cn(
          "fixed bottom-4 right-4 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition-transform hover:scale-105 sm:bottom-6 sm:right-6 sm:h-14 sm:w-14",
          open && "rotate-90",
        )}
        aria-label="Chat IA"
      >
        {open ? <X className="h-6 w-6" /> : <MessageSquare className="h-6 w-6" />}
      </button>

      {open && (
        <div className="fixed inset-x-3 bottom-20 z-40 flex h-[min(560px,calc(100dvh-6rem))] flex-col rounded-lg border bg-background shadow-xl sm:inset-x-auto sm:bottom-24 sm:right-6 sm:h-[560px] sm:w-[600px]">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <h3 className="font-semibold">Chat IA</h3>
            <div className="flex items-center gap-1">
              <Button variant="ghost" size="icon" title="Nueva conversación" onClick={() => { setSession(null); setMessages([]); }}>
                <Plus className="h-4 w-4" />
              </Button>
              <Button variant="ghost" size="icon" onClick={() => setOpen(false)}><X className="h-4 w-4" /></Button>
            </div>
          </div>

          <div className="space-y-2 border-b px-4 py-2">
            <div className="flex gap-2">
              {sessions.length > 0 && (
                <Select value={sessionId ?? ""} onValueChange={(v) => {
                  if (v === "__new__") { setSession(null); setMessages([]); setPendingCorrection(null); }
                  else setSession(v || null);
                }}>
                  <SelectTrigger className="h-8 flex-1 text-xs">
                    <SelectValue placeholder="Nueva conversación" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__new__">
                      <span className="flex items-center gap-1 text-xs font-medium">
                        <Plus className="h-3.5 w-3.5" />Nueva conversación
                      </span>
                    </SelectItem>
                    {sessions.map((s) => (
                      <SelectItem key={s.id} value={s.id}>
                        <span className="text-xs">
                          {s.title ? (s.title.length > 36 ? s.title.slice(0, 36) + "…" : s.title) : "Conversación"} · {s.message_count} msg
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              {sessionId && (
                <Button variant="ghost" size="icon" className="h-8 w-8" title="Archivar conversación"
                  onClick={() => archiveSession(sessionId)}>
                  <Archive className="h-3.5 w-3.5" />
                </Button>
              )}
              {models.length > 0 && (
                <Select value={selectedModel?.id} onValueChange={(id) => setSelectedModel(models.find((m) => m.id === id) || null)}>
                  <SelectTrigger className="h-8 flex-1 text-xs">
                    <div className="flex items-center gap-2">
                      <Cpu className="h-3.5 w-3.5 text-muted-foreground" />
                      <SelectValue placeholder="Modelo" />
                    </div>
                  </SelectTrigger>
                  <SelectContent>
                    {models.map((m) => (
                      <SelectItem key={m.id} value={m.id}>
                        <div className="flex items-center gap-2">
                          <span>{m.model_name}</span>
                          <span className="text-xs text-muted-foreground">({m.provider}{m.is_default && " · default"})</span>
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
          </div>

          <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.length === 0 && (
              <p className="mt-8 text-center text-sm text-muted-foreground">
                Pregunta sobre el expediente. Escribe <b>/</b> para elegir un agente y <b>@</b> para adjuntar un archivo.
              </p>
            )}
            {messages.map((m, i) => (
              <div key={i} className={cn("rounded-lg px-3 py-2 text-sm", m.role === "user" ? "ml-8 bg-primary text-primary-foreground" : "mr-8 bg-muted")}>
                {m.attachments && m.attachments.length > 0 && m.role === "user" && (
                  <div className="mb-1 flex flex-wrap gap-1">
                    {m.attachments.map((a) => (
                      <span key={a.id} className="inline-flex items-center gap-1 rounded bg-background/30 px-1.5 py-0.5 text-xs">
                        {a.kind === "document" ? <FileText className="h-3 w-3" /> : <Video className="h-3 w-3" />}
                        {a.name}
                      </span>
                    ))}
                  </div>
                )}
                {m.role === "assistant" ? <Markdown text={m.content} /> : <p>{m.content}</p>}
                {m.uncertainties && m.uncertainties.length > 0 && (
                  <div className="mt-2 space-y-0.5">
                    {m.uncertainties.map((u, j) => (
                      <p key={j} className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
                        <AlertTriangle className="h-3 w-3" />{u}
                      </p>
                    ))}
                  </div>
                )}
                {m.citations && m.citations.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {m.citations.map((c) => (
                      <button key={c.citation_id} onClick={() => openCitation(c)}
                        title={c.source_type === "document_page" ? "Ver página (y corregir OCR)" : "Ver minuto (y corregir transcripción)"}
                        className="inline-flex items-center gap-1 rounded border bg-background/60 px-1.5 py-0.5 text-xs text-muted-foreground underline decoration-dotted hover:text-foreground">
                        {c.source_type === "document_page"
                          ? <><FileText className="h-3 w-3" />{c.filename} p.{c.page}</>
                          : <><Video className="h-3 w-3" />{c.filename} {c.start_ms != null ? mmss(c.start_ms) : ""}{c.speaker ? ` · ${c.speaker}` : ""}</>}
                      </button>
                    ))}
                  </div>
                )}
                {m.occurrences && m.occurrences.length > 0 && (
                  <div className="mt-2">
                    <p className="text-xs font-medium text-muted-foreground">
                      Aparece en {m.occurrences.length} ubicacion{m.occurrences.length === 1 ? "" : "es"}:
                    </p>
                    <div className="mt-1 max-h-40 space-y-0.5 overflow-y-auto pr-1">
                      {m.occurrences.map((c, k) => (
                        <button key={`${c.citation_id}-${k}`} onClick={() => openCitation(c)}
                          className="block w-full truncate rounded px-1 text-left text-xs text-muted-foreground underline decoration-dotted hover:text-foreground">
                          {c.source_type === "document_page"
                            ? `${c.filename} · página ${c.page}${c.folio ? ` (folio ${c.folio})` : ""}`
                            : `${c.filename} · ${c.start_ms != null ? mmss(c.start_ms) : ""}${c.speaker ? ` · ${c.speaker}` : ""}`}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {m.file_cards && m.file_cards.length > 0 && (
                  <div className="mt-2 space-y-1.5">
                    {m.file_cards.map((card, k) => (
                      <div key={k} className="flex items-center gap-2 rounded-md border bg-background/70 px-2 py-1.5">
                        {card.kind === "document" ? <FileText className="h-4 w-4 shrink-0 text-muted-foreground" /> : <Video className="h-4 w-4 shrink-0 text-muted-foreground" />}
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-medium">{card.name}</p>
                          <p className="text-xs text-muted-foreground">
                            {card.page_count ? `${card.page_count} págs · ` : ""}{card.duration_ms ? `${mmss(card.duration_ms)} · ` : ""}{formatSize(card.size_bytes)}
                          </p>
                        </div>
                        <Button variant="ghost" size="icon" className="h-7 w-7" title="Ver" onClick={() => viewCard(card)}>
                          <Eye className="h-3.5 w-3.5" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-7 w-7" title="Descargar" onClick={() => download(card)}>
                          <Download className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {loading && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />Buscando en el expediente…
              </div>
            )}
          </div>

          {menu && (menu.type === "agent" ? agentOptions.length : fileOptions.length) > 0 && (
            <div className="mx-3 mb-2 max-h-48 overflow-y-auto rounded-md border bg-popover shadow-md">
              {menu.type === "agent"
                ? agentOptions.map((a) => (
                    <button key={a.id} onClick={() => pickAgent(a)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-accent">
                      <Bot className="h-4 w-4 text-muted-foreground" />{a.name}
                    </button>
                  ))
                : fileOptions.map((f) => (
                    <button key={f.id} onClick={() => pickFile(f)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-accent">
                      {f.kind === "pdf" ? <FileText className="h-4 w-4 text-muted-foreground" /> : <Video className="h-4 w-4 text-muted-foreground" />}
                      <span className="truncate">{f.name}</span>
                    </button>
                  ))}
            </div>
          )}

          <div className="border-t p-3">
            {(selectedAgent || attachments.length > 0) && (
              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                {selectedAgent && (
                  <span className="inline-flex items-center gap-1 rounded-md border bg-muted/40 px-2 py-1 text-xs">
                    <Bot className="h-3.5 w-3.5" />{selectedAgent.name}
                    <button onClick={() => setSelectedAgent(null)} className="ml-1 text-muted-foreground hover:text-foreground"><X className="h-3 w-3" /></button>
                  </span>
                )}
                {attachments.map((a) => (
                  <span key={a.id} className="inline-flex items-center gap-1 rounded-md border bg-muted/40 px-2 py-1 text-xs">
                    {a.kind === "document" ? <FileText className="h-3.5 w-3.5" /> : <Video className="h-3.5 w-3.5" />}
                    <span className="max-w-40 truncate">{a.name}</span>
                    <button onClick={() => setAttachments((prev) => prev.filter((x) => x.id !== a.id))}
                      className="ml-1 text-muted-foreground hover:text-foreground"><X className="h-3 w-3" /></button>
                  </span>
                ))}
              </div>
            )}
            {pendingCorrection && (
              <div className="mb-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-2 text-xs">
                <p className="mb-1 font-medium">
                  Corrección propuesta {pendingCorrection.tool === "correct_transcript_segment" ? "(transcripción)"
                    : pendingCorrection.tool === "suggest_reprocess" ? "(mejora de calidad)" : "(OCR)"}
                </p>
                <p className="mb-2 line-clamp-4 whitespace-pre-line text-muted-foreground">{pendingCorrection.summary}</p>
                <div className="flex gap-2">
                  <Button size="sm" className="h-7" disabled={loading} onClick={() => sendText("sí, confirma")}>
                    Confirmar
                  </Button>
                  <Button size="sm" variant="outline" className="h-7" disabled={loading} onClick={() => sendText("no, cancela")}>
                    Cancelar
                  </Button>
                </div>
              </div>
            )}
            <form onSubmit={(e) => { e.preventDefault(); send(); }} className="flex gap-2">
              <Input
                ref={inputRef}
                value={input}
                onChange={(e) => handleChange(e.target.value)}
                placeholder="Escribe tu pregunta… (/ agentes, @ archivos)"
                className="flex-1"
              />
              <Button type="submit" size="icon" disabled={loading}><Send className="h-4 w-4" /></Button>
            </form>
            <p className="mt-2 text-xs text-muted-foreground">/ para agentes · @ para archivos (PDFs y videos) · las citas y archivos son clicables</p>
          </div>
        </div>
      )}

      {/* Visores con deep-link desde citas y file cards (incluyen corrección OCR/ASR). */}
      {caseId && docView && (
        <DocumentOcrViewer key={`${docView.id}-${docView.page}`} caseId={caseId} documentId={docView.id}
          filename={docView.name} initialPage={docView.page} onClose={() => setDocView(null)} />
      )}
      {caseId && mediaView && (
        <MediaTranscriptViewer key={`${mediaView.id}-${mediaView.ms}`} caseId={caseId} mediaId={mediaView.id}
          filename={mediaView.name} initialMs={mediaView.ms} onClose={() => setMediaView(null)} />
      )}
    </>
  );
}
