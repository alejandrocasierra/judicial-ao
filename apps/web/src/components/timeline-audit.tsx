"use client";

import { useMemo } from "react";
import { FileText, Video } from "lucide-react";
import { displayDate, effectiveDate, mmss, type TimelineEvent, type TimelineSource } from "@/components/timeline-types";

// Panel de auditoría de la línea de tiempo: de qué documento/página salió cada actuación,
// qué actuaciones quedaron sin fecha y qué marcas de revisión tienen. No inventa datos:
// solo muestra lo que devuelve el endpoint /timeline.
const FLAG_HELP: Record<string, string> = {
  sin_fuente_real: "Se menciona en otro documento pero no se halló su documento original.",
  sin_fecha: "No se pudo determinar una fecha (ni del acto ni del documento).",
  duplicado: "Duplicado de otra actuación (se fusiona automáticamente).",
  fecha_inconsistente: "La fecha del acto y la fecha del documento no coinciden.",
};

function OriginButtons({ ev, onOpenSource }: { ev: TimelineEvent; onOpenSource: (s: TimelineSource) => void }) {
  const srcs = ev.sources ?? [];
  if (srcs.length === 0) {
    if (ev.document_filename) {
      return (
        <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
          <FileText className="h-3 w-3" />{ev.document_filename}
          {ev.page_number ? ` p.${ev.page_number}` : ""}
        </span>
      );
    }
    return <span className="text-xs text-muted-foreground">Sin fuente registrada</span>;
  }
  return (
    <span className="flex flex-wrap gap-1">
      {srcs.map((s) => (
        <button
          key={s.citation_id}
          type="button"
          onClick={() => onOpenSource(s)}
          className="inline-flex items-center gap-1 rounded border bg-background px-1.5 py-0.5 text-xs text-muted-foreground underline decoration-dotted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {s.source_type === "document_page" ? <FileText className="h-3 w-3" /> : <Video className="h-3 w-3" />}
          {s.filename || (s.source_type === "document_page" ? "documento" : "video")}
          {s.source_type === "document_page" ? ` p.${s.page}` : ` ${mmss(s.start_ms)}`}
        </button>
      ))}
    </span>
  );
}

export function TimelineAudit({
  events,
  subtypeLabel,
  onOpenSource,
}: {
  events: TimelineEvent[];
  subtypeLabel: (s: string) => string;
  onOpenSource: (s: TimelineSource) => void;
}) {
  const { sinFecha, conMarcas } = useMemo(() => {
    const sinFecha = events.filter((e) => !effectiveDate(e));
    const conMarcas = events.filter((e) => (e.review_flags ?? []).length > 0);
    return { sinFecha, conMarcas };
  }, [events]);

  return (
    <div className="space-y-4">
      <div className="grid gap-2 sm:grid-cols-3">
        <div className="rounded-md border bg-card px-3 py-2">
          <p className="text-2xl font-semibold">{events.length}</p>
          <p className="text-xs text-muted-foreground">Actuaciones en la línea de tiempo</p>
        </div>
        <div className="rounded-md border bg-card px-3 py-2">
          <p className="text-2xl font-semibold">{sinFecha.length}</p>
          <p className="text-xs text-muted-foreground">Sin fecha determinada</p>
        </div>
        <div className="rounded-md border bg-card px-3 py-2">
          <p className="text-2xl font-semibold">{conMarcas.length}</p>
          <p className="text-xs text-muted-foreground">Con marcas de revisión</p>
        </div>
      </div>

      <p className="text-xs text-muted-foreground">
        Los duplicados se fusionan automáticamente (se conserva una sola actuación con todas sus
        citas), por eso no aparecen aquí. Esta vista muestra el resto para depuración.
      </p>

      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Actuaciones sin fecha ({sinFecha.length})
        </h3>
        {sinFecha.length === 0 ? (
          <p className="text-sm text-muted-foreground">Todas las actuaciones tienen fecha.</p>
        ) : (
          <ul className="space-y-1.5">
            {sinFecha.map((ev) => (
              <li key={ev.id} className="rounded border px-2 py-1.5 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  {ev.code && <span className="font-mono text-[11px] text-muted-foreground">{ev.code}</span>}
                  {ev.subtype && <span className="text-xs font-medium">{subtypeLabel(ev.subtype)}</span>}
                </div>
                <p className="text-sm">{ev.description}</p>
                <OriginButtons ev={ev} onOpenSource={onOpenSource} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Marcas de revisión ({conMarcas.length})
        </h3>
        {conMarcas.length === 0 ? (
          <p className="text-sm text-muted-foreground">Ninguna actuación quedó marcada para revisión.</p>
        ) : (
          <ul className="space-y-1.5">
            {conMarcas.map((ev) => (
              <li key={ev.id} className="rounded border px-2 py-1.5 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  {ev.code && <span className="font-mono text-[11px] text-muted-foreground">{ev.code}</span>}
                  <span className="text-xs">{displayDate(effectiveDate(ev)) ?? "sin fecha"}</span>
                </div>
                <p className="text-sm">{ev.description}</p>
                <ul className="mt-1 space-y-0.5">
                  {(ev.review_flags ?? []).map((f) => (
                    <li key={f} className="text-xs text-amber-700 dark:text-amber-500">
                      <b>{f}</b>: {FLAG_HELP[f] ?? "Marca de revisión."}
                    </li>
                  ))}
                </ul>
                <OriginButtons ev={ev} onOpenSource={onOpenSource} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
