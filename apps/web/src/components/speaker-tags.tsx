"use client";

/** Tags de los hablantes de una transcripción.
 * - "+" crea un hablante nuevo (mismo modal que el select del segmento).
 * - Lápiz = editar (nombre/rol/parte); Papelera = eliminar (sus citas quedan sin hablante).
 * - "Fusionar" une dos hablantes que son la misma persona.
 * - "Roles sugeridos" asigna de golpe el rol detectado en las firmas. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Merge, Pencil, Plus, Sparkles, Trash2, UserPlus } from "lucide-react";
import { toast } from "sonner";
import {
  SpeakerFormDialog, ROLE_KEY_LABEL, suggestFor,
  type Speaker, type RoleSuggestion,
} from "@/components/speaker-form-dialog";

export type { Speaker };

interface FormTarget { speaker?: Speaker; initialName?: string; initialRole?: string; initialPartyId?: string }

export function SpeakerTags({
  caseId,
  speakers,
  onRenamed,
  unidentifiedId,
  unidentifiedCount = 0,
}: {
  caseId: string;
  speakers: Speaker[];
  onRenamed?: () => void;
  unidentifiedId?: string | null;
  unidentifiedCount?: number;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<FormTarget | null>(null);
  const [reassignFrom, setReassignFrom] = useState<string | null>(null);
  const [reassignOpen, setReassignOpen] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [keepId, setKeepId] = useState<string | undefined>(undefined);
  const [mergeId, setMergeId] = useState<string | undefined>(undefined);

  const suggestionsQ = useQuery({
    queryKey: ["role-suggestions", caseId],
    queryFn: () => api.get<RoleSuggestion[]>(`/cases/${caseId}/speakers/role-suggestions`),
    enabled: !!caseId,
  });
  const suggestions = suggestionsQ.data ?? [];

  const merge = useMutation({
    mutationFn: ({ keep, merge: m }: { keep: string; merge: string }) =>
      api.post(`/cases/${caseId}/speakers/merge`, { keep_speaker_id: keep, merge_speaker_id: m }),
    onSuccess: () => {
      toast.success("Hablantes fusionados y reindexados");
      setMergeOpen(false); setKeepId(undefined); setMergeId(undefined);
      qc.invalidateQueries({ queryKey: ["role-suggestions", caseId] });
      onRenamed?.();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo fusionar"),
  });

  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/cases/${caseId}/speakers/${id}`),
    onSuccess: () => {
      toast.success("Hablante eliminado (sus citas quedan sin hablante)");
      qc.invalidateQueries({ queryKey: ["role-suggestions", caseId] });
      onRenamed?.();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo eliminar"),
  });

  const assignSuggested = useMutation({
    mutationFn: () =>
      api.post(`/cases/${caseId}/speakers/roles`, {
        assignments: suggestions.map((s) => ({
          speaker_id: s.speaker_id,
          speaker_role: ROLE_KEY_LABEL[s.suggested_role] || s.suggested_role,
          resolved_party_id: s.suggested_party_id || null,
        })),
      }),
    onSuccess: () => {
      toast.success("Roles sugeridos asignados");
      qc.invalidateQueries({ queryKey: ["role-suggestions", caseId] });
      onRenamed?.();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudieron asignar los roles"),
  });

  function openCreate() {
    setReassignFrom(null);
    setForm({});
  }
  function openEdit(spk: Speaker) {
    const sug = suggestFor(spk.display_name || spk.label, suggestions);
    const initialRole = spk.speaker_role || (sug ? ROLE_KEY_LABEL[sug.suggested_role] : "");
    const initialPartyId = spk.resolved_party_id || sug?.suggested_party_id || "";
    setForm({ speaker: spk, initialRole, initialPartyId });
  }
  function onSaved(spk?: { id: string }) {
    if (reassignFrom && spk) {
      // "Sin identificar" → nuevo hablante: se pasan TODAS sus citas al recién creado.
      merge.mutate({ keep: spk.id, merge: reassignFrom });
      setReassignFrom(null);
    } else {
      qc.invalidateQueries({ queryKey: ["role-suggestions", caseId] });
      onRenamed?.();
    }
    setForm(null);
  }

  const nameOf = (id?: string) => {
    const s = speakers.find((x) => x.id === id);
    return s ? s.display_name || s.label : "";
  };
  const canMerge = !!keepId && !!mergeId && keepId !== mergeId;

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 p-2">
      <span className="text-xs font-medium text-muted-foreground">Hablantes:</span>
      <Button size="icon" variant="outline" className="h-6 w-6 rounded-full" title="Nuevo hablante" onClick={openCreate}>
        <Plus className="h-3.5 w-3.5" />
      </Button>
      {unidentifiedId && unidentifiedCount > 0 && (
        <Button size="sm" variant="outline" className="h-7 gap-1"
          title="Poner nombre a las citas de «Sin identificar» (se reasignan todas)"
          onClick={() => setReassignOpen(true)}>
          <UserPlus className="h-3.5 w-3.5" />Sin identificar ({unidentifiedCount})
        </Button>
      )}
      {speakers.map((spk) => (
        <span key={spk.id} className="inline-flex items-center gap-1 rounded-md bg-muted/50 pr-1">
          <Badge variant="secondary" className="gap-1 border-0 bg-transparent">
            {spk.display_name || spk.label}
            {spk.speaker_role ? <span className="text-muted-foreground">· {spk.speaker_role}</span> : null}
          </Badge>
          <button type="button" title="Editar nombre, rol y parte" onClick={() => openEdit(spk)}>
            <Pencil className="h-3 w-3 hover:text-foreground" />
          </button>
          <button type="button" title="Eliminar hablante (sus citas quedan sin hablante)"
            onClick={() => { if (confirm(`¿Eliminar a «${spk.display_name || spk.label}»? Sus citas quedarán sin hablante.`)) del.mutate(spk.id); }}>
            <Trash2 className="h-3 w-3 text-destructive hover:opacity-80" />
          </button>
        </span>
      ))}
      <div className="ml-auto flex items-center gap-2">
        {suggestions.length > 0 && (
          <Button size="sm" variant="outline" className="h-7 gap-1" disabled={assignSuggested.isPending}
            title="Asignar a cada hablante el rol detectado en las firmas de los documentos"
            onClick={() => assignSuggested.mutate()}>
            <Sparkles className="h-3.5 w-3.5" />Roles sugeridos ({suggestions.length})
          </Button>
        )}
        {speakers.length >= 2 && (
          <Button size="sm" variant="outline" className="h-7 gap-1" onClick={() => setMergeOpen(true)}>
            <Merge className="h-3.5 w-3.5" />Fusionar
          </Button>
        )}
      </div>

      {form && (
        <SpeakerFormDialog
          key={form.speaker?.id || "new"}
          caseId={caseId}
          open
          onOpenChange={(o) => { if (!o) setForm(null); }}
          speaker={form.speaker}
          initialName={form.initialName}
          initialRole={form.initialRole}
          initialPartyId={form.initialPartyId}
          onSaved={onSaved}
        />
      )}

      <Dialog open={reassignOpen} onOpenChange={setReassignOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader><DialogTitle>Reasignar «Sin identificar» ({unidentifiedCount})</DialogTitle></DialogHeader>
          <p className="text-sm text-muted-foreground">
            Elige un hablante existente o crea uno nuevo. Se reasignan <b>todas</b> las citas de «Sin identificar».
          </p>
          <Select value={undefined}
            onValueChange={(v) => {
              setReassignOpen(false);
              if (v === "__new__") { setReassignFrom(unidentifiedId ?? null); setForm({}); }
              else if (unidentifiedId) { merge.mutate({ keep: v, merge: unidentifiedId }); }
            }}>
            <SelectTrigger className="h-9"><SelectValue placeholder="Reasignar a…" /></SelectTrigger>
            <SelectContent>
              {speakers.map((spk) => (
                <SelectItem key={spk.id} value={spk.id}>{spk.display_name || spk.label}</SelectItem>
              ))}
              <SelectItem value="__new__">➕ Nuevo hablante…</SelectItem>
            </SelectContent>
          </Select>
        </DialogContent>
      </Dialog>

      <Dialog open={mergeOpen} onOpenChange={setMergeOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>Fusionar hablantes</DialogTitle></DialogHeader>
          <p className="text-sm text-muted-foreground">
            Úsalo cuando la misma persona aparezca dos veces (dos clusters de diarización). Los segmentos del
            hablante duplicado pasan al que conservas y el duplicado se elimina.
          </p>
          <div className="space-y-2">
            <label className="text-xs font-medium">Conservar</label>
            <Select value={keepId} onValueChange={setKeepId}>
              <SelectTrigger className="h-9"><SelectValue placeholder="Hablante que se conserva" /></SelectTrigger>
              <SelectContent>
                {speakers.map((spk) => (
                  <SelectItem key={spk.id} value={spk.id}>{spk.display_name || spk.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <label className="text-xs font-medium">Fusionar (se elimina)</label>
            <Select value={mergeId} onValueChange={setMergeId}>
              <SelectTrigger className="h-9"><SelectValue placeholder="Hablante duplicado" /></SelectTrigger>
              <SelectContent>
                {speakers.filter((spk) => spk.id !== keepId).map((spk) => (
                  <SelectItem key={spk.id} value={spk.id}>{spk.display_name || spk.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {canMerge && (
            <p className="text-xs text-muted-foreground">Se fusionará <b>{nameOf(mergeId)}</b> en <b>{nameOf(keepId)}</b>.</p>
          )}
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="outline" onClick={() => setMergeOpen(false)}>Cancelar</Button>
            <Button size="sm" disabled={!canMerge || merge.isPending}
              onClick={() => canMerge && merge.mutate({ keep: keepId!, merge: mergeId! })}>
              <Merge className="mr-1 h-4 w-4" />Fusionar
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
