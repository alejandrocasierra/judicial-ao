"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const tabs = [
  { key: "", label: "Expediente" },
  { key: "hechos", label: "Hechos" },
  { key: "auditoria", label: "Auditoría" },
];

export function ProcessTabs({ caseId }: { caseId: string }) {
  const pathname = usePathname();
  const base = `/dashboard/procesos/${caseId}`;
  return (
    <nav
      aria-label="Secciones del proceso"
      className="-mx-1 flex gap-1 overflow-x-auto border-b pb-0"
    >
      {tabs.map((tab) => {
        const href = tab.key ? `${base}/${tab.key}` : base;
        const active = tab.key
          ? pathname === href || pathname.startsWith(`${href}/`)
          : pathname === base;
        return (
          <Link
            key={tab.key || "expediente"}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "shrink-0 border-b-2 px-3 py-2 text-sm font-medium transition-colors",
              active
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
