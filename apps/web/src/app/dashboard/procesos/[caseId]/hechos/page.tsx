"use client";

import { useParams } from "next/navigation";
import { ProcessTabs } from "@/components/process-tabs";
import { FactsBoard } from "@/components/facts-board";

export default function HechosPage() {
  const { caseId } = useParams<{ caseId: string }>();
  return (
    <div className="space-y-4">
      <ProcessTabs caseId={caseId} />
      <FactsBoard caseId={caseId} />
    </div>
  );
}
