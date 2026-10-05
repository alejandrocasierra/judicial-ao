"use client";

/** Monitor global de procesos OCR/ASR en el header.
 * Icono con badge del número de trabajos activos; al hacer clic navega a la
 * vista dedicada /dashboard/procesamiento. */

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";

interface ActiveProcessing {
  jobs: unknown[];
  active_count: number;
}

export function HeaderProcessingMonitor() {
  const router = useRouter();

  const { data } = useQuery({
    queryKey: ["active-processing"],
    queryFn: () => api.get<ActiveProcessing>("/cases/processing/active"),
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
  });

  const count = data?.active_count ?? 0;

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={() => router.push("/dashboard/procesamiento")}
      className="relative"
      title="Monitor de procesos OCR/ASR"
    >
      <Loader2 className={`h-4 w-4 ${count > 0 ? "animate-spin" : ""}`} />
      {count > 0 && (
        <Badge
          variant="destructive"
          className="absolute -right-1 -top-1 h-5 w-5 rounded-full p-0 text-[10px] flex items-center justify-center"
        >
          {count > 9 ? "9+" : count}
        </Badge>
      )}
    </Button>
  );
}
