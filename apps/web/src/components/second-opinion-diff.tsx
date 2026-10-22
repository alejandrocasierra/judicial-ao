"use client";

import { AlertTriangle, CheckCircle2, Clock, Loader2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type Difference = {
  claim_index: number;
  second_index?: number | null;
  type: string;
  severity?: string;
  reason?: string;
  original_text?: string | null;
  second_text?: string | null;
};

type OriginalClaim = { claim_index: number; claim_text: string; status?: string };
type SecondClaim = {
  claim_index: number;
  claim_text: string;
  comparison: string;
  status: string;
};

export type SecondOpinionDiffRun = {
  id: string;
  status: string;
  verdict?: string | null;
  summary?: string | null;
  error_code?: string | null;
  error_detail?: string | null;
  cost?: number | string | null;
  cost_source?: string | null;
  comparison_mode?: string | null;
  comparison_error?: string | null;
  output?: { comparison?: { verdict?: string; differences?: Difference[]; mode?: string } } | null;
  claims?: SecondClaim[];
  original_claims?: OriginalClaim[];
};

type Row = {
  key: string;
  label: string;
  tone: "match" | "differ" | "uncertain" | "missing" | "extra";
  original?: string;
  second?: string;
  reason?: string;
};

const TONE: Record<Row["tone"], string> = {
  match: "border-emerald-600/40 bg-emerald-500/5",
  differ: "border-rose-600/40 bg-rose-500/5",
  uncertain: "border-amber-600/40 bg-amber-500/5",
  missing: "border-amber-600/40 bg-amber-500/5",
  extra: "border-sky-600/40 bg-sky-500/5",
};

const LABEL: Record<Row["tone"], string> = {
  match: "Coincide",
  differ: "Difiere",
  uncertain: "Incierto",
  missing: "Falta en la segunda",
  extra: "Solo en la segunda",
};

function toneFor(type: string): Row["tone"] {
  if (type === "MATCH") return "match";
  if (type === "DIFFERENT_NUMBER" || type === "DIFFERENT_PARTY" || type === "CONTRADICTED") return "differ";
  if (type === "MISSING") return "missing";
  if (type === "EXTRA") return "extra";
  return "uncertain";
}

function buildRows(run: SecondOpinionDiffRun): Row[] {
  const comparison = run.output?.comparison;
  const differences = comparison?.differences || [];
  const originals = run.original_claims || [];
  const seconds = run.claims || [];
  const rows: Row[] = [];
  const usedSecond = new Set<number>();

  const pushFor = (index: number) => {
    const original = originals.find((o) => o.claim_index === index);
    const diff = differences.find((d) => d.claim_index === index && d.type !== "EXTRA");
    const fallbackSecond = seconds.find((s) => s.claim_index === index);
    const type = diff?.type || (diff ? "UNCERTAIN" : "UNCERTAIN");
    const secondText = diff?.second_text || fallbackSecond?.claim_text;
    if (!original && !secondText) return;
    if (diff?.second_index != null) usedSecond.add(diff.second_index);
    if (!diff && fallbackSecond) usedSecond.add(fallbackSecond.claim_index);
    rows.push({
      key: `c${index}`,
      label: LABEL[toneFor(type)],
      tone: toneFor(type),
      original: original?.claim_text || diff?.original_text || undefined,
      second: secondText || undefined,
      reason: diff?.reason,
    });
  };

  for (const original of originals) pushFor(original.claim_index);

  differences
    .filter((d) => d.type === "EXTRA")
    .forEach((diff, position) => {
      const second = seconds.find((s) => s.claim_index === diff.second_index)
        || seconds.find((s) => s.claim_text === diff.second_text);
      rows.push({
        key: `extra-${diff.second_index ?? position}`,
        label: LABEL.extra,
        tone: "extra",
        second: diff.second_text || second?.claim_text,
        reason: diff.reason,
      });
    });

  // Si la corrida no guardó `original_claims`, se muestran al menos los claims
  // del verificador con su comparación, sin inventar el texto original.
  if (!originals.length && !rows.length) {
    for (const second of seconds) {
      const tone = second.comparison === "MATCH"
        ? "match"
        : second.comparison === "CONTRADICTED"
          ? "differ"
          : second.comparison === "EXTRA"
            ? "extra"
            : "uncertain";
      rows.push({
        key: `s${second.claim_index}`,
        label: LABEL[tone],
        tone,
        second: second.claim_text,
      });
    }
  }
  return rows;
}

/** Diff lado a lado por claim. No decide cuál respuesta es verdadera. */
export function SecondOpinionDiff({ run }: { run: SecondOpinionDiffRun }) {
  if (["QUEUED", "RUNNING"].includes(run.status)) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-md border p-8 text-center text-sm text-muted-foreground">
        <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
        <p className="font-medium text-foreground">Segunda opinión en curso…</p>
        <p>El verificador responde sin ver la respuesta original y luego se compara claim por claim.</p>
      </div>
    );
  }

  if (run.status === "CANCELLED") {
    return (
      <div className="flex flex-col items-center gap-2 rounded-md border p-8 text-center text-sm text-muted-foreground">
        <Clock className="h-6 w-6" aria-hidden="true" />
        <p className="font-medium text-foreground">Segunda opinión cancelada</p>
        <p>No se publicó ningún veredicto. Puedes solicitarla de nuevo desde la respuesta.</p>
      </div>
    );
  }

  if (run.status === "FAILED") {
    return (
      <div className="flex flex-col items-center gap-2 rounded-md border border-rose-600/30 p-8 text-center text-sm">
        <XCircle className="h-6 w-6 text-rose-600" aria-hidden="true" />
        <p className="font-medium">No se pudo completar la segunda opinión</p>
        <p className="text-muted-foreground">
          {run.error_detail || run.error_code || "Error del verificador o del comparador."}
        </p>
        <p className="text-muted-foreground">La respuesta original no se modifica; queda pendiente de revisión.</p>
      </div>
    );
  }

  const rows = buildRows(run);
  if (!rows.length) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-md border p-8 text-center text-sm text-muted-foreground">
        <AlertTriangle className="h-6 w-6" aria-hidden="true" />
        <p className="font-medium text-foreground">Sin diferencias registradas</p>
        <p>La corrida terminó sin claims comparables.</p>
      </div>
    );
  }

  const compareMode = run.output?.comparison?.mode || run.comparison_mode || "DETERMINISTIC";
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {run.verdict && <Badge variant="secondary">{run.verdict}</Badge>}
        <Badge variant="outline">{compareMode === "LLM" ? "Comparador LLM" : "Comparador determinista"}</Badge>
        <span className="text-xs text-muted-foreground">
          {run.cost != null && Number(run.cost) > 0
            ? `Costo ${Number(run.cost).toFixed(6)} USD (${run.cost_source === "MODEL_PRICE" ? "precio del modelo" : "estimado"})`
            : "Costo no registrado"}
        </span>
      </div>
      {run.comparison_error && (
        <p className="rounded-md border border-amber-600/30 p-2 text-xs text-amber-700 dark:text-amber-300">
          Fallback determinista: {run.comparison_error}
        </p>
      )}
      <div className="grid grid-cols-2 gap-2 text-xs font-medium text-muted-foreground">
        <p>Respuesta original</p>
        <p>Segunda opinión</p>
      </div>
      <div className="space-y-2">
        {rows.map((row) => (
          <div key={row.key} className={cn("rounded-md border p-3", TONE[row.tone])}>
            <div className="mb-2 flex items-center gap-2">
              {row.tone === "match"
                ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" aria-hidden="true" />
                : <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />}
              <span className="text-xs font-medium">{row.label}</span>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              <div className="min-w-0 rounded border bg-background/60 p-2">
                <p className="mb-1 text-[11px] font-medium text-muted-foreground md:hidden">Original</p>
                <p className="leading-5">{row.original || "—"}</p>
              </div>
              <div className="min-w-0 rounded border bg-background/60 p-2">
                <p className="mb-1 text-[11px] font-medium text-muted-foreground md:hidden">Segunda opinión</p>
                <p className="leading-5">{row.second || "—"}</p>
              </div>
            </div>
            {row.reason && <p className="mt-2 text-xs text-muted-foreground">{row.reason}</p>}
          </div>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        La coincidencia no convierte una afirmación en verdad judicial. Un veredicto incierto o discrepante exige revisión humana.
      </p>
    </div>
  );
}
