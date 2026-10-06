"use client";

/** Tags de los hablantes de una transcripción.
 * - "+" crea un hablante nuevo (nombre + rol opcional + parte) → aparece en los tags y en el selector.
 * - El lápiz edita nombre, rol y parte (el rol CONFIRMADO hace exacto el conteo de «¿cuántos jueces?»).
 * - "Fusionar" une dos hablantes que son la misma persona (reasigna segmentos y borra el duplicado). */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Merge, Pencil, Plus, Sparkles } from "lucide-react";
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

interface Party {
  id: string;
  name: string;
  role?: string | null;
}

const STANDARD_ROLES = ["Juez", "Magistrado", "Apoderado", "Abogado", "Fiscal", "Secretario",
  "Demandante", "Demandado", "Testigo", "Perito", "Parte"];
const CUSTOM = "__custom__";
const NONE = "__none__";

const ROLE_KEY_LABEL: Record<string, string> = {
  juez: "Juez", apoderado: "Apoderado", testigo: "Testigo", perito: "Perito",
  secretario: "Secretario", fiscal: "Fiscal", parte: "Parte",
};

interface RoleSuggestion {
  speaker_id: string;
  label: string;
  display_name?: string | null;
  current_role?: string | null;
  current_party_id?: string | null;
  suggested_role: string;
  mentions: number;
  filename?: string | null;
  page_number?: number | null;
  suggested_party_id?: string | null;
  suggested_party_name?: string | null;
  suggested_party_side?: string | null;
}

function normTokens(s: string): string[] {
  return (s || "")
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .toUpperCase().split(/[^A-Z0-9]+/).filter((t) => t.length >= 3);
}

function similar(a: string[], b: string[]): boolean {
  const A = new Set(a), B = new Set(b);
  if (!A.size || !B.size) return false;
  const inter = [...A].filter((x) => B.has(x)).length;
  const union = new Set([...A, ...B]).size;
  return a.every((x) => B.has(x)) || b.every((x) => A.has(x)) || inter / union >= 0.5;
}

function suggestFor(name: string, list: RoleSuggestion[]): RoleSuggestion | undefined {
  const toks = normTokens(name);
  let best: RoleSuggestion | undefined;
  for (const s of list) {
    if (similar(toks, normTokens(s.display_name || s.label)) && (!best || s.mentions > best.mentions)) best = s;
  }
  return best;
}

