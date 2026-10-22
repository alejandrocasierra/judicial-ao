"use client";

import { useParams } from "next/navigation";
import { ProcessTabs } from "@/components/process-tabs";
import { VerificationPolicyForm } from "@/components/verification-policy-form";

export default function VerificacionProcesoPage() {
  const { caseId } = useParams<{ caseId: string }>();
  return (
    <div className="space-y-4">
      <ProcessTabs caseId={caseId} />
      <VerificationPolicyForm caseId={caseId} />
    </div>
  );
}
