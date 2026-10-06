"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { FolderKanban, Plus, ChevronRight, Pencil, Trash2, AlertTriangle, Activity, FileText, Video, GitBranch, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { DocumentOcrViewer } from "@/components/document-ocr-viewer";
import { MediaTranscriptViewer } from "@/components/media-transcript-viewer";

interface Case { id: string; case_number: string; title: string; created_at: string; version: number; }
interface TimelineSource {
  citation_id: string; source_type: string; document_id?: string | null; page?: number | null;
  media_id?: string | null; start_ms?: number | null; end_ms?: number | null; filename?: string | null;
}
interface TimelineEvent {
  id: string; event_date: string | null; date_precision?: string | null; event_type?: string | null;
  description: string; timeline_confidence?: string | null; sources: TimelineSource[];
  kind?: string | null; subtype?: string | null; instance?: string | null; actor?: string | null;
  authority?: string | null; date_type?: string | null; procedural_effect?: string | null;
  document_id?: string | null; page_number?: number | null; document_filename?: string | null;
  duplicate_of?: string | null; review_flags?: string[] | null;
}

const INSTANCE_ES: Record<string, string> = {
  primera: "Primera instancia", segunda: "Segunda instancia", casacion: "Casación", tutela: "Tutela",
  incidente: "Incidente", cautelar: "Medida cautelar", ejecucion: "Ejecución", otro: "Otras actuaciones",
  generico: "Otros eventos",
};
const ACTOR_ES: Record<string, string> = {
  juzgado: "Juzgado", demandante: "Demandante", demandado: "Demandado",
  apoderado_demandante: "Apod. demandante", apoderado_demandado: "Apod. demandado",
  tercero: "Tercero", fiscal: "Fiscalía", secretario: "Secretaría", otro: "Otro",
};
const SUBTYPE_ES: Record<string, string> = {
  demanda: "Demanda", reforma_demanda: "Reforma de demanda", subsanacion: "Subsanación", rechazo: "Rechazo",
  inadmision: "Inadmisión", admision: "Admisión", reparto: "Reparto", radicacion: "Radicación",
  contestacion: "Contestación", reconvencion: "Reconvención", excepciones: "Excepciones", memorial: "Memorial",
  solicitud: "Solicitud", desistimiento: "Desistimiento", alegato: "Alegato", recurso: "Recurso", objecion: "Objeción",
  auto: "Auto", auto_admisorio: "Auto admisorio", auto_pruebas: "Auto de pruebas", auto_fija_audiencia: "Fija audiencia",
  auto_suspension: "Suspensión", auto_terminacion: "Terminación", sentencia: "Sentencia", correccion: "Corrección",
  aclaracion: "Aclaración", adicion: "Adición", apremio: "Apremio", reposicion: "Reposición", apelacion: "Apelación",
  queja: "Queja", casacion: "Casación", revision: "Revisión", nulidad: "Nulidad",
  concesion_recurso: "Concesión de recurso", improcedencia_recurso: "Improcedencia del recurso",
  notificacion_personal: "Notificación personal", notificacion_estado: "Notificación por estado",
  notificacion_electronica: "Notificación electrónica", emplazamiento: "Emplazamiento", comunicacion: "Comunicación",
  solicitud_prueba: "Solicitud de prueba", decreto_prueba: "Decreto de prueba", practica_prueba: "Práctica de prueba",
  testimonio: "Testimonio", interrogatorio: "Interrogatorio", dictamen: "Dictamen", inspeccion_judicial: "Inspección judicial",
  incorporacion_documental: "Incorporación documental", audiencia: "Audiencia", audiencia_inicial: "Audiencia inicial",
  audiencia_juzgamiento: "Audiencia de juzgamiento", suspension_audiencia: "Suspensión de audiencia",
  mandamiento_pago: "Mandamiento de pago", embargo: "Embargo", secuestro: "Secuestro", remate: "Remate",
  liquidacion_credito: "Liquidación del crédito", avaluo: "Avalúo", otro: "Otro",
};
const INSTANCE_ORDER = ["primera", "segunda", "casacion", "tutela", "incidente", "cautelar", "ejecucion", "otro", "generico"];
const FLAG_ES: Record<string, string> = {
  duplicado: "duplicado", fecha_inconsistente: "fecha inconsistente",
  sin_fuente_real: "sin fuente real", sin_fecha: "sin fecha",
};

