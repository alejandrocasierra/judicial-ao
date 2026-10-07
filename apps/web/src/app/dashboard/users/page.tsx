"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Plus, Search, Download, RefreshCw, Eye, EyeOff, KeyRound, Pencil, User as UserIcon } from "lucide-react";
import { toast } from "sonner";

interface User {
  id: string;
  email: string;
  full_name: string;
  org_role: string;
  locale: string;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
  version: number;
}

interface UserUpdate {
  id: string;
  expected_version: number;
  full_name?: string;
  org_role?: string;
  locale?: string;
  is_active?: boolean;
  password?: string;
}

const ROLES = [
  { value: "ORG_ADMIN", label: "Administrador" },
  { value: "CASE_MANAGER", label: "Gestor de casos" },
  { value: "LAWYER", label: "Abogado" },
  { value: "REVIEWER", label: "Revisor" },
  { value: "ANALYST", label: "Analista" },
  { value: "READ_ONLY", label: "Solo lectura" },
];

/** Contraseña aleatoria que cumple la política (mayúscula, minúscula, número y símbolo). */
function genPassword(len = 16): string {
  const lower = "abcdefghijkmnpqrstuvwxyz";
  const upper = "ABCDEFGHJKLMNPQRSTUVWXYZ";
  const digit = "23456789";
  const sym = "!@#$%&*?";
  const all = lower + upper + digit + sym;
  const pick = (s: string) => s[Math.floor(Math.random() * s.length)];
  const out = [pick(lower), pick(upper), pick(digit), pick(sym)];
  for (let i = out.length; i < len; i++) out.push(pick(all));
  return out.sort(() => Math.random() - 0.5).join("");
}

