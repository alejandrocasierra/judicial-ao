"use client";

import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Cpu, Plus, Trash2, Pencil, KeyRound } from "lucide-react";
import { toast } from "sonner";

interface Model { id: string; provider: string; model_name: string; api_base_url?: string | null; is_default: boolean; ocr_enabled: boolean; asr_enabled: boolean; has_api_key: boolean; created_at: string; }
interface Provider { id: string; models: string[]; }

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: "Anthropic (Claude)", openai: "OpenAI", gemini: "Google Gemini",
  kimi: "Kimi (Moonshot)", deepseek: "DeepSeek", custom: "Personalizado",
};

export default function ModelsPage() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Model | null>(null);
  const [provider, setProvider] = useState("anthropic");
  const [modelName, setModelName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [isDefault, setIsDefault] = useState(false);
  const [ocrEnabled, setOcrEnabled] = useState(false);
  const [asrEnabled, setAsrEnabled] = useState(false);

  const { data: models = [], isLoading } = useQuery({ queryKey: ["models"], queryFn: () => api.get<Model[]>("/admin/models") });
  const { data: providers = [] } = useQuery({ queryKey: ["model-providers"], queryFn: () => api.get<{ providers: Provider[] }>("/admin/models/providers").then((r) => r.providers) });
  const [catalogNonce, setCatalogNonce] = useState(0);
  // La API key escrita se propaga con retardo: así el catálogo se trae solo al pegarla.
  const [debouncedKey, setDebouncedKey] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedKey(apiKey.trim()), 600);
    return () => clearTimeout(t);
  }, [apiKey]);
  // Catálogo DINÁMICO: trae TODOS los modelos del proveedor (se actualiza solo cuando el proveedor lanza uno nuevo).
  const { data: catalogData, isFetching: catalogLoading } = useQuery({
    queryKey: ["models-available", provider, debouncedKey, catalogNonce, open],
    queryFn: () => api.post<{ models: string[]; source: string }>("/admin/models/available",
      { provider, api_key: debouncedKey || undefined, api_base_url: apiBaseUrl || undefined }),
    enabled: open,
  });
  const catalog = catalogData?.models ?? [];

  const save = useMutation({
    mutationFn: () => editing
      ? api.patch(`/admin/models/${editing.id}`, { provider, model_name: modelName, api_key: apiKey || undefined, api_base_url: apiBaseUrl || null, is_default: isDefault, ocr_enabled: ocrEnabled, asr_enabled: asrEnabled })
      : api.post("/admin/models", { provider, model_name: modelName, api_key: apiKey, api_base_url: apiBaseUrl || null, is_default: isDefault, ocr_enabled: ocrEnabled, asr_enabled: asrEnabled }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["models"] });
      setOpen(false);
      toast.success("Modelo guardado");
      if (ocrEnabled || asrEnabled) toast.info("Sólo un modelo puede tener OCR y uno ASR activos: se desmarcó el anterior.");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error"),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/admin/models/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["models"] }); toast.success("Modelo eliminado"); },
  });

  function openNew() { setEditing(null); setProvider("anthropic"); setModelName(""); setApiKey(""); setApiBaseUrl(""); setIsDefault(false); setOcrEnabled(false); setAsrEnabled(false); setOpen(true); }
  function openEdit(m: Model) { setEditing(m); setProvider(m.provider); setModelName(m.model_name); setApiKey(""); setApiBaseUrl(m.api_base_url || ""); setIsDefault(m.is_default); setOcrEnabled(m.ocr_enabled); setAsrEnabled(m.asr_enabled); setOpen(true); }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Modelos IA</h1>
          <p className="text-muted-foreground">Proveedores, modelos y API keys</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild><Button onClick={openNew}><Plus className="mr-2 h-4 w-4" />Nuevo modelo</Button></DialogTrigger>
          <DialogContent>
            <DialogHeader><DialogTitle>{editing ? "Editar modelo" : "Crear modelo"}</DialogTitle></DialogHeader>
            <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }} className="space-y-4">
              <div><label className="text-sm font-medium">Proveedor</label>
                <Select value={provider} onValueChange={(v) => { setProvider(v); setModelName(""); setApiKey(""); }}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {providers.map((p) => <SelectItem key={p.id} value={p.id}>{PROVIDER_LABEL[p.id] || p.id}</SelectItem>)}
                  </SelectContent>
                </Select></div>
              <div>
                <label className="text-sm font-medium">Modelo</label>
                {catalog.length > 0 ? (
                  <Select value={modelName} onValueChange={setModelName}>
                    <SelectTrigger><SelectValue placeholder="Elige un modelo" /></SelectTrigger>
                    <SelectContent>{catalog.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}</SelectContent>
                  </Select>
                ) : (
                  <Input value={modelName} onChange={(e) => setModelName(e.target.value)} placeholder="nombre-del-modelo" required />
                )}
                <p className="mt-1 flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
                  {catalogLoading
                    ? "Buscando los modelos del proveedor…"
                    : catalogData?.source === "live"
                      ? `Actualizado desde el proveedor (${catalog.length} modelos disponibles).`
                      : "Catálogo de referencia: pega la API key del proveedor para traer TODOS sus modelos (se cargan solos)."}
                  <button type="button" className="underline decoration-dotted hover:text-foreground"
                    onClick={() => setCatalogNonce((n) => n + 1)}>Actualizar</button>
                </p>
              </div>
              <div><label className="text-sm font-medium">API Key</label>
                <Input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
                  placeholder={editing?.has_api_key ? "•••• (dejar vacío para conservar)" : "sk-..."} /></div>
              <div>
                <label className="text-sm font-medium">URL base del API (opcional)</label>
                <Input value={apiBaseUrl} onChange={(e) => setApiBaseUrl(e.target.value)}
                  placeholder="Vacío = la del proveedor · ej. https://mi-proxy o https://...googleapis.com/v1beta/openai" />
                <p className="mt-1 text-xs text-muted-foreground">
                  Útil para proxies, otra región o tu propia VPS. El chat usará esta URL tal cual.
                </p>
              </div>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={isDefault} onCheckedChange={(v) => setIsDefault(Boolean(v))} />
                Marcar como modelo por defecto
              </label>
              <div className="space-y-2 rounded-md border p-3">
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox checked={ocrEnabled} onCheckedChange={(v) => setOcrEnabled(Boolean(v))} />
                  Habilitar este modelo para <span className="font-medium">OCR</span>
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox checked={asrEnabled} onCheckedChange={(v) => setAsrEnabled(Boolean(v))} />
                  Habilitar este modelo para <span className="font-medium">ASR</span>
                </label>
                <p className="text-xs text-muted-foreground">
                  Sólo puede haber un modelo con OCR activo y uno con ASR activo por organización:
                  al activarlo aquí, se desmarca automáticamente en el otro modelo.
                </p>
              </div>
              <Button type="submit" className="w-full" disabled={save.isPending}>{save.isPending ? "Guardando…" : "Guardar"}</Button>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {isLoading ? <p className="text-muted-foreground">Cargando…</p> : models.length === 0 ? (
        <Card><CardContent className="flex flex-col items-center justify-center py-12">
          <Cpu className="mb-4 h-12 w-12 text-muted-foreground" /><p className="text-muted-foreground">No hay modelos configurados aún</p>
        </CardContent></Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {models.map((m) => (
            <Card key={m.id}>
              <CardHeader><CardTitle className="flex items-center justify-between">
                <span className="flex items-center gap-2"><Cpu className="h-5 w-5" />{m.model_name}</span>
                <span className="flex items-center gap-1">
                  {m.ocr_enabled && <Badge variant="secondary">OCR</Badge>}
                  {m.asr_enabled && <Badge variant="secondary">ASR</Badge>}
                  {m.is_default && <Badge>Default</Badge>}
                </span>
              </CardTitle></CardHeader>
              <CardContent className="space-y-2">
                <Badge variant="outline">{PROVIDER_LABEL[m.provider] || m.provider}</Badge>
                <div className="flex items-center gap-1 text-sm text-muted-foreground">
                  <KeyRound className="h-3.5 w-3.5" />{m.has_api_key ? "API key configurada" : "Sin API key"}
                </div>
                {m.api_base_url && (
                  <p className="truncate text-xs text-muted-foreground" title={m.api_base_url}>{m.api_base_url}</p>
                )}
                <div className="flex gap-1 pt-1">
                  <Button variant="ghost" size="sm" onClick={() => openEdit(m)}><Pencil className="mr-1 h-4 w-4" />Editar</Button>
                  <Button variant="ghost" size="sm" onClick={() => remove.mutate(m.id)}><Trash2 className="mr-1 h-4 w-4" />Eliminar</Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
