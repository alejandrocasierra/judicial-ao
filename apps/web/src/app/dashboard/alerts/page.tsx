"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { AlertTriangle, Bell, CheckCircle2 } from "lucide-react";

interface Alert {
  severity: "info" | "warning" | "critical";
  alert: string;
  message: string;
  value?: number;
  threshold?: number;
  case_id?: string;
}

export default function AlertsPage() {
  const { data: alerts = [], isLoading, refetch, isFetching } = useQuery({
    queryKey: ["admin", "alerts"],
    queryFn: () => api.get<Alert[]>("/admin/alerts"),
  });

  const severityVariant = (s: Alert["severity"]) =>
    s === "critical" ? "destructive" : s === "warning" ? "default" : "secondary";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Alertas</h1>
          <p className="text-muted-foreground">Backlog, fallos OCR/ASR y presupuesto (SSD §113)</p>
        </div>
        <button
          onClick={() => refetch()}
          className="rounded-md border px-3 py-2 text-sm hover:bg-accent"
        >
          {isFetching ? "Actualizando..." : "Actualizar"}
        </button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bell className="h-5 w-5" />
            Alertas activas
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-muted-foreground">Cargando...</p>
          ) : alerts.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12">
              <CheckCircle2 className="h-12 w-12 text-green-500 mb-4" />
              <p className="text-muted-foreground">No hay alertas activas</p>
            </div>
          ) : (
            <div className="space-y-2">
              {alerts.map((a, i) => (
                <div key={i} className="flex items-start justify-between rounded-md border p-3">
                  <div className="flex items-start gap-3">
                    <AlertTriangle className="h-5 w-5 text-muted-foreground mt-0.5" />
                    <div>
                      <p className="font-medium">{a.message}</p>
                      <p className="text-sm text-muted-foreground">{a.alert}</p>
                    </div>
                  </div>
                  <Badge variant={severityVariant(a.severity)}>{a.severity}</Badge>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
