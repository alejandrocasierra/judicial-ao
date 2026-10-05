"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Wrench, Plus, Trash2, Pencil } from "lucide-react";
import { toast } from "sonner";

interface Skill { id: string; name: string; system_prompt: string; is_system?: boolean; used_by?: number; }

export default function SkillsPage() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Skill | null>(null);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");

  const { data: skills = [], isLoading } = useQuery({ queryKey: ["skills"], queryFn: () => api.get<Skill[]>("/admin/skills") });
  const save = useMutation({
    mutationFn: () => editing
      ? api.patch(`/admin/skills/${editing.id}`, { name, system_prompt: prompt })
      : api.post("/admin/skills", { name, system_prompt: prompt }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["skills"] }); setOpen(false); toast.success("Skill guardada"); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error"),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/admin/skills/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["skills"] }); toast.success("Skill eliminada"); },
  });

  function openNew() { setEditing(null); setName(""); setPrompt(""); setOpen(true); }
  function openEdit(s: Skill) { setEditing(s); setName(s.name); setPrompt(s.system_prompt); setOpen(true); }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Skills</h1>
          <p className="text-muted-foreground">Habilidades reutilizables para los agentes</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild><Button onClick={openNew}><Plus className="mr-2 h-4 w-4" />Nueva skill</Button></DialogTrigger>
          <DialogContent>
            <DialogHeader><DialogTitle>{editing ? "Editar skill" : "Crear skill"}</DialogTitle></DialogHeader>
            <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }} className="space-y-4">
              <div><label className="text-sm font-medium">Nombre</label>
                <Input value={name} onChange={(e) => setName(e.target.value)} required /></div>
              <div><label className="text-sm font-medium">System prompt de la skill</label>
                <Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={6} /></div>
              <Button type="submit" className="w-full" disabled={save.isPending}>{save.isPending ? "Guardando…" : "Guardar"}</Button>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {isLoading ? <p className="text-muted-foreground">Cargando…</p> : skills.length === 0 ? (
        <Card><CardContent className="flex flex-col items-center justify-center py-12">
          <Wrench className="mb-4 h-12 w-12 text-muted-foreground" /><p className="text-muted-foreground">No hay skills configuradas aún</p>
        </CardContent></Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {skills.map((s) => (
            <Card key={s.id}>
              <CardHeader><CardTitle className="flex items-center justify-between">
                <span className="flex items-center gap-2"><Wrench className="h-5 w-5" />{s.name}
                  {s.is_system && <Badge variant="outline">Sistema</Badge>}</span>
                <span className="flex gap-1">
                  <Button variant="ghost" size="icon" onClick={() => openEdit(s)}><Pencil className="h-4 w-4" /></Button>
                  <Button variant="ghost" size="icon" onClick={() => remove.mutate(s.id)}><Trash2 className="h-4 w-4" /></Button>
                </span>
              </CardTitle></CardHeader>
              <CardContent>
                <p className="line-clamp-4 text-sm text-muted-foreground">{s.system_prompt}</p>
                {(s.used_by ?? 0) > 0 && (
                  <p className="mt-2 text-xs text-muted-foreground">Usada por {s.used_by} agente{(s.used_by ?? 0) === 1 ? "" : "s"}</p>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
