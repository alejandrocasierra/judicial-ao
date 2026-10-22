"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Users,
  Shield,
  Settings,
  Bot,
  Wrench,
  Cpu,
  FolderKanban,
  MessageSquare,
  HardDrive,
  LayoutDashboard,
  Scale,
  Bell,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/dashboard/users", label: "Usuarios", icon: Users },
  { href: "/dashboard/roles", label: "Roles y Permisos", icon: Shield },
  { href: "/dashboard/settings", label: "Correos", icon: Settings },
  { href: "/dashboard/agents", label: "Agentes", icon: Bot },
  { href: "/dashboard/skills", label: "Skills", icon: Wrench },
  { href: "/dashboard/models", label: "Modelos IA", icon: Cpu },
  { href: "/dashboard/procesos", label: "Procesos", icon: FolderKanban },
  { href: "/dashboard/chats", label: "Chats IA", icon: MessageSquare },
  { href: "/dashboard/backups", label: "Backups", icon: HardDrive },
  { href: "/dashboard/alerts", label: "Alertas", icon: Bell },
];

export function AppSidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pathname = usePathname();

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={onClose}
          aria-hidden
        />
      )}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r bg-sidebar shadow-xl transition-transform duration-200 ease-in-out md:static md:z-auto md:translate-x-0 md:shadow-none",
          open ? "translate-x-0" : "-translate-x-full"
        )}
      >
        <div className="flex h-14 items-center gap-2 border-b px-4">
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            onClick={onClose}
            aria-label="Cerrar menú"
          >
            <X className="h-5 w-5" />
          </Button>
          <Link href="/dashboard" className="flex items-center gap-2 font-semibold" onClick={onClose}>
            <Scale className="h-6 w-6 text-primary" />
            <span>Judicial AI</span>
          </Link>
        </div>
        <nav className="flex-1 overflow-y-auto p-2">
          <ul className="space-y-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = pathname === item.href || pathname?.startsWith(item.href + "/");
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onClose}
                    className={cn(
                      "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-sidebar-accent text-sidebar-accent-foreground"
                        : "text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                    )}
                  >
                    <Icon className="h-4 w-4" />
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </aside>
    </>
  );
}
