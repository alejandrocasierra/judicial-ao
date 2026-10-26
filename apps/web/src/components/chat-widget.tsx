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
  Download, Eye, Archive, AlertTriangle, Square, Copy, RotateCcw, RefreshCw,
  ThumbsUp, ThumbsDown, BrainCircuit, Check, ShieldCheck,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { useChatStore } from "@/lib/chat-store";
import { useAuth } from "@/lib/auth";
import { DocumentOcrViewer } from "@/components/document-ocr-viewer";
import { MediaTranscriptViewer } from "@/components/media-transcript-viewer";
import { Markdown } from "@/components/markdown";
import { AuditBadge } from "@/components/audit-badge";
import { toast } from "sonner";

interface Agent { id: string; name: string; is_system: boolean }
interface Model { id: string; provider: string; model_name: string; is_default: boolean }
interface FileItem { id: string; name: string; kind: "pdf" | "video" }
interface Attachment { kind: "document" | "media"; id: string; name?: string }
interface Citation {
  citation_id: string; source_type: string; filename?: string; folio?: string;
  document_id?: string; page?: number; media_id?: string; start_ms?: number; end_ms?: number; speaker?: string;
  ocr_mode?: string | null; asr_mode?: string | null;
  /** Número visible de la cita (`[n]`); si falta se usa el orden de la lista. */
  marker?: number | string;
  /** Clave de agrupación para ocurrencias; si falta se usa el archivo. */
  group?: string;
}
type AnswerKind = "direct" | "partial" | "insufficient" | "navigational" | "correction";
interface FileCard {
  kind: string; name: string; document_id?: string | null; media_id?: string | null;
  mime_type?: string; size_bytes?: number; page_count?: number | null; duration_ms?: number | null;
  download_path: string; view_path: string;
}
interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  /** Id persistido del mensaje (solo assistant): necesario para valorar el turno. */
  id?: string;
  attachments?: Attachment[];
  citations?: Citation[];
  occurrences?: Citation[];
  file_cards?: FileCard[];
  uncertainties?: string[];
  answer_kind?: AnswerKind;
  degraded?: boolean;
  /** Duración del turno recién generado (no se guarda en el historial). */
  elapsed_ms?: number;
  occurrences_note?: string;
  occurrences_term?: string;
  /** Marca el mensaje que todavía se está escribiendo por streaming. */
  streaming?: boolean;
  /** El usuario detuvo la generación antes del `done`. */
  stopped?: boolean;
  /** Mensaje de error del turno (no es una respuesta del asistente). */
  error?: boolean;
  /** Auditoría legal persistida para este turno. */
  audit_status?: string | null;
  audit_id?: string | null;
  /** La política exige segunda opinión antes de publicar. */
  publication_blocked?: boolean;
  publication_block_reason?: string | null;
}
interface HistoryMessage {
  role: "user" | "assistant";
  id?: string;
  content: string;
  attachments?: unknown[] | null;
  citations?: Citation[] | null;
  occurrences?: Citation[] | null;
  uncertainties?: string[] | null;
  answer_kind?: AnswerKind | null;
  degraded?: boolean | null;
  occurrences_note?: string | null;
  occurrences_term?: string | null;
  /** Veredicto ya guardado por este usuario (el botón recuerda la elección). */
  verdict?: Verdict | null;
  corrected_intent?: string | null;
  audit_status?: string | null;
  audit_id?: string | null;
  publication_blocked?: boolean | null;
  publication_block_reason?: string | null;
}
type Verdict = "accepted" | "rejected" | "wrong_intent";
interface FeedbackState { verdict: Verdict; corrected?: string }
/** Las 7 intenciones en lenguaje humano: lo que el usuario quería, no jerga. */
const INTENT_OPTIONS = [
  { value: "dato", label: "Un dato", hint: "Un hecho concreto del expediente" },
  { value: "analisis", label: "Un análisis", hint: "Comparar, contradicciones, implicaciones" },
  { value: "procedimental", label: "Cómo ocurrió", hint: "Antecedentes y cadena procesal" },
  { value: "agregacion", label: "Una lista", hint: "Quiénes intervinieron o cuántos son" },
  { value: "navegar", label: "Dónde aparece", hint: "Página, minuto o archivo de una mención" },
  { value: "lectura", label: "Leer o resumir", hint: "Un documento o un adjunto" },
  { value: "correccion", label: "Corregir", hint: "Un dato o un hablante ya dicho" },
] as const;
const INTENT_LABELS: Record<string, string> =
  Object.fromEntries(INTENT_OPTIONS.map((o) => [o.value, o.label]));
type RecognitionMode = "todos" | "basico" | "document_ai";

interface Session {
  id: string; title: string; agent_id: string | null; model_id: string | null;
  message_count: number; updated_at: string;
  ocr_mode: RecognitionMode | null; asr_mode: RecognitionMode | null;
}
interface SendResponse {
  answer: string; citations?: Citation[]; occurrences?: Citation[]; file_cards?: FileCard[]; uncertainties?: string[];
  answer_kind?: AnswerKind; intent?: string; degraded?: boolean;
  elapsed_ms?: number; timings?: Record<string, number>;
  occurrences_term?: string; occurrences_note?: string;
  pending_correction?: { tool: string; summary: string; requires_confirmation?: boolean } | null;
  audit_id?: string | null;
  audit_status?: string | null;
  publication_blocked?: boolean;
  publication_block_reason?: string | null;
  second_opinion?: { triggered: boolean; reason?: string; run_id?: string; job_id?: string } | null;
  user_message: { id: string }; assistant_message: { id: string };
}

