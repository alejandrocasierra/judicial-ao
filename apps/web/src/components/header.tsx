"use client";

import { useAuth } from "@/lib/auth";
import { Moon, Sun, LogOut, User } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { HeaderProcessingMonitor } from "@/components/header-processing-monitor";

export function Header() {
  const { user, logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  // next-themes no conoce el tema hasta hidratar: se marca "montado" en el siguiente
  // frame (no síncrono en el efecto) para evitar el desajuste de hidratación.
  useEffect(() => {
    const id = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(id);
  }, []);

  return (
    <header className="flex h-14 items-center justify-between border-b bg-background px-4">
      <div className="flex items-center gap-4">
        <span className="text-sm font-medium text-muted-foreground">
          {user?.organization_id ? `Org: ${user.organization_id.slice(0, 8)}…` : ""}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <HeaderProcessingMonitor />
        {mounted && (
          <button
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className="rounded-md p-2 hover:bg-accent"
            aria-label="Cambiar tema"
          >
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        )}
        <div className="flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm">
          <User className="h-4 w-4 text-muted-foreground" />
          <span className="max-w-[150px] truncate">{user?.full_name || user?.email}</span>
        </div>
        <button
          onClick={logout}
          className="rounded-md p-2 hover:bg-accent text-muted-foreground hover:text-foreground"
          aria-label="Cerrar sesión"
        >
          <LogOut className="h-4 w-4" />
        </button>
      </div>
    </header>
  );
}
