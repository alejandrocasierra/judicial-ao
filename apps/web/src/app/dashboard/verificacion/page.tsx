import { redirect } from "next/navigation";

/**
 * Compatibilidad: la verificación dejó de ser una pantalla global con UUID.
 * Ahora vive dentro del proceso, en /dashboard/procesos/[caseId]/verificacion.
 */
export default function VerificacionPage() {
  redirect("/dashboard/procesos");
}