export function SpeakerTags({
  caseId,
  speakers,
  onRenamed,
}: {
  caseId: string;
  speakers: Speaker[];
  /** Tras crear/editar/fusionar: refresca los segmentos (y por tanto los hablantes). */
  onRenamed?: () => void;
}) {
  const [formOpen, setFormOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState<string>(NONE);
  const [customRole, setCustomRole] = useState("");
  const [partyId, setPartyId] = useState<string>(NONE);

  const [mergeOpen, setMergeOpen] = useState(false);
  const [keepId, setKeepId] = useState<string | undefined>(undefined);
  const [mergeId, setMergeId] = useState<string | undefined>(undefined);

  const partiesQ = useQuery({
    queryKey: ["parties", caseId],
    queryFn: () => api.get<Party[]>(`/cases/${caseId}/parties`),
    enabled: !!caseId,
  });
  const parties = partiesQ.data ?? [];
  const qc = useQueryClient();
  const suggestionsQ = useQuery({
    queryKey: ["role-suggestions", caseId],
    queryFn: () => api.get<RoleSuggestion[]>(`/cases/${caseId}/speakers/role-suggestions`),
    enabled: !!caseId,
  });
  const suggestions = suggestionsQ.data ?? [];
  const suggestion = name.trim() ? suggestFor(name, suggestions) : undefined;

  const effectiveRole = role === CUSTOM ? customRole.trim() : (role === NONE ? "" : role);

  const create = useMutation({
    mutationFn: () =>
      api.post(`/cases/${caseId}/speakers`, {
        display_name: name.trim(),
        speaker_role: effectiveRole || null,
        resolved_party_id: partyId === NONE ? null : partyId,
      }),
    onSuccess: () => { toast.success("Hablante creado"); closeForm(); qc.invalidateQueries({ queryKey: ["role-suggestions", caseId] }); onRenamed?.(); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo crear el hablante"),
  });

  const save = useMutation({
    mutationFn: () =>
      api.patch(`/cases/${caseId}/speakers/${editId}`, {
        display_name: name.trim(),
        speaker_role: effectiveRole || null,
        resolved_party_id: partyId === NONE ? null : partyId,
      }),
    onSuccess: () => { toast.success("Hablante actualizado"); closeForm(); qc.invalidateQueries({ queryKey: ["role-suggestions", caseId] }); onRenamed?.(); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo actualizar"),
  });

  const merge = useMutation({
    mutationFn: () =>
      api.post(`/cases/${caseId}/speakers/merge`, { keep_speaker_id: keepId, merge_speaker_id: mergeId }),
    onSuccess: () => {
      toast.success("Hablantes fusionados y reindexados");
      setMergeOpen(false); setKeepId(undefined); setMergeId(undefined); onRenamed?.();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo fusionar"),
  });

  // Asigna de golpe el rol sugerido a todos los hablantes detectados en las firmas.
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

  function closeForm() {
    setFormOpen(false); setEditId(null); setName(""); setRole(NONE); setCustomRole(""); setPartyId(NONE);
  }
  function openCreate() {
    closeForm(); setFormOpen(true);
  }
  function openEdit(spk: Speaker) {
    const r = spk.speaker_role || "";
    const nm = spk.display_name || spk.label;
    const sug = suggestFor(nm, suggestions);
    const sugLabel = sug ? ROLE_KEY_LABEL[sug.suggested_role] : "";
    setEditId(spk.id);
    setName(nm);
    setRole(r ? (STANDARD_ROLES.includes(r) ? r : CUSTOM) : (sugLabel || NONE));
    setCustomRole(r && !STANDARD_ROLES.includes(r) ? r : "");
    setPartyId(spk.resolved_party_id || sug?.suggested_party_id || NONE);
    setFormOpen(true);
  }

  const nameOf = (id?: string) => {
    const s = speakers.find((x) => x.id === id);
    return s ? s.display_name || s.label : "";
  };
  const canMerge = !!keepId && !!mergeId && keepId !== mergeId;
  const canSubmit = name.trim().length > 0 && (role !== CUSTOM || customRole.trim().length > 0);
  const isEdit = !!editId;

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 p-2">
      <span className="text-xs font-medium text-muted-foreground">Hablantes:</span>
      <Button size="icon" variant="outline" className="h-6 w-6 rounded-full" title="Nuevo hablante" onClick={openCreate}>
        <Plus className="h-3.5 w-3.5" />
      </Button>
      {speakers.map((spk) => (
        <button key={spk.id} type="button" title="Editar nombre, rol y parte"
          onClick={() => openEdit(spk)}>
          <Badge variant="secondary" className="cursor-pointer gap-1 hover:bg-muted">
            {spk.display_name || spk.label}
            {spk.speaker_role ? <span className="text-muted-foreground">· {spk.speaker_role}</span> : null}
            <Pencil className="h-3 w-3" />
          </Badge>
        </button>
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

      <Dialog open={formOpen} onOpenChange={(o) => { if (!o) closeForm(); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{isEdit ? "Editar hablante" : "Nuevo hablante"}</DialogTitle>
          </DialogHeader>
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
              {suggestion && (
                <p className="text-[11px] text-muted-foreground">
                  Sugerido: <b>{ROLE_KEY_LABEL[suggestion.suggested_role] || suggestion.suggested_role}</b>
                  {" "}— {suggestion.mentions} menciones
                  {suggestion.filename ? ` (${suggestion.filename} p.${suggestion.page_number})` : ""}
                  {role === NONE && (
                    <button type="button" className="ml-1 underline"
                      onClick={() => setRole(ROLE_KEY_LABEL[suggestion.suggested_role] || NONE)}>usar</button>
                  )}
                </p>
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
              {suggestion?.suggested_party_id && (
                <p className="text-[11px] text-muted-foreground">
                  Sugerido: <b>{suggestion.suggested_party_name}</b> para este hablante.
                </p>
              )}
              {!suggestion?.suggested_party_id && suggestion?.suggested_party_side && (
                <p className="text-[11px] text-muted-foreground">
                  Parte probable: <b>{suggestion.suggested_party_side}</b> (aún no hay partes cargadas).
                </p>
              )}
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="outline" onClick={closeForm}>Cancelar</Button>
            <Button size="sm" disabled={!canSubmit || create.isPending || save.isPending}
              onClick={() => (isEdit ? save : create).mutate()}>
              {isEdit ? "Guardar" : "Crear"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

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
            <p className="text-xs text-muted-foreground">Se fusionará <b>{nameOf(mergeId)}</b> en <b>{nameOf(keepId)}</b>.</p>
          )}
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="outline" onClick={() => setMergeOpen(false)}>Cancelar</Button>
            <Button size="sm" disabled={!canMerge || merge.isPending} onClick={() => merge.mutate()}>
              <Merge className="mr-1 h-4 w-4" />Fusionar
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
