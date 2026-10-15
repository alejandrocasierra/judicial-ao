// Tipos compartidos de la línea de tiempo procesal (usados por page.tsx y los
// componentes de visualización: cadena procesal, grafo causal y auditoría).
export interface TimelineSource {
  citation_id: string;
  source_type: string;
  document_id?: string | null;
  page?: number | null;
  media_id?: string | null;
  start_ms?: number | null;
  end_ms?: number | null;
  filename?: string | null;
}

export interface TimelineLink {
  relationship: string;
  direction: "in" | "out";
  other_id: string;
  other_code: string;
  other_subtype?: string | null;
  confidence?: number | null;
}

export interface TimelineEvent {
  id: string;
  event_date: string | null;
  document_date?: string | null;
  incorporation_date?: string | null;
  date_precision?: string | null;
  event_type?: string | null;
  description: string;
  timeline_confidence?: string | null;
  sources: TimelineSource[];
  kind?: string | null;
  subtype?: string | null;
  instance?: string | null;
  actor?: string | null;
  authority?: string | null;
  date_type?: string | null;
  procedural_effect?: string | null;
  document_id?: string | null;
  page_number?: number | null;
  document_filename?: string | null;
  duplicate_of?: string | null;
  review_flags?: string[] | null;
  seq?: number;
  code?: string | null;
  links?: TimelineLink[] | null;
}

/** Fechas placeholder que NUNCA deben mostrarse (1899-12-31, etc.). */
export const REJECT_DATES = new Set(["1899-12-31", "1899-12-30", "1900-01-01", "0001-01-01"]);

/** Fecha legible o null si es placeholder/inválida. */
export function displayDate(iso: string | null | undefined): string | null {
  if (!iso || REJECT_DATES.has(iso)) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return d.toLocaleDateString("es-CO", { year: "numeric", month: "short", day: "numeric" });
}

/** Mejor fecha disponible para ordenar/mostrar: actuación > documento > incorporación. */
export function effectiveDate(ev: TimelineEvent): string | null {
  for (const d of [ev.event_date, ev.document_date, ev.incorporation_date]) {
    if (d && !REJECT_DATES.has(d)) return d;
  }
  return null;
}

export function mmss(ms: number | null | undefined): string {
  const s = Math.floor((ms || 0) / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}
