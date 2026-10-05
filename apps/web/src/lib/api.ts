declare global {
  interface Window { __API_URL__?: string }
}

/** URL base del API. En producción se inyecta en RUNTIME desde el servidor
 * (`window.__API_URL__`, tomado de la env `API_PUBLIC_URL`), de modo que la misma
 * imagen sirve en localhost o en GCP sin recompilar. Admite valor absoluto
 * (p. ej. https://api.dominio/v1) o relativo al mismo origen (p. ej. /v1). */
function apiBase(): string {
  if (typeof window !== "undefined" && window.__API_URL__) return window.__API_URL__;
  return process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/v1";
}

/** Concatena la base con el path; si la base es relativa, la resuelve contra el origen actual. */
function apiUrl(path: string): string {
  const base = apiBase().replace(/\/$/, "");
  if (/^https?:\/\//i.test(base)) return base + path;
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  return `${origin}${base}${path}`;
}

export class ApiError extends Error {
  constructor(
    public code: string,
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let refreshPromise: Promise<boolean> | null = null;

/** Renueva el access token con el refresh token (una sola vez en paralelo). */
async function tryRefresh(): Promise<boolean> {
  if (refreshPromise) return refreshPromise;
  const refreshToken = typeof window !== "undefined" ? localStorage.getItem("refresh_token") : null;
  if (!refreshToken) return false;
  refreshPromise = (async () => {
    try {
      const res = await fetch(apiUrl("/auth/refresh"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!res.ok) return false;
      const data = await res.json();
      localStorage.setItem("access_token", data.access_token);
      localStorage.setItem("refresh_token", data.refresh_token);
      return true;
    } catch {
      return false;
    } finally {
      refreshPromise = null;
    }
  })();
  return refreshPromise;
}

function hardLogout() {
  if (typeof window === "undefined") return;
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  // Navegación forzada fuera de React (módulo sin acceso al router): recarga completa
  // para limpiar todo el estado en memoria tras invalidar la sesión.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  if (window.location.pathname !== "/login") window.location.href = "/login";
}

async function request<T>(
  path: string,
  options: RequestInit & { params?: Record<string, string> } = {},
  retried = false,
): Promise<T> {
  const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
  const url = new URL(apiUrl(path));
  if (options.params) {
    for (const [k, v] of Object.entries(options.params)) url.searchParams.set(k, v);
  }

  const res = await fetch(url.toString(), {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  // Access token expirado: intenta renovar y reintenta una sola vez.
  if (res.status === 401 && !retried && !path.startsWith("/auth/")) {
    const ok = await tryRefresh();
    if (ok) return request<T>(path, options, true);
    hardLogout();
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: { code: "INTERNAL_ERROR" } }));
    const code = body?.error?.code || "INTERNAL_ERROR";
    throw new ApiError(code, res.status, body?.error?.message || res.statusText);
  }

  return res.json();
}

/** URL directa con token para <video>/<img> (no pueden enviar cabeceras).
 * Permite streaming HTTP Range: el navegador reproduce y salta sin descargar todo. */
export function streamUrl(path: string): string {
  const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
  const sep = path.includes("?") ? "&" : "?";
  return `${apiUrl(path)}${token ? `${sep}token=${encodeURIComponent(token)}` : ""}`;
}

export const api = {
  get: <T>(path: string, params?: Record<string, string>) =>
    request<T>(path, { method: "GET", params }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string) =>
    request<T>(path, { method: "DELETE" }),
  blob: async (path: string): Promise<Blob> => {
    const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
    const res = await fetch(apiUrl(path), {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new Error("No se pudo cargar el recurso");
    return res.blob();
  },
  /** Sube archivos como multipart/form-data (sin Content-Type JSON). */
  upload: async <T>(path: string, form: FormData, retried = false): Promise<T> => {
    const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
    const res = await fetch(apiUrl(path), {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    });
    if (res.status === 401 && !retried) {
      const ok = await tryRefresh();
      if (ok) return api.upload<T>(path, form, true);
      hardLogout();
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({ error: { code: "INTERNAL_ERROR" } }));
      const code = body?.error?.code || "INTERNAL_ERROR";
      throw new ApiError(code, res.status, body?.error?.message || res.statusText);
    }
    return res.json();
  },
};
