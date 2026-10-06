"use client";

/** Vista de GRAFO del proceso: nodos = actuaciones, flechas = relaciones del Process Graph.
 *  Cada nodo es clicable (muestra su detalle y permite abrir la evidencia). */

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FileText, Video } from "lucide-react";
import { cn } from "@/lib/utils";

export interface GSource {
  citation_id: string; source_type: string; document_id?: string | null; page?: number | null;
  media_id?: string | null; start_ms?: number | null; filename?: string | null;
}
export interface GLink {
  relationship: string; direction: "in" | "out"; other_id: string; other_code: string;
  other_subtype?: string | null;
}
export interface GEvent {
  id: string; code?: string | null; event_date?: string | null; subtype?: string | null;
  instance?: string | null; actor?: string | null; authority?: string | null;
  description: string; procedural_effect?: string | null; sources?: GSource[]; links?: GLink[] | null;
}

const INSTANCE_ORDER = ["primera", "segunda", "casacion", "tutela", "incidente", "cautelar", "ejecucion", "otro"];
const INSTANCE_ES: Record<string, string> = {
  primera: "Primera instancia", segunda: "Segunda instancia", casacion: "Casación", tutela: "Tutela",
  incidente: "Incidente", cautelar: "Medida cautelar", ejecucion: "Ejecución", otro: "Otras actuaciones",
};
const ACTOR_ES: Record<string, string> = {
  juzgado: "Juzgado", demandante: "Demandante", demandado: "Demandado",
  apoderado_demandante: "Apod. demandante", apoderado_demandado: "Apod. demandado",
  tercero: "Tercero", fiscal: "Fiscalía", secretario: "Secretaría", otro: "Otro",
};
const SUBTYPE_ES: Record<string, string> = {
  demanda: "Demanda", contestacion: "Contestación", reconvencion: "Reconvención", excepciones: "Excepciones",
  auto_admisorio: "Auto admisorio", auto_pruebas: "Auto de pruebas", auto_fija_audiencia: "Fija audiencia",
  sentencia: "Sentencia", apelacion: "Apelación", reposicion: "Reposición", queja: "Queja", casacion: "Casación",
  nulidad: "Nulidad", concesion_recurso: "Concesión de recurso", notificacion_personal: "Notif. personal",
  notificacion_estado: "Notif. por estado", notificacion_electronica: "Notif. electrónica",
  emplazamiento: "Emplazamiento", decreto_prueba: "Decreto de prueba", audiencia: "Audiencia",
  mandamiento_pago: "Mandamiento de pago", embargo: "Embargo", secuestro: "Secuestro", remate: "Remate",
  solicitud: "Solicitud", memorial: "Memorial", recurso: "Recurso", auto: "Auto", otro: "Otro",
};
const REL_ES: Record<string, string> = {
  causes: "causa", responds_to: "responde a", appeals: "apela a", confirms: "confirma",
  revokes: "revoca", precede: "precede", precedes: "precede", refers_to: "refiere a", same_as: "idéntico a",
};

const NODE_W = 216;
const NODE_H = 76;
const GAP_X = 34;
const LANE_H = 120;

