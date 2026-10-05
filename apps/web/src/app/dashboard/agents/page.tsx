"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Bot, Plus, Trash2, Pencil } from "lucide-react";
import { toast } from "sonner";

interface Agent { id: string; name: string; system_prompt: string; skills: string[]; is_system: boolean; kind: string; created_at: string; }
interface Skill { id: string; name: string; system_prompt: string; }

export default function AgentsPage() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Agent | null>(null);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [skills, setSkills] = useState<string[]>([]);

  const { data: agents = [], isLoading } = useQuery({ queryKey: ["agents"], queryFn: () => api.get<Agent[]>("/admin/agents") });
  const { data: allSkills = [] } = useQuery({ queryKey: ["skills"], queryFn: () => api.get<Skill[]>("/admin/skills") });

  const save = useMutation({
    mutationFn: () => editing
      ? api.patch(`/admin/agents/${editing.id}`, { name, system_prompt: prompt, skills })
      : api.post("/admin/agents", { name, system_prompt: prompt, skills }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["agents"] }); setOpen(false); toast.success("Agente guardado"); },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error"),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/admin/agents/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["agents"] }); toast.success("Agente eliminado"); },
  });

  function openNew() { setEditing(null); setName(""); setPrompt(""); setSkills([]); setOpen(true); }
  function openEdit(a: Agent) { setEditing(a); setName(a.name); setPrompt(a.system_prompt); setSkills(a.skills || []); setOpen(true); }
  function toggleSkill(id: string) { setSkills((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id])); }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Agentes</h1>
          <p className="text-muted-foreground">Crea agentes y enlázales skills</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild><Button onClick={openNew}><Plus className="mr-2 h-4 w-4" />Nuevo agente</Button></DialogTrigger>
          <DialogContent>
            <DialogHeader><DialogTitle>{editing ? "Editar agente" : "Crear agente"}</DialogTitle></DialogHeader>
            <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }} className="space-y-4">
              <div><label className="text-sm font-medium">Nombre</label>
                <Input value={name} onChange={(e) => setName(e.target.value)} required placeholder="Agente de contradicciones" /></div>
              <div><label className="text-sm font-medium">System prompt</label>
                <Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={5} placeholder="Eres un asistente jurídico que…" /></div>
              <div>
                <label className="text-sm font-medium">Skills asociadas</label>
                <div className="mt-2 max-h-40 space-y-2 overflow-y-auto rounded-md border p-3">
                  {allSkills.length === 0 ? <p className="text-sm text-muted-foreground">No hay skills; créalas en el módulo Skills.</p> :
                    allSkills.map((s) => (
                      <label key={s.id} className="flex items-center gap-2 text-sm">
                        <Checkbox checked={skills.includes(s.id)} onCheckedChange={() => toggleSkill(s.id)} />
                        {s.name}
                      </label>
                    ))}
                </div>
              </div>
              <Button type="submit" className="w-full" disabled={save.isPending}>{save.isPending ? "Guardando…" : "Guardar"}</Button>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {isLoading ? <p className="text-muted-foreground">Cargando…</p> : agents.length === 0 ? (
        <Card><CardContent className="flex flex-col items-center justify-center py-12">
          <Bot className="mb-4 h-12 w-12 text-muted-foreground" /><p className="text-muted-foreground">No hay agentes configurados aún</p>
        </CardContent></Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {agents.map((a) => (
            <Card key={a.id}>
              <CardHeader><CardTitle className="flex items-center justify-between">
                <span className="flex items-center gap-2"><Bot className="h-5 w-5" />{a.name}
                  {a.kind === "chat" && <Badge variant="secondary">Chat</Badge>}</span>
                <span className="flex items-center gap-1">
                  {a.is_system && <Badge variant="outline">Sistema</Badge>}
                  <Button variant="ghost" size="icon" onClick={() => openEdit(a)}><Pencil className="h-4 w-4" /></Button>
                  <Button variant="ghost" size="icon" onClick={() => remove.mutate(a.id)}><Trash2 className="h-4 w-4" /></Button>
                </span>
              </CardTitle></CardHeader>
              <CardContent>
                <p className="line-clamp-3 text-sm text-muted-foreground">{a.system_prompt}</p>
                {a.skills?.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1">
                    {a.skills.map((id) => <Badge key={id} variant="secondary">{allSkills.find((s) => s.id === id)?.name || id.slice(0, 8)}</Badge>)}
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
