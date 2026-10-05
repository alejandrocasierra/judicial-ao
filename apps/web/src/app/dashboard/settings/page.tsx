"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";

interface SmtpSettings {
  from_name: string;
  from_email: string;
  server: string;
  port: number;
  security: string;
  username: string;
  cc_emails: string[];
}

interface SmtpForm {
  from_name: string;
  from_email: string;
  server: string;
  port: number;
  security: string;
  username: string;
  password: string;
  cc_emails: string;
}

const EMPTY: SmtpForm = {
  from_name: "",
  from_email: "",
  server: "",
  port: 587,
  security: "STARTTLS",
  username: "",
  password: "",
  cc_emails: "",
};

// Convierte la configuración del backend al formulario (sin placeholders de ejemplo).
function toForm(d: SmtpSettings): SmtpForm {
  return {
    from_name: d.from_name || "",
    from_email: d.from_email || "",
    server: d.server || "",
    port: d.port || 587,
    security: d.security || "STARTTLS",
    username: d.username || "",
    password: "",
    cc_emails: (d.cc_emails || []).join(", "),
  };
}

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<SmtpForm>(EMPTY);

  const { data, isLoading } = useQuery({
    queryKey: ["admin", "smtp"],
    queryFn: () => api.get<SmtpSettings>("/admin/smtp"),
  });

  // Inicializa el formulario cuando llega la configuración (ajuste en render,
  // sin efecto de estado: no pisa lo que el usuario está escribiendo después).
  const [loadedFrom, setLoadedFrom] = useState<SmtpSettings | null>(null);
  if (data && data !== loadedFrom) {
    setLoadedFrom(data);
    setForm(toForm(data));
  }

  const mutation = useMutation({
    mutationFn: (payload: SmtpForm) =>
      api.post("/admin/smtp", {
        from_name: payload.from_name,
        from_email: payload.from_email,
        server: payload.server,
        port: payload.port,
        security: payload.security,
        username: payload.username,
        password: payload.password || undefined,
        cc_emails: payload.cc_emails,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "smtp"] });
      toast.success("Configuración SMTP guardada");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error al guardar"),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Configuración de Correos</h1>
        <p className="text-muted-foreground">Configuración SMTP para notificaciones</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>SMTP</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-muted-foreground">Cargando…</p>
          ) : (
            <form onSubmit={(e) => { e.preventDefault(); mutation.mutate(form); }} className="max-w-md space-y-4">
              <div>
                <label className="text-sm font-medium">From Name</label>
                <Input value={form.from_name} onChange={(e) => setForm({ ...form, from_name: e.target.value })} />
              </div>
              <div>
                <label className="text-sm font-medium">From Email</label>
                <Input type="email" value={form.from_email} onChange={(e) => setForm({ ...form, from_email: e.target.value })} />
              </div>
              <div>
                <label className="text-sm font-medium">Server</label>
                <Input value={form.server} onChange={(e) => setForm({ ...form, server: e.target.value })} />
              </div>
              <div>
                <label className="text-sm font-medium">Port</label>
                <Input type="number" value={form.port} onChange={(e) => setForm({ ...form, port: Number(e.target.value) })} />
              </div>
              <div>
                <label className="text-sm font-medium">Security</label>
                <Select value={form.security} onValueChange={(v) => setForm({ ...form, security: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="STARTTLS">STARTTLS</SelectItem>
                    <SelectItem value="SSL/TLS">SSL/TLS</SelectItem>
                    <SelectItem value="NONE">None</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-sm font-medium">Username</label>
                <Input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
              </div>
              <div>
                <label className="text-sm font-medium">Password</label>
                <Input type="password" value={form.password} placeholder="••••••••"
                       onChange={(e) => setForm({ ...form, password: e.target.value })} />
              </div>
              <div>
                <label className="text-sm font-medium">CC Emails</label>
                <Input value={form.cc_emails} onChange={(e) => setForm({ ...form, cc_emails: e.target.value })} />
                <p className="mt-1 text-xs text-muted-foreground">Correos separados por coma que recibirán copia</p>
              </div>
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending ? "Guardando…" : "Guardar configuración"}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
