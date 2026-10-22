"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

type Policy = {
  id?: string | null;
  mode?: string;
  require_primary_citation?: boolean;
  require_legal_norm?: boolean;
  block_on_unsupported?: boolean;
  block_on_numbers?: boolean;
  block_on_parties?: boolean;
  block_on_legal_conclusion?: boolean;
  require_second_opinion?: boolean;
  second_opinion_model_id?: string | null;
};

type Settings = {
  second_opinion_enabled?: boolean;
  auto_second_opinion?: string;
};

type PolicyResponse = { policy: Policy; settings?: Settings | null };
type Model = { id: string; provider: string; model_name: string };

const AUTO_OPTIONS = [
  { value: "NEVER", label: "Manual: solo cuando se solicite" },
  { value: "ANALYSIS", label: "Automática en análisis" },
  { value: "ALL", label: "Automática en toda respuesta" },
  { value: "STRICT_ONLY", label: "Automática si el gate está en revisión o estricto" },
];

/**
 * Política de verificación de un proceso.
 *
 * El proceso ya está fijado por la URL. No se pide un UUID: la misma pestaña
 * sirve para el expediente abierto y no permite configurar otro por accidente.
 */
export function VerificationPolicyForm({ caseId }: { caseId: string }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState("BALANCED");
  const [modelId, setModelId] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [auto, setAuto] = useState("NEVER");
  const [requireSecond, setRequireSecond] = useState(false);

  const policy = useQuery({
    queryKey: ["verification-policy", caseId],
    queryFn: () => api.get<PolicyResponse>(`/cases/${caseId}/verification-policy`),
    enabled: Boolean(caseId),
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
      auto_second_opinion: auto,
      require_second_opinion: requireSecond,
    }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["verification-policy", caseId] }),
  });

  const modelList: Model[] = Array.isArray(models.data) ? models.data : (models.data?.models ?? []);

  useEffect(() => {
    if (!policy.data) return;
    setMode(policy.data.policy.mode || "BALANCED");
    setModelId(policy.data.policy.second_opinion_model_id || "");
    setEnabled(Boolean(policy.data.settings?.second_opinion_enabled));
    setAuto(policy.data.settings?.auto_second_opinion || "NEVER");
    setRequireSecond(Boolean(policy.data.policy.require_second_opinion));
  }, [policy.data]);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="flex items-center gap-2 text-xl font-semibold">
          <ShieldCheck className="h-5 w-5 text-primary" /> Verificación del proceso
        </h2>
        <p className="text-sm text-muted-foreground">
          Política del gate legal y modelo de segunda opinión para este expediente.
          Sin un modelo distinto al respondedor, la segunda opinión no se ejecuta.
        </p>
      </div>

      <Card>
        <CardContent className="space-y-5 p-4">
          {policy.isLoading && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Cargando política del proceso.
            </p>
          )}
          {policy.isError && (
            <p className="text-sm text-rose-600">
              No se pudo cargar la política. Revisa tus permisos sobre este proceso.
            </p>
          )}

          {policy.data && (
            <>
              <div className="grid gap-4 md:grid-cols-2">
                <label className="text-sm">
                  <span className="mb-1 block font-medium">Modo del gate legal</span>
                  <select
                    value={mode}
                    onChange={(e) => setMode(e.target.value)}
                    className="h-9 w-full rounded-md border bg-background px-2"
                  >
                    <option value="STRICT">STRICT (bloquea)</option>
                    <option value="BALANCED">BALANCED</option>
                    <option value="ADVISORY">ADVISORY</option>
                  </select>
                </label>

                <label className="text-sm">
                  <span className="mb-1 block font-medium">Modelo de segunda opinión</span>
                  <select
                    value={modelId}
                    onChange={(e) => setModelId(e.target.value)}
                    className="h-9 w-full rounded-md border bg-background px-2"
                  >
                    <option value="">Sin modelo</option>
                    {modelList.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.provider}/{m.model_name}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="text-sm">
                  <span className="mb-1 block font-medium">Disparo automático</span>
                  <select
                    value={auto}
                    onChange={(e) => setAuto(e.target.value)}
                    className="h-9 w-full rounded-md border bg-background px-2"
                  >
                    {AUTO_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </label>

                <div className="space-y-3 rounded-md border p-3">
                  <label className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={enabled}
                      onChange={(e) => setEnabled(e.target.checked)}
                    />
                    <span>
                      Habilitar segunda opinión por proceso
                      <span className="block text-xs text-muted-foreground">
                        Requiere un modelo distinto al que respondió.
                      </span>
                    </span>
                  </label>
                  <label className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={requireSecond}
                      onChange={(e) => setRequireSecond(e.target.checked)}
                    />
                    <span>
                      Exigirla antes de publicar
                      <span className="block text-xs text-muted-foreground">
                        La respuesta original queda retenida hasta completar la segunda opinión.
                      </span>
                    </span>
                  </label>
                </div>
              </div>

              {enabled && !modelId && (
                <p className="flex items-start gap-2 rounded-md border border-amber-600/30 bg-amber-500/5 p-3 text-sm text-amber-800 dark:text-amber-200">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  La segunda opinión está habilitada pero no tiene modelo. No se ejecutará hasta elegir uno distinto.
                </p>
              )}

              <div className="flex flex-wrap items-center gap-3">
                <Button onClick={() => save.mutate()} disabled={save.isPending || !caseId}>
                  {save.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Guardar política
                </Button>
                {save.isSuccess && (
                  <p className="text-sm text-emerald-700 dark:text-emerald-300">Política guardada.</p>
                )}
                {save.isError && (
                  <p className="text-sm text-rose-600">
                    No se pudo guardar. Revisa permisos y el modelo seleccionado.
                  </p>
                )}
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
