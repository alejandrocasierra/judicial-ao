"use client";

/** Modal ÚNICO de hablante: crear (desde el "+" de Hablantes o desde el select del segmento) o editar.
 *  Incluye nombre, rol (estándar o nuevo) y parte del proceso. */

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";

export interface Speaker {
  id: string;
  label: string;
  display_name?: string | null;
  speaker_role?: string | null;
  resolved_party_id?: string | null;
  resolution_status?: string;
  version: number;
}

interface Party { id: string; name: string }

export interface RoleSuggestion {
  speaker_id: string;
  label: string;
  display_name?: string | null;
  current_role?: string | null;
  suggested_role: string;
  mentions: number;
  filename?: string | null;
  page_number?: number | null;
  suggested_party_id?: string | null;
  suggested_party_name?: string | null;
  suggested_party_side?: string | null;
}

export const STANDARD_ROLES = ["Juez", "Magistrado", "Apoderado", "Abogado", "Fiscal", "Secretario",
  "Demandante", "Demandado", "Testigo", "Perito", "Parte"];
export const ROLE_KEY_LABEL: Record<string, string> = {
  juez: "Juez", apoderado: "Apoderado", testigo: "Testigo", perito: "Perito",
  secretario: "Secretario", fiscal: "Fiscal", parte: "Parte",
};
const CUSTOM = "__custom__";
const NONE = "__none__";

export function normTokens(s: string): string[] {
  return (s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .toUpperCase().split(/[^A-Z0-9]+/).filter((t) => t.length >= 3);
}

export function suggestFor(name: string, list: RoleSuggestion[]): RoleSuggestion | undefined {
  const toks = new Set(normTokens(name));
  if (!toks.size) return undefined;
  let best: RoleSuggestion | undefined;
  for (const s of list) {
    const b = new Set(normTokens(s.display_name || s.label));
    if (!b.size) continue;
    const inter = [...toks].filter((x) => b.has(x)).length;
    const union = new Set([...toks, ...b]).size;
    const subset = [...toks].every((x) => b.has(x)) || [...b].every((x) => toks.has(x));
    if (subset || inter / union >= 0.5) {
      if (!best || s.mentions > best.mentions) best = s;
    }
  }
  return best;
}

export function SpeakerFormDialog({
  caseId,
  open,
  onOpenChange,
  speaker,
  initialName,
  initialRole,
  initialPartyId,
  onSaved,
}: {
  caseId: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  speaker?: Speaker | null;
  initialName?: string;
  initialRole?: string;
  initialPartyId?: string;
  onSaved?: (s: { id: string; display_name?: string | null; label?: string }) => void;
}) {
  const isEdit = !!speaker;
  const startRole = initialRole || speaker?.speaker_role || "";
  const [name, setName] = useState(initialName ?? speaker?.display_name ?? speaker?.label ?? "");
  const [role, setRole] = useState(startRole ? (STANDARD_ROLES.includes(startRole) ? startRole : CUSTOM) : NONE);
  const [customRole, setCustomRole] = useState(startRole && !STANDARD_ROLES.includes(startRole) ? startRole : "");
  const [partyId, setPartyId] = useState(initialPartyId || speaker?.resolved_party_id || NONE);

  const partiesQ = useQuery({
    queryKey: ["parties", caseId],
    queryFn: () => api.get<Party[]>(`/cases/${caseId}/parties`),
    enabled: open,
  });
  const parties = partiesQ.data ?? [];

  const effectiveRole = role === CUSTOM ? customRole.trim() : (role === NONE ? "" : role);
  const canSubmit = name.trim().length > 0 && (role !== CUSTOM || customRole.trim().length > 0);

  const create = useMutation({
    mutationFn: () => api.post<{ id: string; display_name?: string | null; label: string }>(
      `/cases/${caseId}/speakers`,
      { display_name: name.trim(), speaker_role: effectiveRole || null,
        resolved_party_id: partyId === NONE ? null : partyId }),
    onSuccess: (s) => { toast.success("Hablante creado"); onOpenChange(false); onSaved?.(s); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo crear el hablante"),
  });
  const save = useMutation({
    mutationFn: () => api.patch(`/cases/${caseId}/speakers/${speaker!.id}`,
      { display_name: name.trim(), speaker_role: effectiveRole || null,
        resolved_party_id: partyId === NONE ? null : partyId }),
    onSuccess: () => { toast.success("Hablante actualizado"); onOpenChange(false); onSaved?.(speaker!); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo actualizar"),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader><DialogTitle>{isEdit ? "Editar hablante" : "Nuevo hablante"}</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <label className="text-xs font-medium">Nombre</label>
            <Input autoFocus placeholder="Nombre del hablante" value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && canSubmit) (isEdit ? save : create).mutate(); }} />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium">Rol</label>
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger className="h-9"><SelectValue placeholder="Sin rol" /></SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>Sin rol</SelectItem>
                {STANDARD_ROLES.map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}
                <SelectItem value={CUSTOM}>➕ Otro rol…</SelectItem>
              </SelectContent>
            </Select>
            {role === CUSTOM && (
              <Input className="h-9" placeholder="Nombre del rol (p. ej. Magistrado auxiliar)"
                value={customRole} onChange={(e) => setCustomRole(e.target.value)} />
            )}
            <p className="text-[11px] text-muted-foreground">
              El rol permite responder con exactitud «¿cuántos jueces/apoderados…?».
            </p>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium">Parte del proceso (opcional)</label>
            <Select value={partyId} onValueChange={setPartyId}>
              <SelectTrigger className="h-9"><SelectValue placeholder="Sin parte" /></SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>Sin parte</SelectItem>
                {parties.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="outline" onClick={() => onOpenChange(false)}>Cancelar</Button>
          <Button size="sm" disabled={!canSubmit || create.isPending || save.isPending}
            onClick={() => (isEdit ? save : create).mutate()}>{isEdit ? "Guardar" : "Crear"}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
