"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Database, FileText, GitBranch, RefreshCw, Scale, Search } from "lucide-react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ProofBadge } from "@/components/proof-badge";
import { cn } from "@/lib/utils";

type Position = {
  id: string;
  party_id?: string | null;
  party_name?: string | null;
  side: string;
  position: string;
  source_claim_id?: string | null;
  source_citation_id?: string | null;
};
type Evidence = {
  id: string;
  evidence_id: string;
  evidence_type?: string;
  description?: string;
  admissibility_status?: string;
  stance: string;
};
type Citation = {
  id: string;
  source_type: string;
  document_id?: string | null;
  page_number?: number | null;
  filename?: string | null;
  media_id?: string | null;
  segment_id?: string | null;
};
type Decision = {
  id: string;
  decision_type?: string;
  decision_date?: string | null;
  outcome?: string | null;
  source_document_id?: string | null;
  filename?: string | null;
};
type Fact = {
  id: string;
  proposition: string;
  status: string;
  proof_status: string;
  proof_basis?: string;
  is_proven: boolean;
  fact_code?: string | null;
  confidence?: number | null;
  version: number;
  positions: Position[];
  evidence: Evidence[];
  citations: Citation[];
  decision?: Decision | null;
  revisions?: Array<{ version: number; reason: string; created_at: string; proof_status: string }>;
};

const proofTabs = [
  { key: "proven", label: "Probados" },
  { key: "supported", label: "Con prueba" },
  { key: "disputed", label: "Discutidos" },
  { key: "all", label: "Todos" },
];

function positionLabel(position: string) {
  return ({ ALLEGES: "Alega", DENIES: "Niega", ADMITS: "Admite", UNKNOWN: "Sin posición" } as Record<string, string>)[position] || position;
}

