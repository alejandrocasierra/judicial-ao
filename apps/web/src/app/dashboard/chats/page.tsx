"use client";

/** Historial de conversaciones del Chat IA: todas las sesiones del usuario,
 * agrupadas por expediente. "Continuar" abre el widget en esa conversación;
 * "Archivar" la retira del listado (soft-delete en el backend). */

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useChatStore } from "@/lib/chat-store";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { MessageSquare, Trash2, Play } from "lucide-react";
import { toast } from "sonner";

interface Case { id: string; case_number: string; title: string }
interface Session {
  id: string; title: string; agent_id: string | null; model_id: string | null;
  message_count: number; updated_at: string;
}
interface CaseSessions { case: Case; sessions: Session[] }

export default function ChatsPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const openChat = useChatStore((s) => s.openChat);
  const [toDelete, setToDelete] = useState<{ caseId: string; sessionId: string; title: string } | null>(null);

  // El chat vive dentro del proceso: abre la sesión y navega a ese expediente.
  const continueSession = (caseId: string, sessionId: string) => {
    openChat(caseId, sessionId);
    router.push(`/dashboard/procesos/${caseId}`);
  };

  const { data = [], isLoading } = useQuery({
    queryKey: ["all-chats"],
    queryFn: async (): Promise<CaseSessions[]> => {
      const cases = await api.get<Case[]>("/cases");
      const all = await Promise.all(cases.map(async (c) => {
        try {
          return { case: c, sessions: await api.get<Session[]>(`/cases/${c.id}/chats`) };
        } catch { return { case: c, sessions: [] }; }
      }));
      return all.filter((x) => x.sessions.length > 0);
    },
  });

  const remove = useMutation({
    mutationFn: ({ caseId, sessionId }: { caseId: string; sessionId: string }) =>
      api.delete(`/cases/${caseId}/chats/${sessionId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["all-chats"] });
      setToDelete(null);
      toast.success("Conversación eliminada del historial");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "No se pudo eliminar"),
  });

  const total = useMemo(() => data.reduce((n, x) => n + x.sessions.length, 0), [data]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Chats IA</h1>
          <p className="text-muted-foreground">
            {total > 0 ? `${total} conversación${total === 1 ? "" : "es"} sobre tus expedientes` : "Historial de conversaciones con IA"}
          </p>
        </div>
        <Button onClick={() => router.push("/dashboard/procesos")}>
          <MessageSquare className="mr-2 h-4 w-4" />Ir a un proceso
        </Button>
      </div>

      {isLoading ? (
        <p className="text-muted-foreground">Cargando…</p>
      ) : data.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <MessageSquare className="mb-4 h-12 w-12 text-muted-foreground" />
            <p className="text-muted-foreground">No hay chats registrados aún</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Entra a un proceso y usa «Chat del proceso» para preguntar sobre sus documentos.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          {data.map(({ case: c, sessions }) => (
            <div key={c.id} className="space-y-2">
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold">{c.case_number}</h2>
                <span className="truncate text-sm text-muted-foreground">· {c.title}</span>
              </div>
              <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                {sessions.map((s) => (
                  <Card key={s.id}>
                    <CardContent className="space-y-3 p-4">
                      <div className="flex items-start justify-between gap-2">
                        <p className="line-clamp-2 text-sm font-medium">{s.title || "Conversación sin título"}</p>
                        <Badge variant="secondary" className="shrink-0">{s.message_count} msg</Badge>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Última actividad: {new Date(s.updated_at).toLocaleString()}
                      </p>
                      <div className="flex gap-2">
                        <Button size="sm" className="flex-1" onClick={() => continueSession(c.id, s.id)}>
                          <Play className="mr-1 h-3.5 w-3.5" />Continuar
                        </Button>
                        <Button size="sm" variant="outline" className="text-destructive hover:text-destructive"
                          title="Eliminar conversación del historial"
                          onClick={() => setToDelete({ caseId: c.id, sessionId: s.id, title: s.title || "esta conversación" })}>
                          <Trash2 className="mr-1 h-3.5 w-3.5" />Eliminar
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <Dialog open={!!toDelete} onOpenChange={(o) => { if (!o) setToDelete(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Eliminar conversación</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            ¿Seguro que quieres eliminar <span className="font-medium text-foreground">«{toDelete?.title}»</span> del
            historial? Desaparecerá de la lista. Los documentos y la evidencia del expediente no se ven afectados.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setToDelete(null)}>Cancelar</Button>
            <Button className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={remove.isPending}
              onClick={() => toDelete && remove.mutate({ caseId: toDelete.caseId, sessionId: toDelete.sessionId })}>
              {remove.isPending ? "Eliminando…" : "Eliminar"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
