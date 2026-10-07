"use client";

/** Renderizador Markdown ligero (sin dependencias): encabezados `#`-`####`, negrita,
 * código inline, listas con viñetas/numéricas, reglas horizontales y párrafos.
 * Se usa en el chat y en la vista de Chunks del visor OCR. */

import { type ReactNode } from "react";

function inline(s: string, key: string): ReactNode[] {
  return s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((seg, i) => {
    if (seg.startsWith("**") && seg.endsWith("**")) return <strong key={`${key}-${i}`}>{seg.slice(2, -2)}</strong>;
    if (seg.startsWith("`") && seg.endsWith("`"))
      return <code key={`${key}-${i}`} className="rounded bg-background/60 px-1 text-xs">{seg.slice(1, -1)}</code>;
    return <span key={`${key}-${i}`}>{seg}</span>;
  });
}

const HEADING_CLASS: Record<number, string> = {
  1: "mt-3 text-lg font-semibold",
  2: "mt-3 text-base font-semibold",
  3: "mt-2 text-sm font-semibold",
  4: "mt-2 text-xs font-semibold uppercase tracking-wide",
};
const HEADING_TAG = { 1: "h3", 2: "h4", 3: "h5", 4: "h6" } as const;

export function Markdown({ text, className }: { text: string; className?: string }) {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushPara = (k: string) => {
    if (para.length) {
      blocks.push(<p key={k}>{inline(para.join(" "), k)}</p>);
      para = [];
    }
  };
  const flushList = (k: string) => {
    if (list && list.items.length) {
      const items = list.items.map((li, i) => <li key={i}>{inline(li, `${k}-${i}`)}</li>);
      blocks.push(
        list.ordered
          ? <ol key={k} className="ml-5 list-decimal space-y-0.5">{items}</ol>
          : <ul key={k} className="ml-4 list-disc space-y-0.5">{items}</ul>,
      );
    }
    list = null;
  };

  lines.forEach((raw, i) => {
    const t = raw.trim();
    if (!t) { flushPara(`p${i}`); flushList(`l${i}`); return; }

    const h = t.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flushPara(`p${i}`); flushList(`l${i}`);
      const level = Math.min(h[1].length, 4);
      const Tag = HEADING_TAG[level as 1 | 2 | 3 | 4];
      blocks.push(<Tag key={`h${i}`} className={HEADING_CLASS[level]}>{inline(h[2], `h${i}`)}</Tag>);
      return;
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) {
      flushPara(`p${i}`); flushList(`l${i}`);
      blocks.push(<hr key={`r${i}`} className="my-2 border-border" />);
      return;
    }

    const ul = t.match(/^[-•*·]\s+(.*)$/);
    if (ul) {
      flushPara(`p${i}`);
      if (!list || list.ordered) { flushList(`l${i}`); list = { ordered: false, items: [] }; }
      list.items.push(ul[1]);
      return;
    }
    const ol = t.match(/^\d{1,3}[.)]\s+(.*)$/);
    if (ol) {
      flushPara(`p${i}`);
      if (!list || !list.ordered) { flushList(`l${i}`); list = { ordered: true, items: [] }; }
      list.items.push(ol[1]);
      return;
    }

    flushList(`l${i}`);
    para.push(t);
  });
  flushPara("p-end"); flushList("l-end");

  return <div className={className ? `space-y-1 ${className}` : "space-y-1"}>{blocks}</div>;
}
