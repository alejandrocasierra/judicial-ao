"use client";

/** Panel de PARTES del proceso: lista las actuales y permite EXTRAER candidatos de los autos
 *  (demandante/demandado/…) y confirmarlas (crear) con revisión humana. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Plus, Sparkles, Users } from "lucide-react";
import { toast } from "sonner";

interface Party { id: string; name: string; role: string; entity_type: string; aliases?: string[] }
interface Candidate {
  name: string; normalized_name: string; role: string; entity_type: string;
  mentions: number; filename: string; page_number: number; folio?: string | null; document_id: string;
}
interface CreateResult { created: Array<{ id: string; name: string }> }

const ROLE_ES: Record<string, string> = {
  claimant: "Demandante", defendant: "Demandado", plaintiff: "Ejecutante", respondent: "Ejecutado",
  appellant: "Apelante", appellee: "Apelado", attorney: "Apoderado", representative: "Representante",
  third_party: "Tercero", witness: "Testigo", expert: "Perito", judge: "Juez",
};

export function PartiesButton({ caseId }: { caseId: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const partiesQ = useQuery({
    queryKey: ["parties", caseId],
    queryFn: () => api.get<Party[]>(`/cases/${caseId}/parties`),
    enabled: open,
  });
  const parties = partiesQ.data ?? [];

  const extract = useMutation({
    mutationFn: () => api.post<Candidate[]>(`/cases/${caseId}/parties/extract`, {}),
    onSuccess: (c) => {
      setCandidates(c);
      setSelected(new Set(c.map((x) => x.normalized_name)));
      if (!c.length) toast.info("No se detectaron partes en los documentos");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo extraer"),
  });

  const create = useMutation({
    mutationFn: () => {
      const chosen = (candidates ?? []).filter((c) => selected.has(c.normalized_name))
        .map((c) => ({ name: c.name, role: c.role, entity_type: c.entity_type }));
      return api.post<CreateResult>(`/cases/${caseId}/parties`, { parties: chosen });
    },
    onSuccess: (r) => {
      toast.success(`Partes creadas: ${r?.created?.length ?? 0}`);
      qc.invalidateQueries({ queryKey: ["parties", caseId] });
      setCandidates(null);
      setSelected(new Set());
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudieron crear"),
  });

  function toggle(norm: string) {
    setSelected((prev) => {
      const s = new Set(prev);
      if (s.has(norm)) s.delete(norm); else s.add(norm);
      return s;
    });
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline"><Users className="mr-2 h-4 w-4" />Partes</Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader><DialogTitle>Partes del proceso</DialogTitle></DialogHeader>
        <div className="space-y-4">
          <div>
            <h3 className="mb-2 text-sm font-semibold">Actuales ({parties.length})</h3>
            {parties.length ? (
              <ul className="space-y-1 text-sm">
                {parties.map((p) => (
                  <li key={p.id} className="flex flex-wrap items-center gap-2">
                    <Badge variant="secondary">{ROLE_ES[p.role] || p.role}</Badge>
                    <span>{p.name}</span>
                    <span className="text-xs text-muted-foreground">
                      ({p.entity_type === "organization" ? "organización" : "persona"})
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">Aún no hay partes. Extrae candidatos de los autos.</p>
            )}
          </div>

          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => extract.mutate()} disabled={extract.isPending}>
              <Sparkles className="mr-1 h-4 w-4" />{extract.isPending ? "Buscando…" : "Extraer de documentos"}
            </Button>
            {candidates && (
              <Button size="sm" onClick={() => create.mutate()} disabled={create.isPending || selected.size === 0}>
                <Plus className="mr-1 h-4 w-4" />Crear seleccionadas ({selected.size})
              </Button>
            )}
          </div>

          {candidates && (
            <div className="max-h-72 overflow-y-auto rounded-md border">
              {candidates.length ? candidates.map((c) => (
                <label key={c.normalized_name} className="flex cursor-pointer items-center gap-2 border-b px-3 py-2 text-sm last:border-0">
                  <Checkbox checked={selected.has(c.normalized_name)} onCheckedChange={() => toggle(c.normalized_name)} />
                  <Badge variant="outline">{ROLE_ES[c.role] || c.role}</Badge>
                  <span className="font-medium">{c.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {c.mentions} menciones · {c.filename} p.{c.page_number}
                  </span>
                </label>
              )) : <p className="p-3 text-sm text-muted-foreground">Sin candidatos.</p>}
            </div>
          )}
          <p className="text-[11px] text-muted-foreground">
            La extracción es heurística (encabezados de los autos): revisa antes de crear.
          </p>
        </div>
      </DialogContent>
    </Dialog>
  );
}