/** Etapas del SSE. El backend puede mandar `label` ya localizado; si no, se usa
 * esta traducción. Sin porcentajes: no hay progreso medible que mostrar. */
const STAGE_LABELS: Record<string, string> = {
  accepted: "Preparando la consulta…",
  routing: "Analizando la pregunta y eligiendo agente…",
  retrieving: "Investigando el expediente…",
  tool: "Consultando el expediente…",
  reranking: "Ordenando la evidencia…",
  reviewing: "Revisando la evidencia…",
  synthesizing: "Redactando la respuesta…",
  validating: "Verificando citas, cifras y nombres…",
};
const STAGE_FALLBACK = "Analizando la pregunta…";

/** Cómo se nombra el resultado de la auditoría en el chat (mismo lenguaje que el badge). */
const AUDIT_LABELS: Record<string, string> = {
  PASSED: "verificada",
  WARNING: "advertencia",
  NEEDS_REVIEW: "revisión",
  BLOCKED: "bloqueada",
  ERROR: "error",
};

/** Una etapa visible del trabajo, no un porcentaje inventado. */
interface LiveStage {
  key: string;
  label: string;
  detail?: string;
  state: "active" | "done";
}

/** Recuerda en el navegador qué conversación del expediente se estaba viendo.
 *  El historial real vive en PostgreSQL; esto solo evita abrir otra al recargar. */
function lastSessionKey(caseId: string) {
  return `judicial:chat:last:${caseId}`;
}

