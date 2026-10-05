"use client";

/** Tags de los hablantes detectados en una transcripción.
 * Punto 1: edición GENERAL del nombre (speakers.display_name). Al guardar, el nombre
 * se actualiza en todos los segmentos que comparten ese hablante. */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Check, Pencil, X } from "lucide-react";
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
  speakers,
  onRenamed,
}: {
  speakers: Speaker[];
  /** Tras renombrar: refresca los segmentos para que el nuevo nombre aparezca en los textos. */
  onRenamed?: () => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");

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

  function start(spk: Speaker) {
    setEditingId(spk.id);
    setName(spk.display_name || spk.label);
  }

  if (!speakers.length) return null;

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 p-2">
      <span className="text-xs font-medium text-muted-foreground">Hablantes:</span>
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
    </div>
  );
}
