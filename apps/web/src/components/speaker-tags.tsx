"use client";

/** Tags de los hablantes detectados en una transcripción.
 * - Edición GENERAL del nombre (speakers.display_name): al guardar, el nombre se actualiza
 *   en todos los segmentos que comparten ese hablante.
 * - Fusionar dos hablantes que son la misma persona (dos clusters de diarización):
 *   reasigna los segmentos del duplicado y lo elimina (reindexa pgvector y el grafo). */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Check, Merge, Pencil, Plus, X } from "lucide-react";
import { toast } from "sonner";

export interface Speaker {
  id: string;
  label: string;
  display_name?: string | null;
  speaker_role?: string | null;
  resolution_status?: string;
  version: number;
}

export function SpeakerTags({
  caseId,
  speakers,
  onRenamed,
}: {
  caseId: string;
  speakers: Speaker[];
  /** Tras renombrar/fusionar: refresca los segmentos para que el nuevo nombre aparezca en los textos. */
  onRenamed?: () => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [mergeOpen, setMergeOpen] = useState(false);
  const [keepId, setKeepId] = useState<string | undefined>(undefined);
  const [mergeId, setMergeId] = useState<string | undefined>(undefined);
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");

  const rename = useMutation({
    mutationFn: (spk: Speaker) =>
      api.post(`/review/${spk.id}`, {
        entity_type: "speaker",
        action: "EDIT",
        expected_version: spk.version,
        changes: { display_name: name.trim() },
        reason: "Edición general del nombre del hablante",
      }),
    onSuccess: () => {
      toast.success("Nombre del hablante actualizado");
      setEditingId(null);
      onRenamed?.();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo renombrar"),
  });

  const merge = useMutation({
    mutationFn: () =>
      api.post(`/cases/${caseId}/speakers/merge`, { keep_speaker_id: keepId, merge_speaker_id: mergeId }),
    onSuccess: () => {
      toast.success("Hablantes fusionados y reindexados");
      setMergeOpen(false);
      setKeepId(undefined);
      setMergeId(undefined);
      onRenamed?.();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo fusionar"),
  });

  // Alta de un hablante nuevo (aparece en los tags y en el selector de "quién lo dijo").
  const create = useMutation({
    mutationFn: () => api.post(`/cases/${caseId}/speakers`, { display_name: newName.trim() }),
    onSuccess: () => {
      toast.success("Hablante creado");
      setCreateOpen(false);
      setNewName("");
      onRenamed?.();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo crear el hablante"),
  });

  function start(spk: Speaker) {
    setEditingId(spk.id);
    setName(spk.display_name || spk.label);
  }

  if (!speakers.length) return null;
  const nameOf = (id?: string) => {
    const s = speakers.find((x) => x.id === id);
    return s ? s.display_name || s.label : "";
  };
  const canMerge = !!keepId && !!mergeId && keepId !== mergeId;

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 p-2">
      <span className="text-xs font-medium text-muted-foreground">Hablantes:</span>
      <Button
        size="icon"
        variant="outline"
        className="h-6 w-6 rounded-full"
        title="Nuevo hablante"
        onClick={() => setCreateOpen(true)}
      >
        <Plus className="h-3.5 w-3.5" />
      </Button>
      {speakers.map((spk) =>
        editingId === spk.id ? (
          <span key={spk.id} className="flex items-center gap-1">
            <Input
              autoFocus
              value={name}
              className="h-7 w-44"
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && name.trim()) rename.mutate(spk);
                if (e.key === "Escape") setEditingId(null);
              }}
            />
            <Button
              size="icon"
              variant="ghost"
              className="h-7 w-7"
              title="Guardar"
              disabled={rename.isPending || !name.trim()}
              onClick={() => rename.mutate(spk)}
            >
              <Check className="h-4 w-4" />
            </Button>
            <Button size="icon" variant="ghost" className="h-7 w-7" title="Cancelar" onClick={() => setEditingId(null)}>
              <X className="h-4 w-4" />
            </Button>
          </span>
        ) : (
          <button key={spk.id} type="button" title="Cambiar el nombre en toda la transcripción" onClick={() => start(spk)}>
            <Badge variant="secondary" className="cursor-pointer gap-1 hover:bg-muted">
              {spk.display_name || spk.label}
              <Pencil className="h-3 w-3" />
            </Badge>
          </button>
        ),
      )}
      {speakers.length >= 2 && (
        <Button size="sm" variant="outline" className="ml-auto h-7 gap-1" onClick={() => setMergeOpen(true)}>
          <Merge className="h-3.5 w-3.5" />
          Fusionar
        </Button>
      )}

      <Dialog open={mergeOpen} onOpenChange={setMergeOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Fusionar hablantes</DialogTitle>
          </DialogHeader>
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
            <p className="text-xs text-muted-foreground">
              Se fusionará <b>{nameOf(mergeId)}</b> en <b>{nameOf(keepId)}</b>.
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="outline" onClick={() => setMergeOpen(false)}>Cancelar</Button>
            <Button size="sm" disabled={!canMerge || merge.isPending} onClick={() => merge.mutate()}>
              <Merge className="mr-1 h-4 w-4" />Fusionar
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Nuevo hablante</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Crea un hablante que la diarización no detectó; quedará en la lista de hablantes y en el selector
            de «quién lo dijo».
          </p>
          <Input
            autoFocus
            placeholder="Nombre del hablante"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && newName.trim()) create.mutate(); }}
          />
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="outline" onClick={() => setCreateOpen(false)}>Cancelar</Button>
            <Button size="sm" disabled={!newName.trim() || create.isPending} onClick={() => create.mutate()}>
              <Plus className="mr-1 h-4 w-4" />Crear
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