function mmss(ms: number) {
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
    : `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

function durationLabel(ms: number) {
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  return mmss(ms);
}

function formatSize(bytes?: number) {
  if (!bytes) return "";
  if (bytes > 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

function modeBadge(c: Citation) {
  const mode = c.source_type === "document_page" ? c.ocr_mode : c.asr_mode;
  if (!mode || mode === "todos") return null;
  const label = mode === "basico" ? "básico" : "Document AI";
  const kind = c.source_type === "document_page" ? "OCR" : "ASR";
  return <span className="ml-1 rounded bg-muted px-1 text-[10px] text-muted-foreground">{kind} {label}</span>;
}

/** Texto visible de una cita: archivo + página o archivo + minuto + hablante. */
function citationLabel(c: Citation) {
  if (c.source_type === "document_page") {
    return `${c.filename || "Documento"} · p. ${c.page ?? 1}${c.folio ? ` (folio ${c.folio})` : ""}`;
  }
  const at = c.start_ms != null ? mmss(c.start_ms) : "";
  return `${c.filename || "Audio"}${at ? ` · ${at}` : ""}${c.speaker ? ` · ${c.speaker}` : ""}`;
}

/** Describe la acción del chip para lectores de pantalla y tooltip. */
function citationAction(c: Citation) {
  return c.source_type === "document_page"
    ? `Abrir ${c.filename || "el documento"} en la página ${c.page ?? 1}`
    : `Abrir ${c.filename || "el audio"} en ${c.start_ms != null ? mmss(c.start_ms) : "el inicio"}`;
}

function occurrenceLabel(c: Citation) {
  if (c.source_type === "document_page") {
    return `p. ${c.page ?? 1}${c.folio ? ` (folio ${c.folio})` : ""}`;
  }
  return `${c.start_ms != null ? mmss(c.start_ms) : "inicio"}${c.speaker ? ` · ${c.speaker}` : ""}`;
}

function CitationChip({ citation, marker, onOpen }: {
  citation: Citation; marker: number | string; onOpen: (c: Citation) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(citation)}
      title={citationAction(citation)}
      aria-label={citationAction(citation)}
      className="inline-flex max-w-full items-center gap-1 rounded border bg-background/60 px-1.5 py-0.5 text-xs text-muted-foreground underline decoration-dotted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
    >
      <span className="shrink-0 font-medium tabular-nums">[{marker}]</span>
      {citation.source_type === "document_page"
        ? <FileText className="h-3 w-3 shrink-0" aria-hidden="true" />
        : <Video className="h-3 w-3 shrink-0" aria-hidden="true" />}
      <span className="min-w-0 truncate">{citationLabel(citation)}</span>
      {modeBadge(citation)}
    </button>
  );
}

/** Con 1 o 2 citas van inline; con más, se pliegan para no tapar la respuesta. */
function CitationList({ citations, onOpen }: { citations: Citation[]; onOpen: (c: Citation) => void }) {
  const chips = citations.map((c, i) => (
    <CitationChip key={c.citation_id || `cite-${i}`} citation={c} marker={c.marker ?? i + 1} onOpen={onOpen} />
  ));
  if (citations.length <= 2) {
    return <div className="mt-2 flex flex-wrap gap-1">{chips}</div>;
  }
  return (
    <details className="mt-2">
      <summary className="cursor-pointer select-none text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background">
        Fuentes ({citations.length})
      </summary>
      <div className="mt-1 flex flex-wrap gap-1">{chips}</div>
    </details>
  );
}

interface OccurrenceGroup { key: string; label: string; items: Citation[] }

function groupOccurrences(items: Citation[]): OccurrenceGroup[] {
  const groups = new Map<string, OccurrenceGroup>();
  for (const c of items) {
    const label = c.group || c.filename || "Archivo";
    const key = c.group || c.filename || c.citation_id;
    const current = groups.get(key);
    if (current) current.items.push(c);
    else groups.set(key, { key, label, items: [c] });
  }
  return Array.from(groups.values());
}

/** Ubicaciones agrupadas por archivo. No se usa para `insufficient`. */
function Occurrences({ items, term, note, onOpen }: {
  items: Citation[]; term?: string; note?: string; onOpen: (c: Citation) => void;
}) {
  const groups = groupOccurrences(items);
  if (groups.length === 0) return null;
  const total = items.length;
  return (
    <div className="mt-2 rounded-md border bg-background/40 p-2">
      <p className="text-xs font-medium text-muted-foreground">
        {term ? `Menciones de «${term}»` : "Menciones"}{" "}
        <span className="font-normal">
          ({total} {total === 1 ? "ubicación" : "ubicaciones"})
        </span>
      </p>
      {note && <p className="mt-0.5 text-xs text-muted-foreground">{note}</p>}
      <ul className="mt-1 space-y-1.5">
        {groups.map((g) => (
          <li key={g.key} className="min-w-0">
            <p className="truncate text-xs font-medium">
              {g.label}{" "}
              <span className="font-normal text-muted-foreground">
                · {g.items.length} {g.items.length === 1 ? "mención" : "menciones"}
              </span>
            </p>
            <div className="mt-0.5 flex flex-wrap gap-1">
              {g.items.map((c, k) => (
                <button key={`${c.citation_id}-${k}`} type="button" onClick={() => onOpen(c)}
                  title={citationAction(c)} aria-label={citationAction(c)}
                  className="rounded px-1.5 py-0.5 text-xs text-muted-foreground underline decoration-dotted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background">
                  {occurrenceLabel(c)}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Evita repetir en la lista lo que el compositor ya escribió en el cuerpo. */
function visibleUncertainties(m: ChatMessage): string[] {
  if (!m.uncertainties || m.uncertainties.length === 0) return [];
  const body = m.content.replace(/\s+/g, " ").trim().toLowerCase();
  const seen = new Set<string>();
  return m.uncertainties.filter((u) => {
    const text = u.replace(/\s+/g, " ").trim();
    const key = text.toLowerCase();
    if (!key || seen.has(key) || body.includes(key)) return false;
    seen.add(key);
    return true;
  });
}

function mapHistory(m: HistoryMessage): ChatMessage {
  if (m.role === "assistant") {
    return {
      role: "assistant",
      id: m.id,
      content: m.content,
      citations: m.citations || undefined,
      occurrences: m.occurrences || undefined,
      uncertainties: m.uncertainties || undefined,
      file_cards: (m.attachments as unknown as FileCard[]) || undefined,
      answer_kind: m.answer_kind || undefined,
      degraded: m.degraded || undefined,
      occurrences_note: m.occurrences_note || undefined,
      occurrences_term: m.occurrences_term || undefined,
      audit_status: m.audit_status || undefined,
      audit_id: m.audit_id || undefined,
      publication_blocked: m.publication_blocked || undefined,
      publication_block_reason: m.publication_block_reason || undefined,
    };
  }
  return {
    role: "user",
    content: m.content,
    attachments: (m.attachments as unknown as Attachment[]) || undefined,
  };
}

/** El veredicto vive en el backend: al recargar, el botón recuerda la elección. */
function feedbackFromHistory(messages: HistoryMessage[]): Record<string, FeedbackState> {
  const out: Record<string, FeedbackState> = {};
  for (const m of messages) {
    if (m.role === "assistant" && m.id && m.verdict) {
      out[m.id] = { verdict: m.verdict, corrected: m.corrected_intent || undefined };
    }
  }
  return out;
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
  const [releasingId, setReleasingId] = useState<string | null>(null);
  const [auditingId, setAuditingId] = useState<string | null>(null);
  /** Ya llegaron tokens: el panel de etapas deja paso al texto para no duplicar el “trabajando”. */
  const [receiving, setReceiving] = useState(false);
  /** Segundos transcurridos del turno: el panel no parece colgado si el modelo tarda. */
  const [waitSec, setWaitSec] = useState(0);
  const canAudit = useAuth((s) => s.user?.permissions.includes("audit.run") ?? false);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [models, setModels] = useState<Model[]>([]);
  const [selectedModel, setSelectedModel] = useState<Model | null>(null);
  const [modeOverride, setModeOverride] = useState<{ ocr?: RecognitionMode; asr?: RecognitionMode } | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [menu, setMenu] = useState<{ type: "agent" | "file"; filter: string } | null>(null);
  const [docView, setDocView] = useState<{ id: string; page: number; name?: string } | null>(null);
  const [mediaView, setMediaView] = useState<{ id: string; ms: number; name?: string } | null>(null);
  const [pendingCorrection, setPendingCorrection] = useState<{ tool: string; summary: string } | null>(null);
  const [liveStages, setLiveStages] = useState<LiveStage[]>([]);
  const stageSeq = useRef(0);
  /** Veredicto local por mensaje (optimista); el backend lo persiste en intent_feedback. */
  const [feedback, setFeedback] = useState<Record<string, FeedbackState>>({});
  /** Mensaje cuyo «no era esto» está abierto para elegir la intención correcta. */
  const [correcting, setCorrecting] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const sendingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const stickToBottomRef = useRef(true);

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
          let saved: string | null = null;
          try { saved = localStorage.getItem(lastSessionKey(caseId)); } catch { saved = null; }
          const preferred = ss.find((s) => s.id === saved) ?? ss[0];
          setSession(preferred?.id ?? null);
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
        setMessages(r.messages.map(mapHistory));
        setFeedback(feedbackFromHistory(r.messages));
        setCorrecting(null);
      } catch { if (!cancelled) setMessages([]); }
    })();
    return () => { cancelled = true; };
  }, [open, caseId, sessionId]);

  // El historial y el resumen están en el servidor; esto solo fija la sesión
  // exacta para que al volver al otro día se abra la misma conversación.
  useEffect(() => {
    if (!sessionId) return;
    try { localStorage.setItem(lastSessionKey(caseId), sessionId); } catch { /* navegador sin storage */ }
  }, [caseId, sessionId]);

  const activeSession = useMemo(() => sessions.find((s) => s.id === sessionId), [sessions, sessionId]);
  const ocrMode = modeOverride?.ocr ?? activeSession?.ocr_mode ?? "todos";
  const asrMode = modeOverride?.asr ?? activeSession?.asr_mode ?? "todos";

  // Auto-scroll al último mensaje, solo si el usuario sigue cerca del fondo.
  // Durante el streaming se salta sin animación (evita saltos en cada token);
  // al terminar (`loading` false) el desplazamiento es suave.
  useEffect(() => {
    if (!stickToBottomRef.current) return;
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: loading ? "auto" : "smooth",
    });
  }, [messages, loading]);

  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  useEffect(() => () => abortRef.current?.abort(), []);

  // Reloj del turno: la síntesis tarda decenas de segundos y sin un contador
  // el panel parece colgado. Se reinicia en cada pregunta.
  useEffect(() => {
    if (!loading) return;
    const started = Date.now();
    setWaitSec(0);
    const timer = setInterval(() => setWaitSec(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [loading]);

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

  /** Recarga el historial persistido. `meta` conserva los campos del turno
   * recién respondido que el listado histórico todavía no devuelve. */
  async function loadMessages(sid: string, meta?: SendResponse) {
    try {
      const r = await api.get<{ messages: HistoryMessage[] }>(
        `/cases/${caseId}/chats/${sid}/messages`, { limit: "100" });
      const mapped = r.messages.map(mapHistory);
      if (meta) {
        for (let i = mapped.length - 1; i >= 0; i--) {
          if (mapped[i].role !== "assistant") continue;
          mapped[i] = {
            ...mapped[i],
            id: mapped[i].id || meta.assistant_message?.id,
            content: mapped[i].content || meta.answer || "",
            citations: meta.citations ?? mapped[i].citations,
            occurrences: meta.occurrences ?? mapped[i].occurrences,
            file_cards: meta.file_cards ?? mapped[i].file_cards,
            uncertainties: meta.uncertainties ?? mapped[i].uncertainties,
            answer_kind: meta.answer_kind,
            degraded: meta.degraded,
            elapsed_ms: meta.elapsed_ms,
            occurrences_note: meta.occurrences_note,
            occurrences_term: meta.occurrences_term,
            audit_status: meta.audit_status ?? mapped[i].audit_status,
            audit_id: meta.audit_id ?? mapped[i].audit_id,
            publication_blocked: meta.publication_blocked ?? mapped[i].publication_blocked,
          };
          break;
        }
      }
      setMessages(mapped);
      // Conserva el veredicto optimista del turno recién enviado y suma los persistidos.
      setFeedback((prev) => ({ ...prev, ...feedbackFromHistory(r.messages) }));
    } catch { /* se conserva lo que haya en pantalla */ }
  }

  async function releaseMessage(id: string) {
    if (!id || releasingId) return;
    setReleasingId(id);
    try {
      await api.post(`/cases/${caseId}/messages/${id}/release`, {
        reason: "Liberación humana desde el chat",
      });
      if (sessionId) await loadMessages(sessionId);
      toast.success("Respuesta original liberada.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "No se pudo liberar la respuesta.");
    } finally {
      setReleasingId(null);
    }
  }

  /** Re-ejecuta el gate legal sobre una respuesta ya publicada. No “aprueba”:
   * vuelve a medir claims y hallazgos, y el badge refleja el resultado real. */
  async function reaudit(message: ChatMessage) {
    const id = message.id;
    if (!id || auditingId) return;
    setAuditingId(id);
    try {
      const result = await api.post<{ status?: string }>(`/cases/${caseId}/messages/${id}/audit`);
      const status = result.status || "";
      const label = AUDIT_LABELS[status] || status || "sin estado";
      if (status === "PASSED") toast.success(`Auditoría: ${label}.`);
      else toast.message(`Auditoría: ${label}.`, {
        description: "Abre el detalle para ver los claims y hallazgos que impiden verificarla.",
      });
      if (sessionId) await loadMessages(sessionId);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "No se pudo auditar la respuesta.");
    } finally {
      setAuditingId(null);
    }
  }

  async function send() {
    if ((!input.trim() && attachments.length === 0) || loading || !caseId) return;
    const q = input.trim() || `Analiza ${attachments.map((a) => "@" + a.name).join(", ")}`;
    setInput("");
    await sendText(q, attachments);
  }

  function stopGeneration() {
    abortRef.current?.abort();
  }

  function appendStreamToken(text: string) {
    setMessages((m) => {
      const copy = [...m];
      for (let i = copy.length - 1; i >= 0; i--) {
        if (copy[i].role === "assistant" && copy[i].streaming) {
          copy[i] = { ...copy[i], content: copy[i].content + text };
          return copy;
        }
      }
      copy.push({ role: "assistant", content: text, streaming: true });
      return copy;
    });
  }

  /** Añade o actualiza una etapa. La anterior se marca como terminada. */
  function upsertStage(key: string, label: string, detail?: string,
                       state: "active" | "done" = "active") {
    setLiveStages((prev) => {
      const next = prev.map((s) => (s.state === "active" ? { ...s, state: "done" as const } : s));
      const i = next.findIndex((s) => s.key === key);
      const stage: LiveStage = { key, label, detail, state };
      if (i >= 0) next[i] = stage;
      else next.push(stage);
      return next.slice(-8);
    });
  }

  function resetStages() {
    stageSeq.current = 0;
    setLiveStages([]);
  }

  async function sendText(q: string, atts: Attachment[] = [], opts: { appendUser?: boolean } = {}) {
    if (loading || !caseId) return;
    sendingRef.current = true;
    setAttachments([]);
    setMenu(null);
    setPendingCorrection(null);
    if (opts.appendUser !== false) {
      setMessages((m) => [...m, { role: "user", content: q, attachments: atts }]);
    }
    setLoading(true);
    setReceiving(false);
    resetStages();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      let sid = sessionId;
      if (!sid) {
        const created = await api.post<Session>(`/cases/${caseId}/chats`, {
          agent_id: selectedAgent?.id ?? null,
          model_id: selectedModel?.id ?? null,
          ocr_mode: ocrMode,
          asr_mode: asrMode,
        });
        sid = created.id;
        setSession(sid);
        setSessions((ss) => [{ ...created, message_count: created.message_count ?? 0,
                               updated_at: created.updated_at ?? new Date().toISOString() }, ...ss]);
      }

      // `auto` deja que el router del backend elija la tubería; agente y modelo
      // viajan en CADA mensaje porque el backend los aplica sobre la sesión.
      const payload = {
        content: q,
        attachments: atts,
        strategy: "auto" as const,
        agent_id: selectedAgent?.id ?? null,
        model_id: selectedModel?.id ?? null,
        ocr_mode: ocrMode,
        asr_mode: asrMode,
      };

      let res: SendResponse | null = null;
      let streamError: string | null = null;
      let serverError: string | null = null;

      setStreaming(true);
      try {
        res = await api.postStream<SendResponse>(
          `/cases/${caseId}/chats/${sid}/messages/stream`,
          payload,
          (event, data) => {
            const d = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
            if (event === "stage") {
              const stageName = typeof d.stage === "string" ? d.stage : "";
              // `stage: done` es el cierre interno del pipeline; el evento `done`
              // con la respuesta completa llega justo después.
              if (stageName === "done") {
                setLiveStages((prev) => prev.map((s) => ({ ...s, state: "done" as const })));
                return;
              }
              const tool = typeof d.tool === "string" ? d.tool
                : typeof d.name === "string" ? d.name : "";
              const label = typeof d.label === "string" && d.label
                ? d.label
                : tool
                  ? `Consultando ${tool}…`
                  : STAGE_LABELS[stageName] || STAGE_FALLBACK;
              const detail: string[] = [];
              // En el cierre de una herramienta el label ya dice qué encontró.
              if (!(tool && d.phase === "done")) {
                if (typeof d.evidence === "number" && d.evidence > 0) {
                  detail.push(`${d.evidence} fragmento${d.evidence === 1 ? "" : "s"}`);
                }
                if (Array.isArray(d.sources)) {
                  const sources = d.sources.filter((s): s is string => typeof s === "string").slice(0, 3);
                  if (sources.length) detail.push(sources.join(" · "));
                }
              }
              if (typeof d.elapsed_ms === "number" && d.elapsed_ms > 0) {
                detail.push(`${(d.elapsed_ms / 1000).toFixed(1)} s`);
              }
              const key = tool
                ? `tool:${tool}:${String(d.step ?? "")}`
                : `${stageName}:${++stageSeq.current}`;
              upsertStage(key, label, detail.join(" · ") || undefined,
                d.phase === "done" ? "done" : "active");
            } else if (event === "done") {
              setLiveStages((prev) => prev.map((s) => ({ ...s, state: "done" as const })));
            } else if (event === "token") {
              if (typeof d.text === "string" && d.text) {
                setReceiving(true);
                appendStreamToken(d.text);
              }
            } else if (event === "error") {
              serverError = typeof d.message === "string" && d.message
                ? d.message
                : "No se pudo completar la respuesta.";
              streamError = serverError;
              controller.abort();
            }
          },
          controller.signal,
        );
      } catch (e) {
        if (controller.signal.aborted && !streamError) {
          // Cancelado por el usuario: se conserva el texto recibido y se marca.
          setMessages((m) => m.some((x) => x.streaming)
            ? m.map((x) => (x.streaming ? { ...x, streaming: false, stopped: true } : x))
            : [...m, { role: "assistant", content: "", stopped: true }]);
          return;
        }
        // Stream no disponible, incompleto o con error: se descarta el parcial.
        setMessages((m) => m.filter((x) => !x.streaming));
        if (!streamError) streamError = e instanceof Error ? e.message : "No se pudo abrir el stream.";
        res = null;
      }
      setStreaming(false);

      if (!res) {
        // Fallback único al POST normal. El mensaje del usuario no se vuelve a
        // añadir en pantalla: el historial recargado es la fuente de verdad.
        upsertStage("fallback", "Reintentando por la vía normal…");
        if (serverError) toast.error(serverError);
        res = await api.post<SendResponse>(`/cases/${caseId}/chats/${sid}/messages`, payload);
      }

      // Reemplaza el parcial por la respuesta final y después recarga el
      // historial una sola vez (nunca en cada token).
      const final = res;
      setMessages((m) => {
        const copy = [...m];
        for (let i = copy.length - 1; i >= 0; i--) {
          if (copy[i].role === "assistant" && copy[i].streaming) {
            copy[i] = { role: "assistant", content: final.answer || copy[i].content };
            break;
          }
        }
        return copy;
      });
      await loadMessages(sid, final);
      if (final.pending_correction) {
        setPendingCorrection({ tool: final.pending_correction.tool, summary: final.pending_correction.summary });
      }
      setSessions((ss) => ss.map((s) => s.id === sid
        ? { ...s, title: s.title || q.slice(0, 80), message_count: s.message_count + 2 } : s));
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Error al procesar la pregunta.";
      setMessages((m) => [...m.filter((x) => !x.streaming), { role: "assistant", content: msg, error: true }]);
      toast.error(msg);
    } finally {
      abortRef.current = null;
      sendingRef.current = false;
      setStreaming(false);
      setLiveStages([]);
      setLoading(false);
      setReceiving(false);
    }
  }

  async function copyMessage(content: string) {
    try {
      await navigator.clipboard.writeText(content);
      toast.success("Respuesta copiada");
    } catch {
      toast.error("No se pudo copiar");
    }
  }

  /** Veredicto del turno: alimenta el corpus y el perfil de intención del usuario.
   * Es optimista en la UI y se revierte si el POST falla. */
  async function postFeedback(message: ChatMessage, verdict: Verdict, corrected?: string) {
    const id = message.id;
    if (!id || !sessionId) return;
    const previous = feedback[id];
    setCorrecting(null);
    setFeedback((f) => ({ ...f, [id]: { verdict, corrected } }));
    try {
      const body = verdict === "wrong_intent"
        ? { verdict, corrected_intent: corrected }
        : { verdict };
      const saved = await api.post<{ verdict: Verdict; corrected_intent: string | null }>(
        `/cases/${caseId}/chats/${sessionId}/messages/${id}/feedback`, body);
      setFeedback((f) => ({
        ...f,
        [id]: { verdict: saved.verdict, corrected: saved.corrected_intent || undefined },
      }));
      toast.success(
        verdict === "accepted" ? "Gracias: ayuda a afinar la búsqueda"
          : verdict === "wrong_intent"
            ? `Anotado: querías ${INTENT_LABELS[corrected || ""] || "otra cosa"}`
            : "Anotado: no era lo que pedías");
    } catch {
      setFeedback((f) => {
        const next = { ...f };
        if (previous) next[id] = previous;
        else delete next[id];
        return next;
      });
      toast.error("No se pudo guardar tu valoración");
    }
  }

  function pickCorrection(message: ChatMessage, intent: string) {
    void postFeedback(message, "wrong_intent", intent);
  }

  /** «Olvidar mi estilo»: borra los ejemplos y el perfil de ESTE usuario. */
  async function forgetStyle() {
    if (!window.confirm("Se borrarán los ejemplos que el chat ha aprendido de tu forma de preguntar. ¿Continuar?")) return;
    try {
      const r = await api.delete<{ exemplars_deleted: number }>("/auth/me/intent-profile");
      setFeedback({});
      toast.success(`Listo: ${r.exemplars_deleted} ejemplos olvidados`);
    } catch { toast.error("No se pudo olvidar el estilo"); }
  }

  function regenerate(index: number) {
    if (loading) return;
    const previousUser = [...messages.slice(0, index)].reverse().find((m) => m.role === "user");
    if (!previousUser) return;
    setMessages((m) => m.slice(0, index));
    void sendText(previousUser.content, previousUser.attachments || [], { appendUser: false });
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
      if (sid === sessionId) {
        setSession(null);
        try { localStorage.removeItem(lastSessionKey(caseId)); } catch { /* sin storage */ }
      }
      toast.success("Conversación archivada");
    } catch (e) { toast.error(e instanceof Error ? e.message : "No se pudo archivar"); }
  }, [caseId, sessionId, setSession]);

  return (
    <>
      <button
        onClick={() => setOpen(!open)}
        className={cn(
          "fixed bottom-4 right-4 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition-transform hover:scale-105 sm:bottom-6 sm:right-6 sm:h-14 sm:w-14",
          open && "hidden",
        )}
        aria-label="Chat IA"
      >
        {open ? <X className="h-6 w-6" /> : <MessageSquare className="h-6 w-6" />}
      </button>

      {open && (
        <div className="fixed inset-2 z-40 flex h-[calc(100dvh-1rem)] w-[calc(100vw-1rem)] flex-col rounded-xl border bg-background shadow-xl sm:inset-4 sm:h-[calc(100dvh-2rem)] sm:w-[calc(100vw-2rem)]">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <h3 className="font-semibold">Chat IA</h3>
            <div className="flex items-center gap-1">
              <Button variant="ghost" size="icon" title="Olvidar mi estilo de búsqueda"
                aria-label="Olvidar mi estilo de búsqueda" onClick={() => void forgetStyle()}>
                <BrainCircuit className="h-4 w-4" />
              </Button>
              <Button variant="ghost" size="icon" title="Nueva conversación" aria-label="Nueva conversación"
                onClick={() => {
                  setSession(null); setMessages([]); setModeOverride(null); setCorrecting(null);
                  try { localStorage.removeItem(lastSessionKey(caseId)); } catch { /* sin storage */ }
                }}>
                <Plus className="h-4 w-4" />
              </Button>
              <Button variant="ghost" size="icon" aria-label="Cerrar chat" onClick={() => setOpen(false)}>
                <X className="h-4 w-4" />
              </Button>
            </div>
          </div>

          <div className="space-y-2 border-b px-4 py-2">
            <div className="flex flex-col gap-2 sm:flex-row">
              {sessions.length > 0 && (
                <Select value={sessionId ?? ""} onValueChange={(v) => {
                  if (v === "__new__") {
                    setSession(null); setMessages([]); setPendingCorrection(null); setModeOverride(null);
                    try { localStorage.removeItem(lastSessionKey(caseId)); } catch { /* sin storage */ }
                  } else {
                    setSession(v || null); setModeOverride(null);
                    try {
                      if (v) localStorage.setItem(lastSessionKey(caseId), v);
                    } catch { /* sin storage */ }
                  }
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
                  aria-label="Archivar conversación" onClick={() => archiveSession(sessionId)}>
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
            <div className="flex flex-col gap-2 sm:flex-row">
              <Select value={ocrMode} onValueChange={(v) => setModeOverride((prev) => ({ ...prev, ocr: v as RecognitionMode }))}>
                <SelectTrigger className="h-8 flex-1 text-xs">
                  <SelectValue placeholder="OCR" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="todos">OCR: todos</SelectItem>
                  <SelectItem value="basico">OCR: básico</SelectItem>
                  <SelectItem value="document_ai">OCR: Document AI</SelectItem>
                </SelectContent>
              </Select>
              <Select value={asrMode} onValueChange={(v) => setModeOverride((prev) => ({ ...prev, asr: v as RecognitionMode }))}>
                <SelectTrigger className="h-8 flex-1 text-xs">
                  <SelectValue placeholder="ASR" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="todos">ASR: todos</SelectItem>
                  <SelectItem value="basico">ASR: básico</SelectItem>
                  <SelectItem value="document_ai">ASR: Document AI</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div ref={scrollRef} onScroll={handleScroll} className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.length === 0 && (
              <p className="mt-8 text-center text-sm text-muted-foreground">
                Pregunta sobre el expediente. Escribe <b>/</b> para elegir un agente y <b>@</b> para adjuntar un archivo.
              </p>
            )}
            {messages.map((m, i) => (
              <div key={i} className={cn("min-w-0 rounded-lg px-3 py-2 text-sm", m.role === "user" ? "ml-8 bg-primary text-primary-foreground" : "mr-8 bg-muted")}>
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
                {m.role === "assistant" ? (
                  m.error ? (
                    <p className="flex items-start gap-1.5 text-destructive">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                      <span>{m.content}</span>
                    </p>
                  ) : <Markdown text={m.content} />
                ) : <p>{m.content}</p>}
                {m.role === "assistant" && !m.error && (m.audit_status || m.publication_blocked) && (
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <AuditBadge
                      status={m.audit_status}
                      auditId={m.audit_id}
                      caseId={caseId}
                      blocked={m.publication_blocked}
                      onNavigate={() => setOpen(false)}
                    />
                    {m.publication_blocked && (
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[11px] text-muted-foreground">
                          Respuesta original retenida en la auditoría hasta la segunda opinión.
                        </span>
                        {m.id && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 text-xs"
                            disabled={releasingId === m.id}
                            onClick={() => void releaseMessage(m.id!)}
                          >
                            {releasingId === m.id ? "Liberando…" : "Liberar respuesta"}
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                )}
                {m.stopped && (
                  <p className="mt-1 text-xs text-muted-foreground">Generación detenida.</p>
                )}
                {m.role === "assistant" && !m.streaming && m.elapsed_ms != null && (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    Listo en {durationLabel(m.elapsed_ms)}
                    {m.citations && m.citations.length > 0
                      ? ` · ${m.citations.length} fuente${m.citations.length === 1 ? "" : "s"}`
                      : ""}
                  </p>
                )}
                {visibleUncertainties(m).length > 0 && (
                  <div className="mt-2 space-y-0.5">
                    {visibleUncertainties(m).map((u, j) => (
                      <p key={j} className="flex items-start gap-1 text-xs text-amber-600 dark:text-amber-400">
                        <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />{u}
                      </p>
                    ))}
                  </div>
                )}
                {m.citations && m.citations.length > 0 && (
                  <CitationList citations={m.citations} onOpen={openCitation} />
                )}
                {m.answer_kind !== "insufficient" && m.occurrences && m.occurrences.length > 0 && (
                  <Occurrences items={m.occurrences} term={m.occurrences_term}
                    note={m.occurrences_note} onOpen={openCitation} />
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
                        <Button variant="ghost" size="icon" className="h-7 w-7" title="Ver"
                          aria-label={`Ver ${card.name}`} onClick={() => viewCard(card)}>
                          <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-7 w-7" title="Descargar"
                          aria-label={`Descargar ${card.name}`} onClick={() => download(card)}>
                          <Download className="h-3.5 w-3.5" aria-hidden="true" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
                {m.role === "assistant" && !m.streaming && (
                  <div className="mt-2 flex flex-wrap items-center gap-1 border-t pt-1.5">
                    {!!m.content && (
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-xs"
                        title="Copiar respuesta" aria-label="Copiar respuesta" onClick={() => copyMessage(m.content)}>
                        <Copy className="mr-1 h-3 w-3" aria-hidden="true" /> Copiar
                      </Button>
                    )}
                    {!m.error && (
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-xs"
                        title="Regenerar respuesta" aria-label="Regenerar respuesta" onClick={() => regenerate(i)}>
                        <RotateCcw className="mr-1 h-3 w-3" aria-hidden="true" /> Regenerar
                      </Button>
                    )}
                    {!m.error && m.id && canAudit && (
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-xs"
                        title={m.audit_status ? "Volver a ejecutar la auditoría legal" : "Ejecutar la auditoría legal"}
                        aria-label={m.audit_status ? "Volver a ejecutar la auditoría legal" : "Ejecutar la auditoría legal"}
                        disabled={auditingId === m.id}
                        onClick={() => void reaudit(m)}>
                        {auditingId === m.id
                          ? <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden="true" />
                          : <ShieldCheck className="mr-1 h-3 w-3" aria-hidden="true" />}
                        {m.audit_status ? "Re-auditar" : "Auditar"}
                      </Button>
                    )}
                    {m.error && (
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-xs"
                        title="Reintentar" aria-label="Reintentar" onClick={() => regenerate(i)}>
                        <RefreshCw className="mr-1 h-3 w-3" aria-hidden="true" /> Reintentar
                      </Button>
                    )}
                    {!m.error && m.id && (
                      <>
                        <Button variant="ghost" size="sm"
                          className={cn("h-7 px-2 text-xs", feedback[m.id]?.verdict === "accepted" && "text-emerald-600 dark:text-emerald-400")}
                          title="Era lo que pedía" aria-label="Era lo que pedía"
                          aria-pressed={feedback[m.id]?.verdict === "accepted"}
                          onClick={() => void postFeedback(m, "accepted")}>
                          <ThumbsUp className="mr-1 h-3 w-3" aria-hidden="true" /> Útil
                        </Button>
                        <Button variant="ghost" size="sm"
                          className={cn("h-7 px-2 text-xs",
                            (feedback[m.id]?.verdict === "rejected" || feedback[m.id]?.verdict === "wrong_intent") && "text-destructive")}
                          title="No era lo que pedía: dime qué era" aria-label="No era lo que pedía: dime qué era"
                          aria-pressed={feedback[m.id]?.verdict === "rejected" || feedback[m.id]?.verdict === "wrong_intent"}
                          onClick={() => setCorrecting((c) => (c === m.id ? null : (m.id ?? null)))}>
                          <ThumbsDown className="mr-1 h-3 w-3" aria-hidden="true" /> No era esto
                        </Button>
                        {feedback[m.id]?.verdict === "wrong_intent" && (
                          <span className="px-1 text-xs text-muted-foreground">
                            Era: {INTENT_LABELS[feedback[m.id]?.corrected || ""] || "otra cosa"}
                          </span>
                        )}
                      </>
                    )}
                  </div>
                )}
                {correcting === m.id && (
                  <div className="mt-2 rounded-md border bg-background p-2" role="group" aria-label="¿Qué querías pedir?">
                    <p className="mb-1.5 text-xs font-medium">¿Qué querías?</p>
                    <div className="flex flex-wrap gap-1">
                      {INTENT_OPTIONS.map((option) => (
                        <Button key={option.value} variant="outline" size="sm"
                          className="h-7 px-2 text-xs" title={option.hint}
                          onClick={() => pickCorrection(m, option.value)}>
                          {option.label}
                        </Button>
                      ))}
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-xs"
                        onClick={() => void postFeedback(m, "rejected")}>
                        No lo sé
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            ))}
            {loading && !receiving && (
              <div className="rounded-lg border bg-muted/30 p-3 text-xs" role="status" aria-live="polite">
                <p className="mb-2 flex items-baseline justify-between gap-2 font-medium text-muted-foreground">
                  <span>Trabajando en tu pregunta</span>
                  {waitSec >= 3 && (
                    <span className="text-[11px] font-normal tabular-nums" aria-label={`${waitSec} segundos transcurridos`}>
                      {waitSec} s
                    </span>
                  )}
                </p>
                <div className="space-y-1.5">
                  {liveStages.length === 0 && (
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden="true" />
                      <span>{STAGE_FALLBACK}</span>
                    </div>
                  )}
                  {liveStages.map((s) => (
                    <div key={s.key} className="flex items-start gap-2">
                      {s.state === "active" ? (
                        <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" aria-hidden="true" />
                      ) : (
                        <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
                      )}
                      <div className="min-w-0">
                        <p className={cn("break-words", s.state === "active" ? "text-foreground" : "text-muted-foreground")}>
                          {s.label}
                        </p>
                        {s.detail && (
                          <p className="mt-0.5 break-words text-[11px] text-muted-foreground">{s.detail}</p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                {waitSec >= 15 && (
                  <p className="mt-2 text-[11px] text-muted-foreground">
                    El modelo sigue redactando. La respuesta se publica completa cuando pasa el gate legal.
                  </p>
                )}
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
                    <Bot className="h-3.5 w-3.5" aria-hidden="true" />{selectedAgent.name}
                    <button type="button" onClick={() => setSelectedAgent(null)}
                      aria-label={`Quitar agente ${selectedAgent.name}`}
                      className="ml-1 rounded text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background">
                      <X className="h-3 w-3" aria-hidden="true" />
                    </button>
                  </span>
                )}
                {attachments.map((a) => (
                  <span key={a.id} className="inline-flex items-center gap-1 rounded-md border bg-muted/40 px-2 py-1 text-xs">
                    {a.kind === "document" ? <FileText className="h-3.5 w-3.5" /> : <Video className="h-3.5 w-3.5" />}
                    <span className="max-w-40 truncate">{a.name}</span>
                    <button type="button" aria-label={`Quitar ${a.name}`}
                      onClick={() => setAttachments((prev) => prev.filter((x) => x.id !== a.id))}
                      className="ml-1 rounded text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background">
                      <X className="h-3 w-3" aria-hidden="true" />
                    </button>
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
              <Button
                type={streaming ? "button" : "submit"}
                size="icon"
                aria-label={streaming ? "Detener la respuesta" : loading ? "Procesando la respuesta" : "Enviar pregunta"}
                title={streaming ? "Detener la respuesta" : loading ? "Procesando la respuesta" : "Enviar pregunta"}
                disabled={loading ? !streaming : (!input.trim() && attachments.length === 0)}
                onClick={streaming ? stopGeneration : undefined}
              >
                {streaming
                  ? <Square className="h-4 w-4" aria-hidden="true" />
                  : loading
                    ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    : <Send className="h-4 w-4" aria-hidden="true" />}
              </Button>
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