function mmss(ms: number) {
  const s = Math.floor((ms || 0) / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export default function ProcesosPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [caseNumber, setCaseNumber] = useState("");
  const [editing, setEditing] = useState<Case | null>(null);
  const [editName, setEditName] = useState("");
  const [deleting, setDeleting] = useState<Case | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [timelineCase, setTimelineCase] = useState<Case | null>(null);
  const [tlKind, setTlKind] = useState<string>("procedural");
  const [tlInstance, setTlInstance] = useState<string>("all");
  const [tlActor, setTlActor] = useState<string>("all");
  const [docView, setDocView] = useState<{ caseId: string; id: string; page: number; name?: string | null } | null>(null);
  const [mediaView, setMediaView] = useState<{ caseId: string; id: string; ms: number; name?: string | null } | null>(null);

  const { data: cases = [], isLoading } = useQuery({
    queryKey: ["cases"],
    queryFn: () => api.get<Case[]>("/cases"),
  });

  const { data: timeline = [], isLoading: timelineLoading } = useQuery({
    queryKey: ["timeline", timelineCase?.id, tlKind, tlInstance, tlActor],
    queryFn: () => {
      const params: Record<string, string> = {};
      if (tlKind !== "all") params.kind = tlKind;
      if (tlInstance !== "all") params.instance = tlInstance;
      if (tlActor !== "all") params.actor = tlActor;
      return api.get<TimelineEvent[]>(`/cases/${timelineCase!.id}/timeline`, params);
    },
    enabled: !!timelineCase,
  });
  const tlInstances = Array.from(new Set(timeline.map((e) => e.instance).filter(Boolean))) as string[];
  const tlActors = Array.from(new Set(timeline.map((e) => e.actor).filter(Boolean))) as string[];
  const tlGroups = INSTANCE_ORDER
    .map((k) => [k, timeline.filter((e) => (e.instance || (e.kind === "procedural" ? "otro" : "generico")) === k)] as const)
    .filter(([, list]) => list.length > 0);

  const buildGraph = useMutation({
    mutationFn: () => api.post<{ linked?: { relationships?: number }; reviewed?: { flagged?: number } }>(
      `/cases/${timelineCase!.id}/timeline/build`, {}),
    onSuccess: (r: { linked?: { relationships?: number }; reviewed?: { flagged?: number } }) => {
      toast.success(`Process Graph: ${r?.linked?.relationships ?? 0} relaciones · ${r?.reviewed?.flagged ?? 0} marcas`);
      qc.invalidateQueries({ queryKey: ["timeline"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo construir el grafo"),
  });

  const buildGraphAi = useMutation({
    mutationFn: () => api.post<{ linked?: { relationships?: number }; ai_links?: { links?: number } }>(
      `/cases/${timelineCase!.id}/timeline/build?llm=true`, {}),
    onSuccess: (r) => {
      toast.success(`IA: ${r?.ai_links?.links ?? 0} relaciones causales · ${r?.linked?.relationships ?? 0} por reglas`);
      qc.invalidateQueries({ queryKey: ["timeline"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "La IA no pudo proponer relaciones (¿modelo/cuota?)"),
  });

  const create = useMutation({
    mutationFn: () => {
      const number = caseNumber.trim() || `EXP-${Date.now()}-${Math.random().toString(36).slice(2, 6).toUpperCase()}`;
      // Radicado colombiano de 23 dígitos => jurisdicción "co"; otro identificador => "generic".
      const jurisdiction = /^\d{23}$/.test(number) ? "co" : "generic";
      return api.post<Case>("/cases", { jurisdiction, case_number: number, title: name.trim(), language: "es" });
    },
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["cases"] });
      setOpen(false);
      setName("");
      setCaseNumber("");
      toast.success("Proceso creado");
      router.push(`/dashboard/procesos/${c.id}`);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error al crear el proceso"),
  });

  const rename = useMutation({
    mutationFn: () => api.patch(`/cases/${editing!.id}`, { title: editName.trim(), expected_version: editing!.version }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cases"] });
      setEditing(null);
      toast.success("Proceso renombrado");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error al renombrar"),
  });

  const purge = useMutation({
    mutationFn: (id: string) => api.delete(`/cases/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cases"] });
      setDeleting(null);
      setConfirmText("");
      toast.success("Proceso eliminado por completo (archivos, OCR/ASR, embeddings y grafo)");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error al eliminar"),
  });

  function openRename(c: Case) { setEditing(c); setEditName(c.title); }
  function openDelete(c: Case) { setDeleting(c); setConfirmText(""); }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Procesos</h1>
          <p className="text-muted-foreground">Cada proceso organiza sus carpetas y archivos (PDFs, videos, Excel, Word, imágenes)</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild><Button><Plus className="mr-2 h-4 w-4" />Nuevo proceso</Button></DialogTrigger>
          <DialogContent>
            <DialogHeader><DialogTitle>Crear proceso</DialogTitle></DialogHeader>
            <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }} className="space-y-4">
              <div>
                <label className="text-sm font-medium">Nombre del proceso</label>
                <Input value={name} onChange={(e) => setName(e.target.value)} required minLength={3}
                  placeholder="Expediente ejecutivo hipotecario" />
              </div>
              <div>
                <label className="text-sm font-medium">Número de radicado <span className="font-normal text-muted-foreground">(opcional)</span></label>
                <Input value={caseNumber} onChange={(e) => setCaseNumber(e.target.value)}
                  placeholder="11001310302120180036100" maxLength={64} />
                <p className="mt-1 text-xs text-muted-foreground">Si lo dejas vacío se genera un identificador automático.</p>
              </div>
              <Button type="submit" className="w-full" disabled={create.isPending || name.trim().length < 3}>
                {create.isPending ? "Creando…" : "Crear proceso"}
              </Button>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {isLoading ? (
        <p className="text-muted-foreground">Cargando…</p>
      ) : cases.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-md border py-16">
          <FolderKanban className="mb-4 h-12 w-12 text-muted-foreground" />
          <p className="text-muted-foreground">Aún no hay procesos. Crea el primero.</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {cases.map((c) => (
            <Card key={c.id} className="group transition-colors hover:border-primary/60">
              <CardHeader>
                <CardTitle className="flex items-center justify-between gap-2">
                  <Link href={`/dashboard/procesos/${c.id}`} className="flex min-w-0 items-center gap-2 hover:text-primary">
                    <FolderKanban className="h-5 w-5 shrink-0 text-primary" />
                    <span className="truncate">{c.title}</span>
                  </Link>
                  <span className="flex shrink-0 items-center gap-1">
                    <span className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                      <Button variant="ghost" size="icon" title="Ver línea de tiempo procesal" onClick={() => setTimelineCase(c)}>
                        <Activity className="h-4 w-4" />
                      </Button>
                      <Button variant="ghost" size="icon" title="Renombrar proceso" onClick={() => openRename(c)}>
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button variant="ghost" size="icon" title="Eliminar proceso" onClick={() => openDelete(c)}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </span>
                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <Badge variant="outline" className="font-mono text-xs">{c.case_number}</Badge>
                <p className="text-xs text-muted-foreground">
                  Creado el {new Date(c.created_at).toLocaleDateString("es-CO", { year: "numeric", month: "long", day: "numeric" })}
                </p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={editing !== null} onOpenChange={(o) => { if (!o) setEditing(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>Renombrar proceso</DialogTitle></DialogHeader>
          <form onSubmit={(e) => { e.preventDefault(); rename.mutate(); }} className="space-y-4">
            <div>
              <label className="text-sm font-medium">Nombre del proceso</label>
              <Input value={editName} onChange={(e) => setEditName(e.target.value)} required minLength={3} />
              <p className="mt-1 font-mono text-xs text-muted-foreground">{editing?.case_number}</p>
            </div>
            <Button type="submit" className="w-full" disabled={rename.isPending || editName.trim().length < 3}>
              {rename.isPending ? "Guardando…" : "Guardar"}
            </Button>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={deleting !== null} onOpenChange={(o) => { if (!o) { setDeleting(null); setConfirmText(""); } }}>
        <DialogContent>
          <DialogHeader><DialogTitle>Eliminar proceso</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="flex items-start gap-3 rounded-md border border-destructive/50 bg-destructive/10 p-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
              <div className="text-sm">
                <p className="font-medium">Esta acción es irreversible.</p>
                <p className="text-muted-foreground">
                  Se eliminarán todos los archivos (Excel, PDF, imágenes, videos), las carpetas y subcarpetas,
                  el OCR de cada página, las transcripciones, los embeddings (pgvector), el knowledge graph y
                  todos los registros de la base de datos de este proceso.
                </p>
              </div>
            </div>
            <div>
              <p className="mb-2 text-sm">
                Para confirmar, escribe el radicado: <span className="font-mono font-medium">{deleting?.case_number}</span>
              </p>
              <Input value={confirmText} onChange={(e) => setConfirmText(e.target.value)}
                placeholder={deleting?.case_number} className="font-mono" />
            </div>
            <Button variant="destructive" className="w-full"
              disabled={purge.isPending || confirmText.trim() !== deleting?.case_number}
              onClick={() => purge.mutate(deleting!.id)}>
              {purge.isPending ? "Eliminando…" : "Eliminar definitivamente"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={timelineCase !== null} onOpenChange={(o) => { if (!o) setTimelineCase(null); }}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-2"><Activity className="h-5 w-5 text-primary" />Línea de tiempo procesal</span>
              <span className="mr-6 flex items-center gap-2">
                <Button size="sm" variant="outline" className="h-7 gap-1" disabled={buildGraph.isPending}
                  title="Recalcular relaciones por reglas, resolver eventos referenciados y marcar duplicados/inconsistencias"
                  onClick={() => buildGraph.mutate()}>
                  <GitBranch className="h-3.5 w-3.5" />{buildGraph.isPending ? "Construyendo…" : "Relacionar y revisar"}
                </Button>
                <Button size="sm" variant="outline" className="h-7 gap-1" disabled={buildGraphAi.isPending}
                  title="Segundo pase con IA: propone relaciones causales (usa el modelo de la organización, p. ej. Gemini)"
                  onClick={() => buildGraphAi.mutate()}>
                  <Sparkles className="h-3.5 w-3.5" />{buildGraphAi.isPending ? "IA…" : "IA: causas"}
                </Button>
              </span>
            </DialogTitle>
          </DialogHeader>
          <p className="-mt-2 text-sm text-muted-foreground">
            {timelineCase?.title} · <span className="font-mono">{timelineCase?.case_number}</span>
          </p>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-muted-foreground">Filtros:</span>
            <Button size="sm" variant={tlKind === "all" ? "default" : "outline"} className="h-7" onClick={() => setTlKind("all")}>Todo</Button>
            <Button size="sm" variant={tlKind === "procedural" ? "default" : "outline"} className="h-7" onClick={() => setTlKind("procedural")}>Actuaciones</Button>
            <Button size="sm" variant={tlKind === "generic" ? "default" : "outline"} className="h-7" onClick={() => setTlKind("generic")}>Otros</Button>
            <span className="mx-1 h-4 w-px bg-border" />
            <Button size="sm" variant={tlInstance === "all" ? "default" : "outline"} className="h-7" onClick={() => setTlInstance("all")}>Toda instancia</Button>
            {tlInstances.map((i) => (
              <Button key={i} size="sm" variant={tlInstance === i ? "default" : "outline"} className="h-7" onClick={() => setTlInstance(tlInstance === i ? "all" : i)}>
                {INSTANCE_ES[i] || i}
              </Button>
            ))}
            {tlActors.length > 0 && <span className="mx-1 h-4 w-px bg-border" />}
            {tlActors.map((a) => (
              <Button key={a} size="sm" variant={tlActor === a ? "default" : "outline"} className="h-7" onClick={() => setTlActor(tlActor === a ? "all" : a)}>
                {ACTOR_ES[a] || a}
              </Button>
            ))}
          </div>
          <div className="max-h-[60vh] overflow-y-auto pr-1">
            {timelineLoading ? (
              <p className="py-6 text-center text-sm text-muted-foreground">Cargando…</p>
            ) : timeline.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                No hay actuaciones en la línea de tiempo todavía. Se alimenta de la extracción de actuaciones
                procesales del expediente (autos, sentencias, recursos, pruebas, audiencias…).
              </p>
            ) : (
              tlGroups.map(([instance, list]) => (
                <section key={instance} className="mb-4">
                  <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    {INSTANCE_ES[instance] || instance} · {list.length}
                  </h4>
                  <ol className="relative ml-2 border-l border-border">
                    {list.map((ev) => (
                      <li key={ev.id} className="relative mb-5 ml-4">
                        <span className="absolute -left-[21px] top-1.5 h-3 w-3 rounded-full border-2 border-background bg-primary" />
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-semibold">
                            {ev.event_date
                              ? new Date(ev.event_date).toLocaleDateString("es-CO", { year: "numeric", month: "short", day: "numeric" })
                              : "Sin fecha"}
                          </span>
                          {ev.subtype && <Badge variant="secondary" className="text-[10px]">{SUBTYPE_ES[ev.subtype] || ev.subtype}</Badge>}
                          {ev.actor && <Badge variant="outline" className="text-[10px]">{ACTOR_ES[ev.actor] || ev.actor}</Badge>}
                          {ev.date_type === "referenciada" && (
                            <Badge variant="outline" className="border-amber-500 text-[10px] text-amber-600">referenciada</Badge>
                          )}
                          {(ev.review_flags ?? []).map((f) => (
                            <Badge key={f} variant="outline" className="border-rose-500 text-[10px] text-rose-600">
                              {FLAG_ES[f] || f}
                            </Badge>
                          ))}
                          {ev.timeline_confidence && ev.timeline_confidence !== "source_backed" && (
                            <Badge variant="outline" className="text-[10px]">{ev.timeline_confidence}</Badge>
                          )}
                        </div>
                        {ev.authority && <p className="mt-0.5 text-[11px] text-muted-foreground">{ev.authority}</p>}
                        <p className="mt-1 text-sm">{ev.description}</p>
                        {ev.procedural_effect && (
                          <p className="mt-0.5 text-xs text-muted-foreground"><b>Efecto:</b> {ev.procedural_effect}</p>
                        )}
                        {ev.sources?.length > 0 && (
                          <div className="mt-1 flex flex-wrap gap-1">
                            {ev.sources.map((s) => (
                              <button key={s.citation_id} type="button"
                                onClick={() => {
                                  if (!timelineCase) return;
                                  if (s.source_type === "document_page" && s.document_id) {
                                    setDocView({ caseId: timelineCase.id, id: s.document_id, page: s.page || 1, name: s.filename });
                                  } else if (s.media_id) {
                                    setMediaView({ caseId: timelineCase.id, id: s.media_id, ms: s.start_ms || 0, name: s.filename });
                                  }
                                }}
                                className="inline-flex items-center gap-1 rounded border bg-background/60 px-1.5 py-0.5 text-xs text-muted-foreground underline decoration-dotted hover:text-foreground">
                                {s.source_type === "document_page" ? <FileText className="h-3 w-3" /> : <Video className="h-3 w-3" />}
                                {s.filename || (s.source_type === "document_page" ? "documento" : "video")}
                                {s.source_type === "document_page" ? ` p.${s.page}` : ` ${mmss(s.start_ms || 0)}`}
                              </button>
                            ))}
                          </div>
                        )}
                      </li>
                    ))}
                  </ol>
                </section>
              ))
            )}
          </div>
        </DialogContent>
      </Dialog>

      <DocumentOcrViewer
        caseId={docView?.caseId ?? ""}
        documentId={docView?.id ?? null}
        filename={docView?.name}
        initialPage={docView?.page}
        onClose={() => setDocView(null)}
      />
      <MediaTranscriptViewer
        caseId={mediaView?.caseId ?? ""}
        mediaId={mediaView?.id ?? null}
        filename={mediaView?.name}
        initialMs={mediaView?.ms}
        onClose={() => setMediaView(null)}
      />
    </div>
  );
}
