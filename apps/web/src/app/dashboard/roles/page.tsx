"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Shield, Plus, Trash2, Pencil, Lock } from "lucide-react";
import { toast } from "sonner";

interface Role { id: string; code: string; name: string; description: string; permissions: string[]; is_system: boolean; }

export default function RolesPage() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Role | null>(null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [permissions, setPermissions] = useState<string[]>([]);

  const { data: roles = [] } = useQuery({ queryKey: ["roles"], queryFn: () => api.get<Role[]>("/admin/roles") });
  const { data: allPerms = [] } = useQuery({ queryKey: ["permissions"], queryFn: () => api.get<{ permissions: string[] }>("/admin/permissions").then((r) => r.permissions) });

  const save = useMutation({
    mutationFn: () => editing
      ? api.patch(`/admin/roles/${editing.id}`, { name, description, permissions })
      : api.post("/admin/roles", { code, name, description, permissions }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["roles"] }); setOpen(false); toast.success("Rol guardado"); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error"),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/admin/roles/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["roles"] }); toast.success("Rol eliminado"); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error"),
  });

  function openNew() { setEditing(null); setCode(""); setName(""); setDescription(""); setPermissions([]); setOpen(true); }
  function openEdit(r: Role) { setEditing(r); setCode(r.code); setName(r.name); setDescription(r.description); setPermissions(r.permissions || []); setOpen(true); }
  function toggle(p: string) { setPermissions((s) => (s.includes(p) ? s.filter((x) => x !== p) : [...s, p])); }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Roles y Permisos</h1>
          <p className="text-muted-foreground">Los roles de sistema son fijos; crea roles personalizados con sus permisos</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild><Button onClick={openNew}><Plus className="mr-2 h-4 w-4" />Nuevo rol</Button></DialogTrigger>
          <DialogContent className="max-w-2xl">
            <DialogHeader><DialogTitle>{editing ? "Editar rol" : "Crear rol"}</DialogTitle></DialogHeader>
            <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }} className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2">
                <div><label className="text-sm font-medium">Código</label>
                  <Input value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} disabled={!!editing}
                    pattern="[A-Z][A-Z0-9_]*" required placeholder="PARALEGAL" /></div>
                <div><label className="text-sm font-medium">Nombre</label>
                  <Input value={name} onChange={(e) => setName(e.target.value)} required placeholder="Paralegal" /></div>
              </div>
              <div><label className="text-sm font-medium">Descripción</label>
                <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} /></div>
              <div>
                <label className="text-sm font-medium">Permisos</label>
                <div className="mt-2 grid max-h-64 grid-cols-2 gap-2 overflow-y-auto rounded-md border p-3">
                  {allPerms.map((p) => (
                    <label key={p} className="flex items-center gap-2 text-sm">
                      <Checkbox checked={permissions.includes(p)} onCheckedChange={() => toggle(p)} />{p}
                    </label>
                  ))}
                </div>
              </div>
              <Button type="submit" className="w-full" disabled={save.isPending}>{save.isPending ? "Guardando…" : "Guardar"}</Button>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {roles.map((r) => (
          <Card key={r.id}>
            <CardHeader><CardTitle className="flex flex-wrap items-center justify-between gap-2">
              <span className="flex items-center gap-2"><Shield className="h-5 w-5" />{r.name}</span>
              <Badge variant="outline">{r.code}</Badge>
            </CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">{r.description || (r.is_system ? "Rol de sistema" : "Rol personalizado")}</p>
              <p className="text-xs text-muted-foreground">{r.permissions.length} permisos</p>
              <div className="flex items-center gap-1">
                {r.is_system && <span className="mr-1 flex items-center gap-1 text-xs text-muted-foreground"><Lock className="h-3.5 w-3.5" />Sistema</span>}
                <Button variant="ghost" size="sm" onClick={() => openEdit(r)}><Pencil className="mr-1 h-4 w-4" />Editar</Button>
                {!["ORG_ADMIN", "SYSTEM"].includes(r.code) && (
                  <Button variant="ghost" size="sm" onClick={() => remove.mutate(r.id)}><Trash2 className="mr-1 h-4 w-4" />Eliminar</Button>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
