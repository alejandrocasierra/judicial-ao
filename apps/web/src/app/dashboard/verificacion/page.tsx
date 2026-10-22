"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type Policy = {
  id?: string | null;
  mode?: string;
  require_primary_citation?: boolean;
  require_legal_norm?: boolean;
  second_opinion_model_id?: string | null;
};
type Model = { id: string; provider: string; model_name: string };
type PolicyResponse = { policy: Policy; settings?: { second_opinion_enabled?: boolean; auto_second_opinion?: string } | null };

export default function VerificacionPage() {
  const qc = useQueryClient();
  const [caseId, setCaseId] = useState("");
  const [mode, setMode] = useState("BALANCED");
  const [modelId, setModelId] = useState("");
  const [enabled, setEnabled] = useState(false);
  const policy = useQuery({
    queryKey: ["verification-policy", caseId],
    queryFn: () => api.get<PolicyResponse>(`/cases/${caseId}/verification-policy`),
    enabled: caseId.trim().length >= 8,
  });
  const models = useQuery({
    queryKey: ["models"],
    queryFn: () => api.get<{ models?: Model[] } | Model[]>("/models"),
  });
  const save = useMutation({
    mutationFn: () => api.put(`/cases/${caseId}/verification-policy`, {
      mode,
      second_opinion_enabled: enabled,
      second_opinion_model_id: modelId || null,
    }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["verification-policy", caseId] }),
  });
  const modelList: Model[] = Array.isArray(models.data) ? models.data : (models.data?.models ?? []);

  useEffect(() => {
    if (!policy.data) return;
    setMode(policy.data.policy.mode || "BALANCED");
    setModelId(policy.data.policy.second_opinion_model_id || "");
    setEnabled(Boolean(policy.data.settings?.second_opinion_enabled));
  }, [policy.data]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <ShieldCheck className="h-6 w-6 text-primary" /> Verificación
        </h1>
        <p className="text-sm text-muted-foreground">
          Política del gate legal por proceso y modelo de segunda opinión. Sin modelo distinto no se ejecuta la segunda opinión.
        </p>
      </div>

      <Card><CardContent className="space-y-4 p-4">
        <div>
          <label className="mb-1 block text-sm font-medium" htmlFor="case-id">ID del proceso</label>
          <Input id="case-id" value={caseId} onChange={(e) => setCaseId(e.target.value)} placeholder="UUID del proceso" className="max-w-xl font-mono" />
        </div>
        {policy.isError && <p className="text-sm text-rose-600">No se pudo cargar la política. Verifica el ID y tus permisos.</p>}
        {policy.data && (
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-sm">
              <span className="mb-1 block font-medium">Modo</span>
              <select value={mode} onChange={(e) => setMode(e.target.value)} className="h-9 w-full rounded-md border bg-background px-2">
                <option value="STRICT">STRICT (bloquea)</option>
                <option value="BALANCED">BALANCED</option>
                <option value="ADVISORY">ADVISORY</option>
              </select>
            </label>
            <label className="text-sm">
              <span className="mb-1 block font-medium">Modelo de segunda opinión</span>
              <select value={modelId} onChange={(e) => setModelId(e.target.value)} className="h-9 w-full rounded-md border bg-background px-2">
                <option value="">Sin modelo</option>
                {modelList.map((m) => <option key={m.id} value={m.id}>{m.provider}/{m.model_name}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              Habilitar segunda opinión por proceso
            </label>
            <div className="flex items-end">
              <Button onClick={() => save.mutate()} disabled={save.isPending || !caseId.trim()}>
                {save.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />} Guardar política
              </Button>
            </div>
          </div>
        )}
        {save.isError && <p className="text-sm text-rose-600">No se pudo guardar. Revisa permisos y el modelo.</p>}
        {save.isSuccess && <p className="text-sm text-emerald-700 dark:text-emerald-300">Política guardada.</p>}
      </CardContent></Card>
    </div>
  );
}
