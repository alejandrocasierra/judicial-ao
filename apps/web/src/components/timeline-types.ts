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

/** Fechas centinela que NUNCA deben mostrarse (marcas de "sin fecha" del LLM). */
export const REJECT_DATES = new Set([
  "1899-12-31", "1899-12-30", "1900-01-01", "1901-01-01",
  "1969-12-31", "1970-01-01", "0001-01-01", "9999-12-31",
]);

const MONTHS_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/**
 * Formatea una fecha ISO (YYYY-MM-DD) SIN desfase de zona horaria.
 * `new Date("2025-01-01")` es medianoche UTC y en Colombia se ve como 2024-12-31:
 * por eso se parsea el texto y se arma a mano. Respeta `date_precision`:
 * - year  -> "2025"
 * - month -> "ene 2025"
 * - day   -> "5 de abr de 2022"
 */
export function displayDate(iso: string | null | undefined, precision?: string | null): string | null {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return null;
  const y = m[1], mo = Number(m[2]), da = Number(m[3]);
  const isoDay = `${y}-${m[2]}-${m[3]}`;
  if (REJECT_DATES.has(isoDay)) return null;
  const year = Number(y);
  if (year < 1900 || year > 2100 || mo < 1 || mo > 12 || da < 1 || da > 31) return null;
  // Un acto judicial nunca cae en 1 de enero ni 31 de diciembre (festivos): si aparece,
  // es el centinela "solo año" del LLM. Se muestra solo el año.
  const isYearSentinel = (mo === 1 && da === 1) || (mo === 12 && da === 31);
  if (precision === "year" || isYearSentinel) return y;
  if (precision === "month") return `${MONTHS_ES[mo - 1]} ${y}`;
  return `${da} de ${MONTHS_ES[mo - 1]} de ${y}`;
}

/** Fecha y precisión efectivas: actuación > documento > incorporación. */
export function eventDateParts(ev: TimelineEvent): { iso: string | null; precision: string } {
  if (ev.event_date && !REJECT_DATES.has(ev.event_date)) {
    return { iso: ev.event_date, precision: ev.date_precision || "day" };
  }
  if (ev.document_date && !REJECT_DATES.has(ev.document_date)) {
    return { iso: ev.document_date, precision: "day" };
  }
  if (ev.incorporation_date && !REJECT_DATES.has(ev.incorporation_date)) {
    return { iso: ev.incorporation_date, precision: "day" };
  }
  return { iso: null, precision: "unknown" };
}

/** Mejor fecha disponible (para ordenar). */
export function effectiveDate(ev: TimelineEvent): string | null {
  return eventDateParts(ev).iso;
}

/** Fecha legible de un evento respetando su precisión. */
export function displayEventDate(ev: TimelineEvent): string | null {
  const { iso, precision } = eventDateParts(ev);
  return iso ? displayDate(iso, precision) : null;
}

export function mmss(ms: number | null | undefined): string {
  const s = Math.floor((ms || 0) / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** Tipos de fecha que representan una actuación REAL (no una mención). */
export function isRealAct(ev: TimelineEvent): boolean {
  const dt = ev.date_type ?? "actuacion";
  return dt === "actuacion";
}
