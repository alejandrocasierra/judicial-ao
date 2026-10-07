"use client";

/** Panel de PARTES del proceso: lista, agrega, edita y elimina partes y gestiona el
 *  catálogo de roles del caso, además de extraer candidatos de los autos (revisión humana). */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Plus, Sparkles, Users, Pencil, Trash2, Settings2, Check, X } from "lucide-react";
import { toast } from "sonner";

interface Party { id: string; name: string; role: string; entity_type: string; aliases?: string[] }
interface PartyRole { id: string; code: string; label: string; is_system: boolean; sort_order: number }
interface Candidate {
  name: string; normalized_name: string; role: string; entity_type: string;
  mentions: number; filename: string; page_number: number; folio?: string | null; document_id: string;
}
interface CreateResult { created: Array<{ id: string; name: string }> }

export function PartiesButton({ caseId }: { caseId: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Alta manual
  const [newName, setNewName] = useState("");
  const [newRole, setNewRole] = useState("third_party");
  // Edición en línea
  const [editId, setEditId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editRole, setEditRole] = useState("third_party");
  // Gestión de roles
  const [rolesOpen, setRolesOpen] = useState(false);
  const [newRoleLabel, setNewRoleLabel] = useState("");
  const [editRoleId, setEditRoleId] = useState<string | null>(null);
  const [editRoleLabel, setEditRoleLabel] = useState("");

  const partiesQ = useQuery({
    queryKey: ["parties", caseId],
    queryFn: () => api.get<Party[]>(`/cases/${caseId}/parties`),
    enabled: open,
  });
  const parties = partiesQ.data ?? [];

  const rolesQ = useQuery({
    queryKey: ["party-roles", caseId],
    queryFn: () => api.get<PartyRole[]>(`/cases/${caseId}/party-roles`),
    enabled: open,
  });
  const roles = rolesQ.data ?? [];
  const roleLabel = (code: string) => roles.find((r) => r.code === code)?.label || code;

  const invalidate = () => qc.invalidateQueries({ queryKey: ["parties", caseId] });

  const extract = useMutation({
    mutationFn: () => api.post<Candidate[]>(`/cases/${caseId}/parties/extract`, {}),
    onSuccess: (c) => {
      setCandidates(c);
      setSelected(new Set(c.map((x) => x.normalized_name)));
      if (!c.length) toast.info("No se detectaron partes en los documentos");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo extraer"),
  });

  const createSelected = useMutation({
    mutationFn: () => {
      const chosen = (candidates ?? []).filter((c) => selected.has(c.normalized_name))
        .map((c) => ({ name: c.name, role: c.role, entity_type: c.entity_type }));
      return api.post<CreateResult>(`/cases/${caseId}/parties`, { parties: chosen });
    },
    onSuccess: (r) => {
      toast.success(`Partes creadas: ${r?.created?.length ?? 0}`);
      invalidate();
      setCandidates(null);
      setSelected(new Set());
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudieron crear"),
  });

  const addParty = useMutation({
    mutationFn: () => api.post(`/cases/${caseId}/parties`, { parties: [{ name: newName.trim(), role: newRole }] }),
    onSuccess: () => { toast.success("Parte agregada"); invalidate(); setNewName(""); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo agregar"),
  });

  const editParty = useMutation({
    mutationFn: () => api.patch(`/cases/${caseId}/parties/${editId}`, { name: editName.trim(), role: editRole }),
    onSuccess: () => { toast.success("Parte actualizada"); invalidate(); setEditId(null); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo actualizar"),
  });

  const removeParty = useMutation({
    mutationFn: (id: string) => api.delete(`/cases/${caseId}/parties/${id}`),
    onSuccess: () => { toast.success("Parte eliminada"); invalidate(); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo eliminar"),
  });

  const createRole = useMutation({
    mutationFn: () => api.post(`/cases/${caseId}/party-roles`, { label: newRoleLabel.trim() }),
    onSuccess: () => { toast.success("Rol creado"); qc.invalidateQueries({ queryKey: ["party-roles", caseId] }); setNewRoleLabel(""); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo crear el rol"),
  });

  const renameRole = useMutation({
    mutationFn: () => api.patch(`/cases/${caseId}/party-roles/${editRoleId}`, { label: editRoleLabel.trim() }),
    onSuccess: () => { toast.success("Rol actualizado"); qc.invalidateQueries({ queryKey: ["party-roles", caseId] }); invalidate(); setEditRoleId(null); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo actualizar el rol"),
  });

  const removeRole = useMutation({
    mutationFn: (id: string) => api.delete(`/cases/${caseId}/party-roles/${id}`),
    onSuccess: () => { toast.success("Rol eliminado"); qc.invalidateQueries({ queryKey: ["party-roles", caseId] }); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo eliminar el rol"),
  });

  function toggle(norm: string) {
    setSelected((prev) => {
      const s = new Set(prev);
      if (s.has(norm)) s.delete(norm); else s.add(norm);
      return s;
    });
  }

  function startEdit(p: Party) {
    setEditId(p.id);
    setEditName(p.name);
    setEditRole(p.role);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline"><Users className="mr-2 h-4 w-4" />Partes</Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <div className="flex items-center justify-between gap-2">
            <DialogTitle>Partes del proceso</DialogTitle>
            <Button variant="ghost" size="sm" className="mr-6" onClick={() => setRolesOpen(true)}>
              <Settings2 className="mr-1 h-4 w-4" />Roles
            </Button>
          </div>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <h3 className="mb-2 text-sm font-semibold">Actuales ({parties.length})</h3>
            {parties.length ? (
              <ul className="space-y-2">
                {parties.map((p) => (
                  <li key={p.id} className="rounded-md border p-2">
                    {editId === p.id ? (
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                        <Input value={editName} onChange={(e) => setEditName(e.target.value)} className="sm:flex-1" />
                        <Select value={editRole} onValueChange={setEditRole}>
                          <SelectTrigger className="sm:w-44"><SelectValue /></SelectTrigger>
                          <SelectContent>
                            {roles.map((r) => <SelectItem key={r.id} value={r.code}>{r.label}</SelectItem>)}
                          </SelectContent>
                        </Select>
                        <div className="flex gap-1">
                          <Button size="icon" variant="ghost" title="Guardar"
                            onClick={() => editParty.mutate()} disabled={editParty.isPending || !editName.trim()}>
                            <Check className="h-4 w-4" />
                          </Button>
                          <Button size="icon" variant="ghost" title="Cancelar" onClick={() => setEditId(null)}>
                            <X className="h-4 w-4" />
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2">
                        <Badge variant="secondary">{roleLabel(p.role)}</Badge>
                        <span className="min-w-0 flex-1 break-words text-sm">{p.name}</span>
                        <span className="hidden text-xs text-muted-foreground sm:inline">
                          {p.entity_type === "organization" ? "organización" : "persona"}
                        </span>
                        <Button size="icon" variant="ghost" title="Editar" onClick={() => startEdit(p)}>
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button size="icon" variant="ghost" title="Eliminar"
                          onClick={() => removeParty.mutate(p.id)} disabled={removeParty.isPending}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">Aún no hay partes. Agrega una o extrae candidatos de los autos.</p>
            )}
          </div>

          {/* Alta manual */}
          <div className="rounded-md border bg-muted/30 p-3">
            <h3 className="mb-2 text-sm font-semibold">Agregar parte</h3>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <Input value={newName} onChange={(e) => setNewName(e.target.value)}
                placeholder="Nombre de la parte" className="sm:flex-1" />
              <Select value={newRole} onValueChange={setNewRole}>
                <SelectTrigger className="sm:w-44"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {roles.map((r) => <SelectItem key={r.id} value={r.code}>{r.label}</SelectItem>)}
                </SelectContent>
              </Select>
              <Button onClick={() => addParty.mutate()} disabled={addParty.isPending || !newName.trim()}>
                <Plus className="mr-1 h-4 w-4" />Agregar
              </Button>
            </div>
          </div>

          {/* Extracción */}
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => extract.mutate()} disabled={extract.isPending}>
              <Sparkles className="mr-1 h-4 w-4" />{extract.isPending ? "Buscando…" : "Extraer de documentos"}
            </Button>
            {candidates && (
              <Button size="sm" onClick={() => createSelected.mutate()} disabled={createSelected.isPending || selected.size === 0}>
                <Plus className="mr-1 h-4 w-4" />Crear seleccionadas ({selected.size})
              </Button>
            )}
          </div>

          {candidates && (
            <div className="max-h-72 overflow-y-auto rounded-md border">
              {candidates.length ? candidates.map((c) => (
                <label key={c.normalized_name} className="flex cursor-pointer items-center gap-2 border-b px-3 py-2 text-sm last:border-0">
                  <Checkbox checked={selected.has(c.normalized_name)} onCheckedChange={() => toggle(c.normalized_name)} />
                  <Badge variant="outline">{roleLabel(c.role)}</Badge>
                  <span className="font-medium">{c.name}</span>
                  <span className="text-xs text-muted-foreground">{c.mentions} menciones · {c.filename} p.{c.page_number}</span>
                </label>
              )) : <p className="p-3 text-sm text-muted-foreground">Sin candidatos.</p>}
            </div>
          )}
          <p className="text-[11px] text-muted-foreground">
            La extracción es heurística (encabezados de los autos): revisa antes de crear.
          </p>
        </div>
      </DialogContent>

      {/* Gestión de roles */}
      <Dialog open={rolesOpen} onOpenChange={setRolesOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Roles de parte</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Input value={newRoleLabel} onChange={(e) => setNewRoleLabel(e.target.value)}
                placeholder="Nuevo rol (p. ej. Curador ad litem)" />
              <Button onClick={() => createRole.mutate()} disabled={createRole.isPending || !newRoleLabel.trim()}>
                <Plus className="mr-1 h-4 w-4" />Crear
              </Button>
            </div>
            <ul className="max-h-72 space-y-1 overflow-y-auto">
              {roles.map((r) => (
                <li key={r.id} className="flex items-center gap-2 rounded-md border px-2 py-1.5">
                  {editRoleId === r.id ? (
                    <>
                      <Input value={editRoleLabel} onChange={(e) => setEditRoleLabel(e.target.value)} className="flex-1" />
                      <Button size="icon" variant="ghost" title="Guardar"
                        onClick={() => renameRole.mutate()} disabled={renameRole.isPending || !editRoleLabel.trim()}>
                        <Check className="h-4 w-4" />
                      </Button>
                      <Button size="icon" variant="ghost" title="Cancelar" onClick={() => setEditRoleId(null)}>
                        <X className="h-4 w-4" />
                      </Button>
                    </>
                  ) : (
                    <>
                      <span className="flex-1 text-sm">{r.label}</span>
                      <span className="text-xs text-muted-foreground">{r.code}</span>
                      <Button size="icon" variant="ghost" title="Renombrar"
                        onClick={() => { setEditRoleId(r.id); setEditRoleLabel(r.label); }}>
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button size="icon" variant="ghost" title="Eliminar"
                        onClick={() => removeRole.mutate(r.id)} disabled={removeRole.isPending}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </DialogContent>
      </Dialog>
    </Dialog>
  );
}