export function ProcessGraph({
  events,
  onOpenSource,
}: {
  events: GEvent[];
  onOpenSource: (s: GSource) => void;
}) {
  const [selected, setSelected] = useState<GEvent | null>(null);

  const laneOf = (e: GEvent) => {
    const k = e.instance || "otro";
    const i = INSTANCE_ORDER.indexOf(k);
    return i < 0 ? INSTANCE_ORDER.length - 1 : i;
  };
  const lanes = Array.from(new Set(events.map(laneOf))).sort((a, b) => a - b);
  const lanePos = new Map(lanes.map((l, i) => [l, i]));
  const pos = (i: number, e: GEvent) => ({
    x: 24 + i * (NODE_W + GAP_X),
    y: 20 + (lanePos.get(laneOf(e)) ?? 0) * LANE_H,
  });
  const idxById = new Map(events.map((e, i) => [e.id, i]));
  const edges: { from: number; to: number; rel: string }[] = [];
  events.forEach((e, i) => {
    (e.links ?? []).forEach((l) => {
      if (l.direction === "out" && idxById.has(l.other_id)) {
        edges.push({ from: i, to: idxById.get(l.other_id)!, rel: l.relationship });
      }
    });
  });

  const width = 24 + Math.max(1, events.length) * (NODE_W + GAP_X) + 40;
  const height = 24 + Math.max(1, lanes.length) * LANE_H;

  return (
    <div className="space-y-2">
      <div className="overflow-auto rounded-md border bg-background" style={{ maxHeight: "56vh" }}>
        <div className="relative" style={{ width, height }}>
          <svg className="absolute inset-0 text-muted-foreground" width={width} height={height}>
            <defs>
              <marker id="pg-arrow" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">
                <path d="M0,0 L7,3 L0,6 Z" fill="currentColor" />
              </marker>
            </defs>
            {edges.map((ed, k) => {
              const a = pos(ed.from, events[ed.from]);
              const b = pos(ed.to, events[ed.to]);
              const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2;
              const x2 = b.x, y2 = b.y + NODE_H / 2;
              const mx = (x1 + x2) / 2;
              return (
                <g key={k}>
                  <path d={`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`}
                    fill="none" stroke="currentColor" strokeOpacity="0.35" strokeWidth="1.5"
                    markerEnd="url(#pg-arrow)" />
                  <text x={mx} y={(y1 + y2) / 2 - 3} textAnchor="middle" fontSize="9" fill="currentColor">
                    {REL_ES[ed.rel] || ed.rel}
                  </text>
                </g>
              );
            })}
          </svg>
          {events.map((e, i) => {
            const p = pos(i, e);
            return (
              <button key={e.id} type="button" onClick={() => setSelected(e)}
                style={{ left: p.x, top: p.y, width: NODE_W, height: NODE_H }}
                className={cn("absolute overflow-hidden rounded-md border bg-card p-2 text-left shadow-sm hover:border-primary",
                  selected?.id === e.id && "border-primary ring-1 ring-primary")}>
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[10px] text-muted-foreground">{e.code}</span>
                  <span className="text-[10px] text-muted-foreground">{e.event_date || "s/f"}</span>
                </div>
                <div className="mt-0.5 truncate text-xs font-medium">{SUBTYPE_ES[e.subtype || ""] || e.subtype || "Actuación"}</div>
                <div className="truncate text-[10px] text-muted-foreground">{e.actor ? (ACTOR_ES[e.actor] || e.actor) : ""}</div>
              </button>
            );
          })}
        </div>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Filas = instancia ({lanes.map((l) => INSTANCE_ES[INSTANCE_ORDER[l]] || INSTANCE_ORDER[l]).join(" · ")}).
        Haz clic en un nodo para ver su detalle y abrir la evidencia.
      </p>
      {selected && (
        <div className="rounded-md border bg-muted/30 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[11px] text-muted-foreground">{selected.code}</span>
            <span className="text-sm font-semibold">
              {selected.event_date ? new Date(selected.event_date).toLocaleDateString("es-CO") : "Sin fecha"}
            </span>
            {selected.subtype && <Badge variant="secondary" className="text-[10px]">{SUBTYPE_ES[selected.subtype] || selected.subtype}</Badge>}
            {selected.actor && <Badge variant="outline" className="text-[10px]">{ACTOR_ES[selected.actor] || selected.actor}</Badge>}
          </div>
          {selected.authority && <p className="mt-0.5 text-[11px] text-muted-foreground">{selected.authority}</p>}
          <p className="mt-1 text-sm">{selected.description}</p>
          {selected.procedural_effect && (
            <p className="mt-0.5 text-xs text-muted-foreground"><b>Efecto:</b> {selected.procedural_effect}</p>
          )}
          {(selected.links ?? []).length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {(selected.links ?? []).map((l, i) => (
                <span key={i} className="rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground">
                  {l.direction === "out" ? "→" : "←"} {REL_ES[l.relationship] || l.relationship} {l.other_code}
                </span>
              ))}
            </div>
          )}
          {(selected.sources ?? []).length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {(selected.sources ?? []).map((s) => (
                <Button key={s.citation_id} variant="outline" size="sm" className="h-6 gap-1 px-1.5 text-xs"
                  onClick={() => onOpenSource(s)}>
                  {s.source_type === "document_page" ? <FileText className="h-3 w-3" /> : <Video className="h-3 w-3" />}
                  {s.filename || "evidencia"}{s.source_type === "document_page" ? ` p.${s.page}` : ""}
                </Button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
