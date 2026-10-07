"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Pagination } from "@/components/pagination";
import { DocumentOcrViewer } from "@/components/document-ocr-viewer";
import { FileText, Search } from "lucide-react";

interface Case { id: string; case_number: string; title: string; }
interface Doc { id: string; filename: string; title?: string; document_type?: string; page_count?: number; processing_status: string; cuaderno?: string; }

export default function PdfsPage() {
  const [search, setSearch] = useState("");
  const [caseId, setCaseId] = useState<string>("");
  const [viewDoc, setViewDoc] = useState<Doc | null>(null);
  const [listPage, setListPage] = useState(1);
  const [listPageSize, setListPageSize] = useState(20);
  // Enlace directo opcional: /dashboard/pdfs?case=<id>&doc=<id>
  const [pendingCaseId, setPendingCaseId] = useState<string | null>(null);
  const [pendingDocId, setPendingDocId] = useState<string | null>(null);
  // Enlace directo: se leen los parámetros en el siguiente frame (evita el desajuste
  // de hidratación sin actualizar el estado de forma síncrona dentro del efecto).
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      const params = new URLSearchParams(window.location.search);
      setPendingCaseId(params.get("case"));
      setPendingDocId(params.get("doc"));
    });
    return () => cancelAnimationFrame(id);
  }, []);

  const { data: cases = [] } = useQuery({ queryKey: ["cases"], queryFn: () => api.get<Case[]>("/cases") });
  // Expediente activo derivado: el elegido, el del enlace directo o el primero.
  const activeCaseId = caseId || (pendingCaseId && cases.some((c) => c.id === pendingCaseId) ? pendingCaseId : "") || cases[0]?.id || "";

  const { data: docs = [], isLoading } = useQuery({
    queryKey: ["documents", activeCaseId],
    queryFn: () => api.get<Doc[]>(`/cases/${activeCaseId}/documents`),
    enabled: !!activeCaseId,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((d) => ["UPLOADED", "OCR_PENDING", "OCR_RUNNING"].includes(d.processing_status)) ? 4000 : false,
  });

  // Apertura directa vía ?doc=<id>: se resuelve cuando la lista lo contiene.
  const [handledPendingDoc, setHandledPendingDoc] = useState<string | null>(null);
  if (pendingDocId && pendingDocId !== handledPendingDoc && docs.length) {
    const found = docs.find((x) => x.id === pendingDocId);
    if (found) { setHandledPendingDoc(pendingDocId); setViewDoc(found); setPendingDocId(null); }
  }

  const filtered = docs
    .filter((d) => (d.title || d.filename).toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => (b.page_count || 0) - (a.page_count || 0));
  const pageItems = filtered.slice((listPage - 1) * listPageSize, listPage * listPageSize);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">PDFs</h1>
          <p className="text-muted-foreground">Abre un cuadernillo, revisa el OCR por hoja y corrígelo</p>
        </div>
        <Select value={activeCaseId} onValueChange={(v) => { setCaseId(v); setListPage(1); }}>
          <SelectTrigger className="w-full sm:w-80"><SelectValue placeholder="Selecciona expediente" /></SelectTrigger>
          <SelectContent>{cases.map((c) => <SelectItem key={c.id} value={c.id}>{c.case_number} — {c.title}</SelectItem>)}</SelectContent>
        </Select>
      </div>

      <Card>
        <CardHeader>
          <div className="relative"><Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input placeholder="Buscar documento…" className="pl-8" value={search} onChange={(e) => { setSearch(e.target.value); setListPage(1); }} /></div>
        </CardHeader>
        <CardContent>
          {isLoading ? <p className="text-muted-foreground">Cargando…</p> : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12"><FileText className="mb-4 h-12 w-12 text-muted-foreground" />
              <p className="text-muted-foreground">No se encontraron documentos</p></div>
          ) : (
            <>
              <div className="space-y-2">
                {pageItems.map((d) => (
                  <div key={d.id} className="flex items-center justify-between rounded-md border p-3 hover:bg-muted/50">
                    <div className="flex items-center gap-3">
                      <FileText className="h-5 w-5 text-muted-foreground" />
                      <div>
                        <p className="font-medium">{d.title || d.filename}</p>
                        <p className="text-sm text-muted-foreground">
                          {d.cuaderno ? `Cuaderno ${d.cuaderno} · ` : ""}{d.page_count ?? 0} págs · {d.processing_status}
                        </p>
                      </div>
                    </div>
                    <Button variant="outline" size="sm" onClick={() => setViewDoc(d)}>Ver OCR</Button>
                  </div>
                ))}
              </div>
              <Pagination page={listPage} pageSize={listPageSize} total={filtered.length}
                          onPage={setListPage} onPageSize={setListPageSize} />
            </>
          )}
        </CardContent>
      </Card>

      <DocumentOcrViewer
        caseId={activeCaseId}
        documentId={viewDoc?.id ?? null}
        filename={viewDoc?.title || viewDoc?.filename}
        onClose={() => setViewDoc(null)}
      />
    </div>
  );
}
