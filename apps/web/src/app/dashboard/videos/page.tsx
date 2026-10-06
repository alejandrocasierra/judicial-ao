"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SpeakerTags, type Speaker } from "@/components/speaker-tags";
import { SpeakerFormDialog } from "@/components/speaker-form-dialog";
import { Pagination } from "@/components/pagination";
import { Video, Search, Save, Play } from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

interface Case { id: string; case_number: string; title: string; }
interface Media { id: string; filename: string; title?: string; duration_ms?: number; processing_status: string; segments: number; }
interface Segment { id: string; start_ms: number; end_ms: number; text: string; confidence: number; needs_review: boolean; speaker_id?: string | null; speaker_label?: string; speaker_name?: string | null; }

function mmss(ms: number) {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export default function VideosPage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [caseId, setCaseId] = useState("");
  const [open, setOpen] = useState(false);
  const [media, setMedia] = useState<Media | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [editSpeakerId, setEditSpeakerId] = useState("none");
  const [newSpeakerOpen, setNewSpeakerOpen] = useState(false);
  const [currentMs, setCurrentMs] = useState(0);
  const [listPage, setListPage] = useState(1);
  const [listPageSize, setListPageSize] = useState(20);

  const videoRef = useRef<HTMLVideoElement>(null);
  const segRefs = useRef<Record<string, HTMLDivElement | null>>({});

  const { data: cases = [] } = useQuery({ queryKey: ["cases"], queryFn: () => api.get<Case[]>("/cases") });
  // Expediente activo derivado: el elegido o el primero (sin efecto de estado).
  const activeCaseId = caseId || cases[0]?.id || "";

  const { data: mediaList = [], isLoading } = useQuery({
    queryKey: ["media", activeCaseId],
    queryFn: () => api.get<Media[]>(`/cases/${activeCaseId}/media`),
    enabled: !!activeCaseId,
  });
  const { data: segmentsData, refetch } = useQuery({
    queryKey: ["segments", activeCaseId, media?.id],
    queryFn: () => api.get<{ segments: Segment[]; speakers: Speaker[]; unknown_speaker_id?: string | null }>(`/cases/${activeCaseId}/media/${media!.id}/segments`),
    enabled: !!media,
  });
  const segData = segmentsData?.segments;
  const speakers = segmentsData?.speakers ?? [];
  const unidentified = segmentsData?.unknown_speaker_id ?? "none";

  useEffect(() => {
    let revoke: string | null = null;
    if (media) {
      api.blob(`/cases/${activeCaseId}/media/${media.id}/download`)
        .then((b) => { const u = URL.createObjectURL(b); revoke = u; setVideoUrl(u); })
        .catch(() => setVideoUrl(null));
    }
    return () => { if (revoke) URL.revokeObjectURL(revoke); };
  }, [activeCaseId, media]);

  // Segmento activo según el tiempo actual del video.
  const activeId = useMemo(() => {
    if (!segData) return null;
    return segData.find((s) => currentMs >= s.start_ms && currentMs < s.end_ms)?.id ?? null;
  }, [segData, currentMs]);

  // Scroll automático en Y hacia el segmento activo, resaltándolo.
  useEffect(() => {
    if (!activeId) return;
    const el = segRefs.current[activeId];
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [activeId]);

  const save = useMutation({
    mutationFn: (segId: string) =>
      api.patch(`/cases/${activeCaseId}/media/${media!.id}/segments/${segId}`, {
        text: editText,
        speaker_id: editSpeakerId === "none" ? null : editSpeakerId,
      }),
    onSuccess: () => {
      toast.success("Transcripción corregida y reindexada");
      setEditingId(null);
      qc.invalidateQueries({ queryKey: ["segments", activeCaseId, media?.id] });
      refetch();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error"),
  });

  async function onNewSpeaker(spk: { id: string }) {
    if (!editingId || !media) return;
    await api.patch(`/cases/${activeCaseId}/media/${media.id}/segments/${editingId}`,
      { text: editText, speaker_id: spk.id });
    toast.success("Hablante creado y asignado a la cita");
    qc.invalidateQueries({ queryKey: ["segments", activeCaseId, media.id] });
    refetch();
    setEditingId(null);
  }

  function openMedia(m: Media) { setMedia(m); setCurrentMs(0); setOpen(true); }
  function seekTo(ms: number) {
    const v = videoRef.current;
    if (v) { v.currentTime = ms / 1000; v.play().catch(() => {}); }
  }

  const filtered = mediaList
    .filter((m) => (m.title || m.filename).toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => (b.segments || 0) - (a.segments || 0));
  const pageItems = filtered.slice((listPage - 1) * listPageSize, listPage * listPageSize);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Videos</h1>
          <p className="text-muted-foreground">Reproduce la audiencia y revisa quién dijo qué y en qué minuto</p>
        </div>
        <Select value={activeCaseId} onValueChange={(v) => { setCaseId(v); setListPage(1); }}>
          <SelectTrigger className="w-80"><SelectValue placeholder="Selecciona expediente" /></SelectTrigger>
          <SelectContent>{cases.map((c) => <SelectItem key={c.id} value={c.id}>{c.case_number} — {c.title}</SelectItem>)}</SelectContent>
        </Select>
      </div>

      <Card>
        <CardHeader>
          <div className="relative"><Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input placeholder="Buscar video…" className="pl-8" value={search} onChange={(e) => { setSearch(e.target.value); setListPage(1); }} /></div>
        </CardHeader>
        <CardContent>
          {isLoading ? <p className="text-muted-foreground">Cargando…</p> : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12"><Video className="mb-4 h-12 w-12 text-muted-foreground" />
              <p className="text-muted-foreground">No se encontraron videos</p></div>
          ) : (
            <>
              <div className="space-y-2">
                {pageItems.map((m) => (
                  <div key={m.id} className="flex items-center justify-between rounded-md border p-3 hover:bg-muted/50">
                    <div className="flex items-center gap-3">
                      <Video className="h-5 w-5 text-muted-foreground" />
                      <div>
                        <p className="font-medium">{m.title || m.filename}</p>
                        <p className="text-sm text-muted-foreground">{m.duration_ms ? mmss(m.duration_ms) : "—"} · {m.segments} segmentos · {m.processing_status}</p>
                      </div>
                    </div>
                    <Button variant="outline" size="sm" onClick={() => openMedia(m)}><Play className="mr-2 h-4 w-4" />Ver transcripción</Button>
                  </div>
                ))}
              </div>
              <Pagination page={listPage} pageSize={listPageSize} total={filtered.length}
                          onPage={setListPage} onPageSize={setListPageSize} />
            </>
          )}
        </CardContent>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-6xl">
          <DialogHeader><DialogTitle>{media?.title || media?.filename}</DialogTitle></DialogHeader>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              {videoUrl ? (
                <video
                  ref={videoRef}
                  src={videoUrl}
                  controls
                  className="w-full rounded-md border"
                  onTimeUpdate={(e) => setCurrentMs(e.currentTarget.currentTime * 1000)}
                  onSeeked={(e) => setCurrentMs(e.currentTarget.currentTime * 1000)}
                />
              ) : <p className="rounded-md border p-8 text-center text-sm text-muted-foreground">Video no disponible.</p>}
              {activeId && <p className="text-xs text-muted-foreground">Reproduciendo el minuto {mmss(currentMs)}</p>}
            </div>

            <div className="space-y-2">
              <SpeakerTags
                caseId={activeCaseId}
                speakers={speakers}
                onRenamed={() => { qc.invalidateQueries({ queryKey: ["segments", activeCaseId, media?.id] }); refetch(); }}
              />
              <div className="max-h-[520px] space-y-2 overflow-y-auto pr-1">
              {segData?.length ? segData.map((s) => {
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
                        {mmss(s.start_ms)}–{mmss(s.end_ms)} · <span className="font-semibold text-foreground">{s.speaker_name || s.speaker_label || "Sin identificar"}</span> {s.needs_review && <Badge variant="destructive" className="ml-2">Revisar</Badge>}
                      </span>
                      <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); setEditingId(s.id); setEditText(s.text); setEditSpeakerId(s.speaker_id ?? unidentified); }}>Editar</Button>
                    </div>
                    {editingId === s.id ? (
                      <div className="space-y-2" onClick={(e) => e.stopPropagation()}>
                        <Select value={editSpeakerId}
                          onValueChange={(v) => { if (v === "__new__") setNewSpeakerOpen(true); else setEditSpeakerId(v); }}>
                          <SelectTrigger className="h-9"><SelectValue placeholder="¿Quién lo dijo?" /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value={unidentified}>Sin identificar</SelectItem>
                            {speakers.map((spk) => (
                              <SelectItem key={spk.id} value={spk.id}>{spk.display_name || spk.label}</SelectItem>
                            ))}
                            <SelectItem value="__new__">➕ Nuevo hablante…</SelectItem>
                          </SelectContent>
                        </Select>
                        <Textarea value={editText} onChange={(e) => setEditText(e.target.value)} rows={3} />
                        <div className="flex gap-2">
                          <Button size="sm" onClick={() => save.mutate(s.id)} disabled={save.isPending}><Save className="mr-1 h-4 w-4" />Guardar</Button>
                          <Button size="sm" variant="outline" onClick={() => setEditingId(null)}>Cancelar</Button>
                        </div>
                      </div>
                    ) : (<p className="text-sm">{s.text}</p>)}
                  </div>
                );
              }) : <p className="text-sm text-muted-foreground">Sin segmentos de transcripción.</p>}
              </div>
            </div>
          </div>
        </DialogContent>
        <SpeakerFormDialog
          caseId={activeCaseId}
          open={newSpeakerOpen}
          onOpenChange={setNewSpeakerOpen}
          initialName=""
          onSaved={onNewSpeaker}
        />
      </Dialog>
    </div>
  );
}
