"use client";

import { useParams } from "next/navigation";
import { ProcessTabs } from "@/components/process-tabs";
import { AuditPanel } from "@/components/audit-panel";

export default function AuditoriaPage() {
  const { caseId } = useParams<{ caseId: string }>();
  return (
    <div className="space-y-4">
      <ProcessTabs caseId={caseId} />
      <AuditPanel caseId={caseId} />
    </div>
  );
}
