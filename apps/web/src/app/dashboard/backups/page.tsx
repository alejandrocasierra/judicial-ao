"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { HardDrive, Plus } from "lucide-react";
import { toast } from "sonner";

interface Backup {
  id: string;
  backup_type: string;
  status: string;
  size_bytes: number | null;
  created_at: string;
  completed_at: string | null;
}

export default function BackupsPage() {
  const queryClient = useQueryClient();
  const { data: backups = [], isLoading } = useQuery({
    queryKey: ["admin", "backups"],
    queryFn: () => api.get<Backup[]>("/admin/backups"),
  });

  const triggerMutation = useMutation({
    mutationFn: () => api.post("/admin/backups"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "backups"] });
      toast.success("Backup iniciado");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error"),
  });

  function formatSize(bytes: number | null) {
    if (!bytes) return "—";
    const mb = bytes / (1024 * 1024);
    return `${mb.toFixed(2)} MB`;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Backups</h1>
          <p className="text-muted-foreground">Copias de seguridad del sistema</p>
        </div>
        <Button onClick={() => triggerMutation.mutate()} disabled={triggerMutation.isPending}>
          <Plus className="mr-2 h-4 w-4" />
          {triggerMutation.isPending ? "Iniciando..." : "Nuevo backup"}
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Historial de backups</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-muted-foreground">Cargando...</p>
          ) : backups.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12">
              <HardDrive className="h-12 w-12 text-muted-foreground mb-4" />
              <p className="text-muted-foreground">No hay backups registrados</p>
            </div>
          ) : (
            <div className="space-y-2">
              {backups.map((b) => (
                <div
                  key={b.id}
                  className="flex items-center justify-between rounded-md border p-3"
                >
                  <div className="flex items-center gap-3">
                    <HardDrive className="h-5 w-5 text-muted-foreground" />
                    <div>
                      <p className="font-medium">Backup {b.backup_type}</p>
                      <p className="text-sm text-muted-foreground">
                        {new Date(b.created_at).toLocaleString("es-CO")}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-muted-foreground">{formatSize(b.size_bytes)}</span>
                    <Badge variant={b.status === "SUCCEEDED" ? "default" : b.status === "FAILED" ? "destructive" : "secondary"}>
                      {b.status}
                    </Badge>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
