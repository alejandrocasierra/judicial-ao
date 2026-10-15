"use client";

import { useMemo } from "react";
import { displayDate, effectiveDate, type TimelineEvent } from "@/components/timeline-types";

// Vista de grafo causal: columnas por instancia, tiempo hacia abajo. Cada nodo es una
// actuación (botón HTML, accesible por teclado) y las flechas son las relaciones del
// Process Graph (causa, apela a, responde a, precede...). El SVG de fondo solo dibuja
// las flechas; los nodos son HTML para conservar foco, hover y clic reales.
const LANE_ORDER = ["primera", "segunda", "casacion", "tutela", "incidente", "cautelar", "ejecucion", "otro", "generico"];

const COL_W = 248;
const ROW_H = 74;
const NODE_W = 208;
const NODE_H = 48;
const PAD_X = 24;
const PAD_TOP = 48;
const PAD_BOTTOM = 32;

function laneOf(ev: TimelineEvent): string {
  const k = ev.instance || (ev.kind === "procedural" ? "otro" : "generico");
  return LANE_ORDER.includes(k) ? k : "otro";
}

export function ProceduralGraph({
  events,
  laneLabel,
  relationLabel,
  onSelect,
}: {
  events: TimelineEvent[];
  laneLabel: (key: string) => string;
  relationLabel: (key: string) => string;
  onSelect: (eventId: string) => void;
}) {
  const layout = useMemo(() => {
    const ordered = [...events].sort((a, b) => {
      const da = effectiveDate(a) ?? "";
      const db = effectiveDate(b) ?? "";
      if (da !== db) return da.localeCompare(db);
      return (a.seq ?? 0) - (b.seq ?? 0);
    });
    const lanes = LANE_ORDER.filter((k) => ordered.some((e) => laneOf(e) === k));
    const laneIndex = new Map(lanes.map((k, i) => [k, i]));
    const pos = new Map<string, { x: number; y: number }>();
    ordered.forEach((ev, i) => {
      const li = laneIndex.get(laneOf(ev)) ?? 0;
      pos.set(ev.id, { x: PAD_X + li * COL_W, y: PAD_TOP + i * ROW_H });
    });
    const edges: { x1: number; y1: number; x2: number; y2: number; rel: string }[] = [];
    const seen = new Set<string>();
    for (const ev of ordered) {
      const a = pos.get(ev.id);
      if (!a) continue;
      for (const l of ev.links ?? []) {
        if (l.direction !== "out") continue;
        const b = pos.get(l.other_id);
        if (!b) continue;
        const key = `${ev.id}|${l.other_id}|${l.relationship}`;
        if (seen.has(key)) continue;
        seen.add(key);
        edges.push({
          x1: a.x + NODE_W / 2, y1: a.y + NODE_H,
          x2: b.x + NODE_W / 2, y2: b.y,
          rel: l.relationship,
        });
      }
    }
    const width = PAD_X * 2 + Math.max(1, lanes.length) * COL_W;
    const height = PAD_TOP + Math.max(1, ordered.length) * ROW_H + PAD_BOTTOM;
    return { ordered, lanes, laneIndex, pos, edges, width, height };
  }, [events]);

  if (events.length === 0) return null;

  return (
    <div className="overflow-auto rounded-md border bg-muted/10" style={{ maxHeight: "60vh" }}>
      <div className="relative" style={{ width: layout.width, height: layout.height }}>
        {/* Encabezados de instancia */}
        {layout.lanes.map((lane, i) => (
          <div
            key={lane}
            className="absolute truncate text-xs font-semibold uppercase tracking-wide text-muted-foreground"
            style={{ left: PAD_X + i * COL_W, top: 14, width: NODE_W - 8 }}
          >
            {laneLabel(lane)}
          </div>
        ))}
        {/* Flechas */}
        <svg width={layout.width} height={layout.height} className="absolute inset-0" aria-hidden="true">
          <defs>
            <marker id="pg-arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
              <path d="M0,0 L6,3 L0,6 Z" className="fill-muted-foreground/70" />
            </marker>
          </defs>
          {layout.edges.map((e, i) => {
            const midY = (e.y1 + e.y2) / 2;
            const d = `M ${e.x1} ${e.y1} C ${e.x1} ${midY}, ${e.x2} ${midY}, ${e.x2} ${e.y2}`;
            return (
              <g key={i}>
                <title>{relationLabel(e.rel)}</title>
                <path d={d} fill="none" strokeWidth={1.5} className="stroke-muted-foreground/50"
                  markerEnd="url(#pg-arrow)" />
              </g>
            );
          })}
        </svg>
        {/* Nodos */}
        {layout.ordered.map((ev) => {
          const p = layout.pos.get(ev.id);
          if (!p) return null;
          const flagged = (ev.review_flags ?? []).filter((f) => f === "sin_fecha" || f === "sin_fuente_real");
          return (
            <button
              key={ev.id}
              type="button"
              onClick={() => onSelect(ev.id)}
              title={ev.description}
              style={{ left: p.x, top: p.y, width: NODE_W, height: NODE_H }}
              className={`absolute flex flex-col justify-center gap-0.5 rounded border bg-card px-2 text-left shadow-sm transition-colors hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                flagged.length > 0 ? "border-amber-500/70" : "border-border"
              }`}
            >
              <span className="truncate text-xs font-medium">{ev.code ? `${ev.code} · ` : ""}{displayDate(effectiveDate(ev)) ?? "sin fecha"}</span>
              <span className="truncate text-[11px] text-muted-foreground">{ev.description}</span>
            </button>
          );
        })}
      </div>
      {layout.edges.length === 0 && (
        <p className="border-t px-3 py-2 text-xs text-muted-foreground">
          Aún no hay relaciones causales entre actuaciones. Usa «Analizar vínculos» o «Causas con IA».
        </p>
      )}
    </div>
  );
}
