"use client";

import { displayEventDate, effectiveDate, type TimelineEvent } from "@/components/timeline-types";

// Cadena procesal canónica: el orden típico de un proceso civil colombiano. Cada paso
// se marca como presente (hay una actuación que lo acredita) o pendiente (no aparece).
// El propósito es dar en 2 segundos "en qué punto va el proceso"; las flechas expresan
// el orden procesal, no son decoración.
interface Step {
  key: string;
  label: string;
  // Subtypes canónicos que acreditan el paso.
  subtypes: string[];
  // event_types alternativos (p. ej. audiencias no tienen subtype propio).
  eventTypes?: string[];
  instances?: string[];
}

const STEPS: Step[] = [
  { key: "demanda", label: "Demanda", subtypes: ["demanda"] },
  { key: "admision", label: "Auto admisorio", subtypes: ["auto_admisorio"] },
  {
    key: "notificacion",
    label: "Notificación",
    subtypes: ["notificacion", "notificacion_personal", "notificacion_por_estado", "notificacion_electronica", "emplazamiento"],
  },
  { key: "contestacion", label: "Contestación", subtypes: ["contestacion", "excepcion", "reconvencion"] },
  {
    key: "pruebas",
    label: "Decreto de pruebas",
    subtypes: ["auto_de_pruebas", "decreto_prueba", "solicitud_prueba", "practica_prueba", "prueba"],
  },
  { key: "audiencia", label: "Audiencia", subtypes: [], eventTypes: ["audiencia"] },
  { key: "sentencia", label: "Sentencia", subtypes: ["sentencia"] },
  { key: "apelacion", label: "Apelación", subtypes: ["apelacion", "recurso", "concesion_recurso", "casacion", "queja"] },
];

function matches(ev: TimelineEvent, step: Step): boolean {
  if (step.subtypes.includes(ev.subtype ?? "")) return true;
  if (step.eventTypes && step.eventTypes.includes(ev.event_type ?? "")) return true;
  return false;
}

export function ProceduralChain({
  events,
  onSelect,
}: {
  events: TimelineEvent[];
  onSelect: (eventId: string) => void;
}) {
  if (events.length === 0) return null;

  const steps = STEPS.map((step) => {
    const found = events.filter((ev) => matches(ev, step));
    const withDate = found
      .filter((ev) => effectiveDate(ev))
      .sort((a, b) => (effectiveDate(a) ?? "").localeCompare(effectiveDate(b) ?? ""));
    const anchor = withDate[0] ?? found[0] ?? null;
    return { step, count: found.length, anchor };
  });
  const present = steps.filter((s) => s.count > 0).length;

  return (
    <section
      aria-label="Cadena procesal"
      className="rounded-md border bg-muted/20 px-3 py-2.5"
    >
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Cadena procesal</h3>
        <span className="text-[11px] text-muted-foreground">
          {present} de {STEPS.length} hitos con respaldo en el expediente
        </span>
      </div>
      <ol className="flex flex-wrap items-stretch gap-x-1 gap-y-2">
        {steps.map(({ step, count, anchor }, i) => {
          const isPresent = count > 0;
          return (
            <li key={step.key} className="flex items-stretch">
              {i > 0 && (
                <span aria-hidden="true" className="flex items-center px-0.5 text-muted-foreground/60">
                  →
                </span>
              )}
              {isPresent && anchor ? (
                <button
                  type="button"
                  onClick={() => onSelect(anchor.id)}
                  title={`Ir a la actuación: ${anchor.description}`}
                  className="flex min-w-[7.5rem] flex-col items-start rounded border border-primary/40 bg-background px-2 py-1 text-left transition-colors hover:border-primary hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="text-xs font-medium">{step.label}</span>
                  <span className="text-[11px] text-muted-foreground">
                    {displayEventDate(anchor) ?? "sin fecha"}
                    {count > 1 && ` · ${count}`}
                  </span>
                </button>
              ) : (
                <div
                  aria-disabled="true"
                  title="No se ha acreditado este hito en el expediente"
                  className="flex min-w-[7.5rem] flex-col items-start rounded border border-dashed px-2 py-1 text-muted-foreground/70"
                >
                  <span className="text-xs font-medium">{step.label}</span>
                  <span className="text-[11px]">pendiente</span>
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