export function FactsBoard({ caseId }: { caseId: string }) {
  const qc = useQueryClient();
  const [proof, setProof] = useState("proven");
  const [side, setSide] = useState<string>("");
  const [position, setPosition] = useState<string>("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Fact | null>(null);
  const [editStatus, setEditStatus] = useState("ALLEGED");
  const [editSide, setEditSide] = useState("DEMANDANTE");
  const [editPosition, setEditPosition] = useState("UNKNOWN");
  const [editReason, setEditReason] = useState("");

  const params = useMemo(() => {
    const p: Record<string, string> = { proof, envelope: "true" };
    if (side) p.side = side;
    if (position) p.position = position;
    if (q.trim().length >= 2) p.q = q.trim();
    return p;
  }, [proof, side, position, q]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["facts", caseId, params],
    queryFn: () => api.get<{ facts: Fact[] }>(`/cases/${caseId}/facts`, params),
  });
  const rebuild = useMutation({
    mutationFn: () => api.post(`/cases/${caseId}/facts/rebuild`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["facts", caseId] });
    },
  });
  const saveFact = useMutation({
    mutationFn: (fact: Fact) => api.patch(`/cases/${caseId}/facts/${fact.id}`, {
      expected_version: fact.version,
      reason: editReason.trim(),
      proof_status: editStatus,
      positions: [{ side: editSide, position: editPosition }],
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["facts", caseId] });
      setSelected(null);
    },
  });

  useEffect(() => {
    if (!selected) return;
    setEditStatus(selected.proof_status || "ALLEGED");
    setEditReason("");
    const first = selected.positions[0];
    setEditSide(first?.side || "DEMANDANTE");
    setEditPosition(first?.position || "UNKNOWN");
  }, [selected]);

  const facts = data?.facts ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold">
            <Scale className="h-5 w-5 text-primary" /> Hechos del proceso
          </h2>
          <p className="text-sm text-muted-foreground">
            Solo <strong>Admitido</strong> y <strong>Probado</strong> cuentan como hechos probados. “Con prueba” no equivale a probado.
          </p>
        </div>
        <Button variant="outline" onClick={() => rebuild.mutate()} disabled={rebuild.isPending}>
          <RefreshCw className={cn("mr-2 h-4 w-4", rebuild.isPending && "animate-spin")} />
          Reconstruir hechos
        </Button>
      </div>

      <div className="flex flex-col gap-3 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex gap-1 overflow-x-auto" role="tablist" aria-label="Estado de prueba">
          {proofTabs.map((tab) => (
            <button
              key={tab.key}
              role="tab"
              aria-selected={proof === tab.key}
              onClick={() => setProof(tab.key)}
              className={cn(
                "shrink-0 rounded-md px-3 py-1.5 text-sm",
                proof === tab.key ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted",
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-md border p-0.5">
            {[
              ["", "Ambos"],
              ["DEMANDANTE", "Demandante"],
              ["DEMANDADO", "Demandado"],
            ].map(([value, label]) => (
              <button
                key={value || "ambos"}
                onClick={() => setSide(value)}
                className={cn("rounded px-2 py-1 text-xs", side === value ? "bg-muted font-medium" : "text-muted-foreground")}
              >
                {label}
              </button>
            ))}
          </div>
          <select
            aria-label="Filtrar por posición"
            value={position}
            onChange={(e) => setPosition(e.target.value)}
            className="h-8 rounded-md border bg-background px-2 text-xs"
          >
            <option value="">Todas</option>
            <option value="UNKNOWN">Sin posición</option>
            <option value="ALLEGES">Alega</option>
            <option value="DENIES">Niega</option>
            <option value="ADMITS">Admite</option>
          </select>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Buscar hecho…"
              className="w-48 pl-8"
              aria-label="Buscar hecho"
            />
          </div>
        </div>
      </div>

      {rebuild.isError && (
        <div className="flex items-center gap-2 rounded-md border border-rose-600/30 p-3 text-sm text-rose-700 dark:text-rose-300">
          <AlertTriangle className="h-4 w-4" /> No se pudo encolar la reconstrucción.
        </div>
      )}
      {isLoading && <p className="text-sm text-muted-foreground">Cargando hechos…</p>}
      {error && <p className="text-sm text-rose-600">No se pudieron cargar los hechos.</p>}
      {!isLoading && !error && facts.length === 0 && (
        <Card>
          <CardContent className="space-y-2 p-8 text-center">
            <Database className="mx-auto h-8 w-8 text-muted-foreground" />
            <p className="font-medium">Aún no hay hechos en esta vista.</p>
            <p className="text-sm text-muted-foreground">
              Reconstruye el ledger desde los documentos, claims y decisiones. Si no hay fuentes, el hecho no se crea.
            </p>
          </CardContent>
        </Card>
      )}

      <div className="space-y-3">
        {facts.map((fact) => {
          const bySide = (s: string) => fact.positions.filter((p) => p.side === s);
          return (
            <button
              key={fact.id}
              onClick={() => setSelected(fact)}
              className="w-full rounded-lg border bg-card p-4 text-left transition-colors hover:bg-muted/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0 space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <ProofBadge status={fact.proof_status} />
                    {fact.fact_code && <Badge variant="secondary" className="font-mono text-xs">{fact.fact_code}</Badge>}
                    {fact.is_proven && <Badge variant="outline" className="text-xs">Hecho probado</Badge>}
                  </div>
                  <p className="text-sm leading-6">{fact.proposition}</p>
                  <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                    {fact.citations.slice(0, 4).map((c) => (
                      <span key={c.id} className="inline-flex items-center gap-1 rounded border px-1.5 py-0.5">
                        <FileText className="h-3 w-3" />
                        {c.filename || c.document_id ? `${c.filename || "documento"}${c.page_number ? ` p. ${c.page_number}` : ""}` : "fuente"}
                      </span>
                    ))}
                    {fact.decision && (
                      <span className="inline-flex items-center gap-1 rounded border px-1.5 py-0.5">
                        <Scale className="h-3 w-3" /> Decisión
                      </span>
                    )}
                  </div>
                </div>
                <div className="grid shrink-0 grid-cols-2 gap-4 text-xs lg:w-72">
                  {["DEMANDANTE", "DEMANDADO"].map((s) => (
                    <div key={s}>
                      <p className="mb-1 font-medium text-muted-foreground">{s === "DEMANDANTE" ? "Demandante" : "Demandado"}</p>
                      {bySide(s).length === 0 ? (
                        <p className="text-muted-foreground">Sin posición</p>
                      ) : bySide(s).map((p) => (
                        <p key={p.id} className="truncate" title={p.party_name || ""}>
                          {p.party_name || "Parte"}: {positionLabel(p.position)}
                        </p>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      <Dialog open={!!selected} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="pr-8 text-left text-base leading-6">{selected?.proposition}</DialogTitle>
          </DialogHeader>
          {selected && (
            <div className="space-y-4 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <ProofBadge status={selected.proof_status} />
                <Badge variant="outline">Base: {selected.proof_basis || "NONE"}</Badge>
                <Badge variant="outline">Versión {selected.version}</Badge>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Card><CardContent className="p-3">
                  <p className="mb-2 font-medium">Posiciones</p>
                  {selected.positions.length === 0 && <p className="text-muted-foreground">Sin posición registrada.</p>}
                  {selected.positions.map((p) => (
                    <p key={p.id} className="text-muted-foreground">
                      <span className="text-foreground">{p.party_name || "Parte"}</span> · {p.side} · {positionLabel(p.position)}
                    </p>
                  ))}
                </CardContent></Card>
                <Card><CardContent className="p-3">
                  <p className="mb-2 font-medium">Decisión</p>
                  {selected.decision ? (
                    <p className="text-muted-foreground">
                      {selected.decision.decision_type || "Decisión"} {selected.decision.filename ? `· ${selected.decision.filename}` : ""}
                    </p>
                  ) : <p className="text-muted-foreground">No hay decisión judicial vinculada.</p>}
                </CardContent></Card>
              </div>
              <div>
                <p className="mb-2 font-medium">Evidencia</p>
                {selected.evidence.length === 0 && <p className="text-muted-foreground">Sin evidencia vinculada.</p>}
                <div className="space-y-1">
                  {selected.evidence.map((e) => (
                    <div key={e.id} className="flex items-center gap-2 rounded border p-2 text-xs">
                      <GitBranch className="h-3 w-3" />
                      <span className="truncate">{e.description || e.evidence_type}</span>
                      <Badge variant="outline" className="ml-auto">{e.stance}</Badge>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <p className="mb-2 font-medium">Fuentes</p>
                {selected.citations.map((c) => (
                  <p key={c.id} className="text-xs text-muted-foreground">
                    {c.filename || c.document_id || c.media_id} {c.page_number ? `p. ${c.page_number}` : ""}
                  </p>
                ))}
              </div>
              {selected.revisions && selected.revisions.length > 0 && (
                <div>
                  <p className="mb-2 font-medium">Historial</p>
                  {selected.revisions.map((r) => (
                    <p key={r.version} className="text-xs text-muted-foreground">
                      v{r.version} · {r.proof_status} · {r.reason}
                    </p>
                  ))}
                </div>
              )}
              <div className="space-y-2 rounded-md border p-3">
                <p className="font-medium">Revisar estado de prueba</p>
                <div className="grid gap-2 sm:grid-cols-3">
                  <label className="text-xs">
                    <span className="mb-1 block text-muted-foreground">Estado</span>
                    <select value={editStatus} onChange={(e) => setEditStatus(e.target.value)} className="h-8 w-full rounded border bg-background px-2">
                      <option value="ALLEGED">Alegado</option>
                      <option value="ADMITTED">Admitido</option>
                      <option value="EVIDENCE_SUPPORTED">Con prueba</option>
                      <option value="DISPUTED">Discutido</option>
                      <option value="JUDICIALLY_PROVEN">Probado judicialmente</option>
                      <option value="REJECTED">Rechazado</option>
                      <option value="UNRESOLVED">Sin resolver</option>
                    </select>
                  </label>
                  <label className="text-xs">
                    <span className="mb-1 block text-muted-foreground">Lado</span>
                    <select value={editSide} onChange={(e) => setEditSide(e.target.value)} className="h-8 w-full rounded border bg-background px-2">
                      <option value="DEMANDANTE">Demandante</option>
                      <option value="DEMANDADO">Demandado</option>
                      <option value="TERCERO">Tercero</option>
                      <option value="NO_ATRIBUIDO">No atribuido</option>
                    </select>
                  </label>
                  <label className="text-xs">
                    <span className="mb-1 block text-muted-foreground">Posición</span>
                    <select value={editPosition} onChange={(e) => setEditPosition(e.target.value)} className="h-8 w-full rounded border bg-background px-2">
                      <option value="UNKNOWN">Sin posición</option>
                      <option value="ALLEGES">Alega</option>
                      <option value="DENIES">Niega</option>
                      <option value="ADMITS">Admite</option>
                    </select>
                  </label>
                </div>
                <Input value={editReason} onChange={(e) => setEditReason(e.target.value)} placeholder="Motivo de la revisión (obligatorio)" />
                {saveFact.isError && <p className="text-xs text-rose-600">No se pudo guardar: verifica la regla de prueba (Admitido exige Admite; Probado exige decisión).</p>}
                <Button size="sm" disabled={editReason.trim().length < 3 || saveFact.isPending}
                        onClick={() => saveFact.mutate(selected)}>
                  Guardar revisión
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
