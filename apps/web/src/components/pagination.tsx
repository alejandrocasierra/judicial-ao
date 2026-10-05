"use client";

import { Button } from "@/components/ui/button";

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  onPageSize: (size: number) => void;
  pageSizeOptions?: number[];
}

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
  onPageSize,
  pageSizeOptions = [10, 20, 50, 100],
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);

  return (
    <div className="mt-4 flex flex-col items-center justify-between gap-3 border-t pt-4 sm:flex-row">
      <p className="text-sm text-muted-foreground">
        Mostrando <b>{start}</b>–<b>{end}</b> de <b>{total}</b> registros
      </p>
      <div className="flex items-center gap-2">
        <label className="text-sm text-muted-foreground">Por página</label>
        <select
          value={pageSize}
          onChange={(e) => { onPageSize(Number(e.target.value)); onPage(1); }}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        >
          {pageSizeOptions.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        <Button variant="outline" size="sm" onClick={() => onPage(Math.max(1, page - 1))} disabled={page <= 1}>
          Anterior
        </Button>
        <span className="px-2 text-sm">{page} / {totalPages}</span>
        <Button variant="outline" size="sm" onClick={() => onPage(Math.min(totalPages, page + 1))} disabled={page >= totalPages}>
          Siguiente
        </Button>
      </div>
    </div>
  );
}