export default function UsersPage() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [newName, setNewName] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newRole, setNewRole] = useState("ANALYST");
  const [newLocale, setNewLocale] = useState("es");
  const [newPassword, setNewPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const queryClient = useQueryClient();

  const { data: users = [], isLoading } = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => api.get<User[]>("/admin/users"),
  });

  const createMutation = useMutation({
    mutationFn: (data: { email: string; full_name: string; org_role: string; locale: string; password?: string }) =>
      api.post<{ invite_sent?: boolean }>("/admin/users", data),
    onSuccess: (r) => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      setDialogOpen(false);
      setNewName("");
      setNewEmail("");
      setNewPassword("");
      toast.success(r?.invite_sent
        ? "Usuario creado y correo de bienvenida enviado"
        : "Usuario creado con la contraseña asignada");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error al crear usuario"),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: UserUpdate) =>
      api.patch(`/admin/users/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      setDialogOpen(false);
      setEditingUser(null);
      setNewPassword("");
      toast.success("Usuario actualizado");
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error al actualizar"),
  });

  const resetPasswordMutation = useMutation({
    mutationFn: (id: string) => api.post(`/admin/users/${id}/reset-password`),
    onSuccess: () => toast.success("Correo de restablecimiento enviado"),
    onError: (e) => toast.error(e instanceof Error ? e.message : "Error"),
  });

  const filtered = users.filter(
    (u) =>
      u.full_name.toLowerCase().includes(search.toLowerCase()) ||
      u.email.toLowerCase().includes(search.toLowerCase()),
  );
  const paginated = filtered.slice((page - 1) * pageSize, page * pageSize);
  const totalPages = Math.ceil(filtered.length / pageSize);

  function exportExcel() {
    import("xlsx").then((xlsx) => {
      const ws = xlsx.utils.json_to_sheet(
        filtered.map((u) => ({
          Nombre: u.full_name,
          Email: u.email,
          Rol: u.org_role,
          Idioma: u.locale,
          Activo: u.is_active ? "Sí" : "No",
          "Último login": u.last_login_at ?? "Nunca",
        })),
      );
      const wb = xlsx.utils.book_new();
      xlsx.utils.book_append_sheet(wb, ws, "Usuarios");
      xlsx.writeFile(wb, "usuarios.xlsx");
    });
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Usuarios</h1>
          <p className="text-muted-foreground">Gestión de usuarios de la organización</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={exportExcel}>
            <Download className="mr-2 h-4 w-4" />
            Exportar Excel
          </Button>
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <DialogTrigger asChild>
              <Button onClick={() => { setEditingUser(null); setNewName(""); setNewEmail(""); setNewPassword(""); setShowPw(false); }}>
                <Plus className="mr-2 h-4 w-4" />
                Nuevo usuario
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{editingUser ? "Editar usuario" : "Crear usuario"}</DialogTitle>
              </DialogHeader>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (editingUser) {
                    updateMutation.mutate({
                      id: editingUser.id,
                      full_name: newName || editingUser.full_name,
                      org_role: newRole,
                      locale: newLocale,
                      expected_version: editingUser.version,
                      ...(newPassword ? { password: newPassword } : {}),
                    });
                  } else {
                    createMutation.mutate({
                      email: newEmail,
                      full_name: newName,
                      org_role: newRole,
                      locale: newLocale,
                      ...(newPassword ? { password: newPassword } : {}),
                    });
                  }
                }}
                className="space-y-4"
              >
                <div>
                  <label className="text-sm font-medium">Nombre completo</label>
                  <Input
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="Juan Pérez"
                    required={!editingUser}
                  />
                </div>
                <div>
                  <label className="text-sm font-medium">Email</label>
                  <Input
                    type="email"
                    value={newEmail}
                    onChange={(e) => setNewEmail(e.target.value)}
                    placeholder="juan@ejemplo.com"
                    required={!editingUser}
                    disabled={!!editingUser}
                  />
                </div>
                <div>
                  <label className="text-sm font-medium">Rol</label>
                  <Select value={newRole} onValueChange={setNewRole}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ROLES.map((r) => (
                        <SelectItem key={r.value} value={r.value}>
                          {r.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-sm font-medium">Idioma</label>
                  <Select value={newLocale} onValueChange={setNewLocale}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="es">Español</SelectItem>
                      <SelectItem value="en">English</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-sm font-medium">
                    {editingUser ? "Nueva contraseña (opcional)" : "Contraseña (opcional)"}
                  </label>
                  <div className="flex gap-2">
                    <div className="relative flex-1">
                      <Input
                        type={showPw ? "text" : "password"}
                        value={newPassword}
                        onChange={(e) => setNewPassword(e.target.value)}
                        autoComplete="new-password"
                        placeholder={editingUser
                          ? "Dejar en blanco para no cambiarla"
                          : "Vacío = se envía correo de bienvenida"}
                      />
                      <button
                        type="button"
                        onClick={() => setShowPw((v) => !v)}
                        className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
                        title={showPw ? "Ocultar" : "Mostrar"}
                      >
                        {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      title="Generar contraseña segura"
                      onClick={() => { setNewPassword(genPassword()); setShowPw(true); }}
                    >
                      <KeyRound className="h-4 w-4" />
                    </Button>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Solo el administrador puede asignarla. Debe tener mín. mayúscula, minúscula, número y símbolo.
                  </p>
                </div>
                {editingUser && (
                  <Button
                    type="button"
                    variant="outline"
                    className="w-full"
                    onClick={() => resetPasswordMutation.mutate(editingUser.id)}
                  >
                    <RefreshCw className="mr-2 h-4 w-4" />
                    Enviar correo de restablecimiento de contraseña
                  </Button>
                )}
                <Button type="submit" className="w-full" disabled={createMutation.isPending || updateMutation.isPending}>
                  {createMutation.isPending || updateMutation.isPending ? "Guardando..." : "Guardar"}
                </Button>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-4">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Buscar por nombre o email..."
                className="pl-8"
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1); }}
              />
            </div>
            <Select value={String(pageSize)} onValueChange={(v) => { setPageSize(Number(v)); setPage(1); }}>
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="10">10 / pág</SelectItem>
                <SelectItem value="20">20 / pág</SelectItem>
                <SelectItem value="50">50 / pág</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="py-8 text-center text-muted-foreground">Cargando...</p>
          ) : paginated.length === 0 ? (
            <p className="py-8 text-center text-muted-foreground">No se encontraron usuarios</p>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {paginated.map((u) => (
                <Card key={u.id}>
                  <CardHeader className="pb-2">
                    <CardTitle className="flex flex-wrap items-start justify-between gap-2">
                      <span className="flex min-w-0 items-center gap-2">
                        <UserIcon className="h-5 w-5 shrink-0 text-primary" />
                        <span className="min-w-0 break-words">{u.full_name}</span>
                      </span>
                      <Badge variant={u.is_active ? "default" : "destructive"}>
                        {u.is_active ? "Activo" : "Inactivo"}
                      </Badge>
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    <p className="break-all text-muted-foreground">{u.email}</p>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline">{ROLES.find((r) => r.value === u.org_role)?.label || u.org_role}</Badge>
                      <Badge variant="secondary">{u.locale}</Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Último login: {u.last_login_at ? new Date(u.last_login_at).toLocaleDateString("es-CO") : "Nunca"}
                    </p>
                    <div className="pt-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setEditingUser(u);
                          setNewName(u.full_name);
                          setNewEmail(u.email);
                          setNewRole(u.org_role);
                          setNewLocale(u.locale);
                          setNewPassword("");
                          setShowPw(false);
                          setDialogOpen(true);
                        }}
                      >
                        <Pencil className="mr-1 h-4 w-4" />Editar
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
          <div className="mt-4 flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              Mostrando {paginated.length} de {filtered.length} usuarios
            </p>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}>
                Anterior
              </Button>
              <span className="flex items-center px-3 text-sm">
                {page} / {totalPages || 1}
              </span>
              <Button variant="outline" size="sm" onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages}>
                Siguiente
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
