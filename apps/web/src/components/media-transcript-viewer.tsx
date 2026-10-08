"use client";

/** Visor de audiencia: video a la izquierda, transcripción a la derecha.
 * Marca el segmento activo según el minuto del video, permite saltar a un minuto
 * al hacer clic y corregir el texto (alimenta el diccionario y reindexa). */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, streamUrl } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SpeakerTags, type Speaker } from "@/components/speaker-tags";
import { SpeakerFormDialog } from "@/components/speaker-form-dialog";
import { Save } from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

interface Segment {
  id: string;
  start_ms: number;
  end_ms: number;
  text: string;
  confidence: number;
  needs_review: boolean;
  speaker_id?: string | null;
  speaker_label?: string;
  speaker_name?: string | null;
}

function mmss(ms: number) {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function MediaTranscriptViewer({
  caseId,
  mediaId,
  filename,
  initialMs,
  processingStatus,
  onClose,
}: {
  caseId: string;
  mediaId: string | null;
  filename?: string | null;
  /** Milisegundo inicial al abrir (p. ej. una cita del chat: "video 14:32"). */
  initialMs?: number | null;
  /** Estado de procesamiento del media (para mostrar "transcribiendo…"). */
  processingStatus?: string | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const open = !!mediaId;

  const [currentMs, setCurrentMs] = useState(0);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [editSpeakerId, setEditSpeakerId] = useState("none");
  const [newSpeakerOpen, setNewSpeakerOpen] = useState(false);

  const videoRef = useRef<HTMLVideoElement>(null);
  const segRefs = useRef<Record<string, HTMLDivElement | null>>({});

  const { data } = useQuery({
    queryKey: ["segments", caseId, mediaId],
    queryFn: () =>
      api.get<{ segments: Segment[]; speakers: Speaker[]; unknown_speaker_id?: string | null }>(
        `/cases/${caseId}/media/${mediaId}/segments`),
    enabled: !!mediaId,
  });
  const segments = data?.segments;
  const speakers = data?.speakers ?? [];
  const unidentified = data?.unknown_speaker_id ?? "none";

  // Streaming directo: el <video> lee por HTTP Range (no espera a descargar todo).
  const videoUrl = useMemo(
    () => (mediaId ? streamUrl(`/cases/${caseId}/media/${mediaId}/download`) : null),
    [caseId, mediaId],
  );
  // Al cambiar de media, reinicia tiempo/edición (ajuste en render, sin efecto de estado).
  const [prevMedia, setPrevMedia] = useState(mediaId);
  if (mediaId !== prevMedia) {
    setPrevMedia(mediaId);
    setCurrentMs(0);
    setEditingId(null);
  }

  // Deep-link (cita del chat): cuando el video está listo, salta al minuto indicado
  // en el siguiente frame (acceso al DOM del <video> sin actualización síncrona en el efecto).
  useEffect(() => {
    if (!videoUrl || initialMs == null) return;
    const id = requestAnimationFrame(() => {
      if (videoRef.current) videoRef.current.currentTime = initialMs / 1000;
      setCurrentMs(initialMs);
    });
    return () => cancelAnimationFrame(id);
  }, [videoUrl, initialMs]);

  const activeId = useMemo(() => {
    if (!segments) return null;
    return segments.find((s) => currentMs >= s.start_ms && currentMs < s.end_ms)?.id ?? null;
  }, [segments, currentMs]);

  useEffect(() => {
    if (!activeId) return;
    const el = segRefs.current[activeId];
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [activeId]);

  const save = useMutation({
    mutationFn: (segId: string) =>
      api.patch(`/cases/${caseId}/media/${mediaId}/segments/${segId}`, {
        text: editText,
        speaker_id: editSpeakerId === "none" ? null : editSpeakerId,
      }),
    onSuccess: () => {
      toast.success("Transcripción corregida y reindexada");
      setEditingId(null);
      qc.invalidateQueries({ queryKey: ["segments", caseId, mediaId] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error al guardar"),
  });

  async function onNewSpeaker(spk: { id: string }) {
    if (!editingId) return;
    await api.patch(`/cases/${caseId}/media/${mediaId}/segments/${editingId}`,
      { text: editText, speaker_id: spk.id });
    toast.success("Hablante creado y asignado a la cita");
    qc.invalidateQueries({ queryKey: ["segments", caseId, mediaId] });
    setEditingId(null);
  }

  function seekTo(ms: number) {
    const v = videoRef.current;
    if (v) { v.currentTime = ms / 1000; v.play().catch(() => {}); }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-6xl">
        <DialogHeader><DialogTitle>{filename}</DialogTitle></DialogHeader>
        <div className="grid min-w-0 gap-4 [&>*]:min-w-0 md:grid-cols-2">
          <div className="space-y-2">
            {videoUrl ? (
              <video
                ref={videoRef}
                src={videoUrl}
                controls
                preload="metadata"
                className="w-full rounded-md border"
                onTimeUpdate={(e) => setCurrentMs(e.currentTarget.currentTime * 1000)}
                onSeeked={(e) => setCurrentMs(e.currentTarget.currentTime * 1000)}
              />
            ) : (
              <p className="rounded-md border p-8 text-center text-sm text-muted-foreground">Video no disponible.</p>
            )}
            {activeId && <p className="text-xs text-muted-foreground">Reproduciendo el minuto {mmss(currentMs)}</p>}
          </div>

          <div className="space-y-2">
            <SpeakerTags
              caseId={caseId}
              speakers={speakers}
              unidentifiedId={data?.unknown_speaker_id ?? null}
              unidentifiedCount={(segments ?? []).filter((s) => data?.unknown_speaker_id && s.speaker_id === data.unknown_speaker_id).length}
              onRenamed={() => qc.invalidateQueries({ queryKey: ["segments", caseId, mediaId] })}
            />
            <div className="max-h-[520px] space-y-2 overflow-y-auto pr-1">
              {segments?.length ? segments.map((s) => {
              const active = s.id === activeId;
              return (
                <div
                  key={s.id}
                  ref={(el) => { segRefs.current[s.id] = el; }}
                  onClick={() => seekTo(s.start_ms)}
                  className={cn(
                    "cursor-pointer rounded-md border p-3 transition-colors",
                    active ? "border-primary bg-primary/10 ring-1 ring-primary" : "hover:bg-muted/50",
                  )}
                >
                  <div className="mb-1 flex items-center justify-between">
                    <span className={cn("text-xs font-medium", active ? "text-primary" : "text-muted-foreground")}>
                      {mmss(s.start_ms)}–{mmss(s.end_ms)} ·{" "}
                      <span className="font-semibold text-foreground">{s.speaker_name || s.speaker_label || "Sin identificar"}</span>
                      {s.needs_review && <Badge variant="destructive" className="ml-2">Revisar</Badge>}
                    </span>
                    <Button variant="ghost" size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditingId(s.id);
                        setEditText(s.text);
                        setEditSpeakerId(s.speaker_id ?? unidentified);
                      }}>
                      Editar
                    </Button>
                  </div>
                  {editingId === s.id ? (
                    <div className="space-y-2" onClick={(e) => e.stopPropagation()}>
                      <Select value={editSpeakerId}
                        onValueChange={(v) => { if (v === "__new__") setNewSpeakerOpen(true); else setEditSpeakerId(v); }}>
                        <SelectTrigger className="h-9">
                          <SelectValue placeholder="¿Quién lo dijo?" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value={unidentified}>Sin identificar</SelectItem>
                          {speakers.map((spk) => (
                            <SelectItem key={spk.id} value={spk.id}>
                              {spk.display_name || spk.label}
                            </SelectItem>
                          ))}
                          <SelectItem value="__new__">➕ Nuevo hablante…</SelectItem>
                        </SelectContent>
                      </Select>
                      <Textarea value={editText} onChange={(e) => setEditText(e.target.value)} rows={3} />
                      <div className="flex gap-2">
                        <Button size="sm" onClick={() => save.mutate(s.id)} disabled={save.isPending}>
                          <Save className="mr-1 h-4 w-4" />Guardar
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => setEditingId(null)}>Cancelar</Button>
                      </div>
                    </div>
                  ) : (<p className="text-sm">{s.text}</p>)}
                </div>
              );
            }) : (
              <p className="text-sm text-muted-foreground">
                {processingStatus && ["UPLOADED", "QUEUED", "OCR_RUNNING", "ASR_RUNNING"].includes(processingStatus)
                  ? "Transcribiendo (ASR) en curso… la transcripción aparecerá aquí al terminar."
                  : processingStatus === "FAILED"
                    ? "La transcripción falló. Usa «Reprocesar»."
                    : "Sin segmentos de transcripción."}
              </p>
            )}
            </div>
          </div>
        </div>
        <SpeakerFormDialog
          caseId={caseId}
          open={newSpeakerOpen}
          onOpenChange={setNewSpeakerOpen}
          initialName=""
          onSaved={onNewSpeaker}
        />
      </DialogContent>
    </Dialog>
  );
}
