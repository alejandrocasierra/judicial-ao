"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { FolderKanban, Plus, ChevronRight, Pencil, Trash2, AlertTriangle, Activity } from "lucide-react";
import { toast } from "sonner";
import { TimelineDialog } from "@/components/timeline-dialog";

interface Case { id: string; case_number: string; title: string; created_at: string; version: number; }



export default function ProcesosPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [caseNumber, setCaseNumber] = useState("");
  const [editing, setEditing] = useState<Case | null>(null);
  const [editName, setEditName] = useState("");
  const [deleting, setDeleting] = useState<Case | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [timelineCase, setTimelineCase] = useState<Case | null>(null);

  const { data: cases = [], isLoading } = useQuery({
    queryKey: ["cases"],
    queryFn: () => api.get<Case[]>("/cases"),
  });

  const create = useMutation({
    mutationFn: () => {
      const number = caseNumber.trim() || `EXP-${Date.now()}-${Math.random().toString(36).slice(2, 6).toUpperCase()}`;
      // Radicado colombiano de 23 dígitos => jurisdicción "co"; otro identificador => "generic".
      const jurisdiction = /^\d{23}$/.test(number) ? "co" : "generic";
      return api.post<Case>("/cases", { jurisdiction, case_number: number, title: name.trim(), language: "es" });
    },
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["cases"] });
      setOpen(false);
      setName("");
      setCaseNumber("");
      toast.success("Proceso creado");
      router.push(`/dashboard/procesos/${c.id}`);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error al crear el proceso"),
  });

  const rename = useMutation({
    mutationFn: () => api.patch(`/cases/${editing!.id}`, { title: editName.trim(), expected_version: editing!.version }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cases"] });
      setEditing(null);
      toast.success("Proceso renombrado");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error al renombrar"),
  });

  const purge = useMutation({
    mutationFn: (id: string) => api.delete(`/cases/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cases"] });
      setDeleting(null);
      setConfirmText("");
      toast.success("Proceso eliminado por completo (archivos, OCR/ASR, embeddings y grafo)");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error al eliminar"),
  });

  function openRename(c: Case) { setEditing(c); setEditName(c.title); }
  function openDelete(c: Case) { setDeleting(c); setConfirmText(""); }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Procesos</h1>
          <p className="text-muted-foreground">Cada proceso organiza sus carpetas y archivos (PDFs, videos, Excel, Word, imágenes)</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild><Button><Plus className="mr-2 h-4 w-4" />Nuevo proceso</Button></DialogTrigger>
          <DialogContent>
            <DialogHeader><DialogTitle>Crear proceso</DialogTitle></DialogHeader>
            <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }} className="space-y-4">
              <div>
                <label className="text-sm font-medium">Nombre del proceso</label>
                <Input value={name} onChange={(e) => setName(e.target.value)} required minLength={3}
                  placeholder="Expediente ejecutivo hipotecario" />
              </div>
              <div>
                <label className="text-sm font-medium">Número de radicado <span className="font-normal text-muted-foreground">(opcional)</span></label>
                <Input value={caseNumber} onChange={(e) => setCaseNumber(e.target.value)}
                  placeholder="11001310302120180036100" maxLength={64} />
                <p className="mt-1 text-xs text-muted-foreground">Si lo dejas vacío se genera un identificador automático.</p>
              </div>
              <Button type="submit" className="w-full" disabled={create.isPending || name.trim().length < 3}>
                {create.isPending ? "Creando…" : "Crear proceso"}
              </Button>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {isLoading ? (
        <p className="text-muted-foreground">Cargando…</p>
      ) : cases.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-md border py-16">
          <FolderKanban className="mb-4 h-12 w-12 text-muted-foreground" />
          <p className="text-muted-foreground">Aún no hay procesos. Crea el primero.</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {cases.map((c) => (
            <Card key={c.id} className="group transition-colors hover:border-primary/60">
              <CardHeader>
                <CardTitle className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <Link href={`/dashboard/procesos/${c.id}`} className="flex min-w-0 items-center gap-2 hover:text-primary">
                    <FolderKanban className="h-5 w-5 shrink-0 text-primary" />
                    <span className="break-words sm:truncate">{c.title}</span>
                  </Link>
                  <span className="flex shrink-0 items-center gap-1">
                    <span className="flex items-center gap-1 sm:opacity-0 sm:transition-opacity sm:group-hover:opacity-100">
                      <Button variant="ghost" size="icon" title="Ver línea de tiempo procesal" onClick={() => setTimelineCase(c)}>
                        <Activity className="h-4 w-4" />
                      </Button>
                      <Button variant="ghost" size="icon" title="Renombrar proceso" onClick={() => openRename(c)}>
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button variant="ghost" size="icon" title="Eliminar proceso" onClick={() => openDelete(c)}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </span>
                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <Badge variant="outline" className="font-mono text-xs">{c.case_number}</Badge>
                <p className="text-xs text-muted-foreground">
                  Creado el {new Date(c.created_at).toLocaleDateString("es-CO", { year: "numeric", month: "long", day: "numeric" })}
                </p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={editing !== null} onOpenChange={(o) => { if (!o) setEditing(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>Renombrar proceso</DialogTitle></DialogHeader>
          <form onSubmit={(e) => { e.preventDefault(); rename.mutate(); }} className="space-y-4">
            <div>
              <label className="text-sm font-medium">Nombre del proceso</label>
              <Input value={editName} onChange={(e) => setEditName(e.target.value)} required minLength={3} />
              <p className="mt-1 font-mono text-xs text-muted-foreground">{editing?.case_number}</p>
            </div>
            <Button type="submit" className="w-full" disabled={rename.isPending || editName.trim().length < 3}>
              {rename.isPending ? "Guardando…" : "Guardar"}
            </Button>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={deleting !== null} onOpenChange={(o) => { if (!o) { setDeleting(null); setConfirmText(""); } }}>
        <DialogContent>
          <DialogHeader><DialogTitle>Eliminar proceso</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="flex items-start gap-3 rounded-md border border-destructive/50 bg-destructive/10 p-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
              <div className="text-sm">
                <p className="font-medium">Esta acción es irreversible.</p>
                <p className="text-muted-foreground">
                  Se eliminarán todos los archivos (Excel, PDF, imágenes, videos), las carpetas y subcarpetas,
                  el OCR de cada página, las transcripciones, los embeddings (pgvector), el knowledge graph y
                  todos los registros de la base de datos de este proceso.
                </p>
              </div>
            </div>
            <div>
              <p className="mb-2 text-sm">
                Para confirmar, escribe el radicado: <span className="font-mono font-medium">{deleting?.case_number}</span>
              </p>
              <Input value={confirmText} onChange={(e) => setConfirmText(e.target.value)}
                placeholder={deleting?.case_number} className="font-mono" />
            </div>
            <Button variant="destructive" className="w-full"
              disabled={purge.isPending || confirmText.trim() !== deleting?.case_number}
              onClick={() => purge.mutate(deleting!.id)}>
              {purge.isPending ? "Eliminando…" : "Eliminar definitivamente"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <TimelineDialog
        key={timelineCase?.id}
        caseItem={timelineCase}
        open={timelineCase !== null}
        onOpenChange={(o) => { if (!o) setTimelineCase(null); }}
      />
    </div>
  );
}
