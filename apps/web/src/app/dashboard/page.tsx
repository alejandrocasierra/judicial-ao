"use client";

import { useAuth } from "@/lib/auth";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Users, FileText, Video, MessageSquare } from "lucide-react";

export default function DashboardPage() {
  const { user } = useAuth();

  const stats = [
    { title: "Usuarios", icon: Users, value: "—", description: "Usuarios registrados" },
    { title: "Documentos", icon: FileText, value: "—", description: "PDFs procesados" },
    { title: "Videos", icon: Video, value: "—", description: "Audiencias transcritas" },
    { title: "Consultas", icon: MessageSquare, value: "—", description: "Preguntas respondidas" },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
        <p className="text-muted-foreground">
          Bienvenido, {user?.full_name || user?.email}
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {stats.map((stat) => (
          <Card key={stat.title}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">{stat.title}</CardTitle>
              <stat.icon className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{stat.value}</div>
              <p className="text-xs text-muted-foreground">{stat.description}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Resumen del sistema</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Plataforma Judicial AI — Panel de administración y gestión de expedientes.
            Selecciona un módulo en el menú lateral para comenzar.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
