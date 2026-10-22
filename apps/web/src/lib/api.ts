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

/** El endpoint respondió, pero no con `text/event-stream` (ruta ausente o
 * versión del API sin streaming). El llamador debe usar el POST normal. */
export class StreamUnsupportedError extends Error {
  constructor(public status: number) {
    super("El servidor no ofrece streaming para esta ruta");
    this.name = "StreamUnsupportedError";
  }
}

/** El stream cerró sin evento `done`: la respuesta quedó incompleta y el
 * llamador debe reintentar por el POST normal. */
export class StreamIncompleteError extends Error {
  constructor() {
    super("El stream terminó antes de completar la respuesta");
    this.name = "StreamIncompleteError";
  }
}

export type StreamEventListener = (event: string, data: unknown) => void;

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

/**
 * POST que consume Server-Sent Events sobre `fetch` (el endpoint exige body, así
 * que `EventSource` no sirve). Devuelve los datos del evento `done`.
 *
 * - Reintenta una vez tras refrescar el token si el primer intento da 401.
 * - Lanza `StreamUnsupportedError` si el servidor no responde con SSE.
 * - Lanza `StreamIncompleteError` si el cuerpo termina sin `done`.
 * - `signal` permite cancelar la lectura (AbortSignal del llamador).
 */
async function requestStream<T>(
  path: string,
  body: unknown,
  onEvent: StreamEventListener,
  signal?: AbortSignal,
  retried = false,
): Promise<T> {
  const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
  const res = await fetch(apiUrl(path), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });

  if (res.status === 401 && !retried && !path.startsWith("/auth/")) {
    const ok = await tryRefresh();
    if (ok) return requestStream<T>(path, body, onEvent, signal, true);
    hardLogout();
  }

  if (!res.ok) {
    // La ruta de streaming no existe en esta versión del API: el llamador debe
    // usar el POST normal en lugar de tratarlo como un error del turno.
    if (res.status === 404 || res.status === 405 || res.status === 501) {
      throw new StreamUnsupportedError(res.status);
    }
    const errBody = await res.json().catch(() => ({ error: { code: "INTERNAL_ERROR" } }));
    const code = errBody?.error?.code || "INTERNAL_ERROR";
    throw new ApiError(code, res.status, errBody?.error?.message || res.statusText);
  }

  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("text/event-stream") || !res.body) {
    throw new StreamUnsupportedError(res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let donePayload: T | null = null;
  let finished = false;

  const dispatch = (block: string) => {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of block.split(/\r?\n/)) {
      if (!line || line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      else if (field === "data") dataLines.push(value);
    }
    if (dataLines.length === 0) return;
    const raw = dataLines.join("\n");
    let data: unknown = raw;
    try { data = JSON.parse(raw); } catch { /* evento de texto plano */ }
    if (event === "done") {
      donePayload = data as T;
      finished = true;
    }
    onEvent(event, data);
  };

  try {
    // `done` cierra el turno: no se espera a que el servidor cierre el socket.
    outer: for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      let sep = buffer.search(/\r?\n\r?\n/);
      while (sep >= 0) {
        const block = buffer.slice(0, sep);
        // El separador mide 4 bytes si es CRLF y 2 si es LF.
        buffer = buffer.slice(sep + (buffer[sep] === "\r" ? 4 : 2));
        dispatch(block);
        if (finished) break outer;
        sep = buffer.search(/\r?\n\r?\n/);
      }
    }
    if (!finished) {
      buffer += decoder.decode();
      if (buffer.trim()) dispatch(buffer);
    }
  } finally {
    if (finished) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }

  if (donePayload === null) throw new StreamIncompleteError();
  return donePayload;
}

/** URL directa con token para <video>/<img> (no pueden enviar cabeceras).
 * Permite streaming HTTP Range: el navegador reproduce y salta sin descargar todo. */
export function streamUrl(path: string): string {
  const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
  const sep = path.includes("?") ? "&" : "?";
  return `${apiUrl(path)}${token ? `${sep}token=${encodeURIComponent(token)}` : ""}`;
}

/** Sube `multipart/form-data` por XHR para tener PROGRESO real (fetch no lo da). */
export function apiUploadXhr<T>(path: string, form: FormData, onProgress?: (p: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
    const xhr = new XMLHttpRequest();
    xhr.open("POST", apiUrl(path));
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.timeout = 0; // sin límite: archivos grandes por internet lento
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let body: { error?: { code?: string; message?: string }; results?: unknown } | null = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* respuesta no-JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as T);
      else reject(new ApiError(body?.error?.code || "INTERNAL_ERROR", xhr.status, body?.error?.message || xhr.statusText));
    };
    xhr.onerror = () => reject(new Error("Error de red al subir"));
    xhr.ontimeout = () => reject(new Error("Tiempo de subida agotado"));
    xhr.send(form);
  });
}

export const api = {  get: <T>(path: string, params?: Record<string, string>) =>
    request<T>(path, { method: "GET", params }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  /** POST con Server-Sent Events: `onEvent` recibe cada `event:`/`data:` y la
   * promesa resuelve con el payload de `done`. */
  postStream: <T>(path: string, body: unknown, onEvent: StreamEventListener, signal?: AbortSignal) =>
    requestStream<T>(path, body, onEvent, signal),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
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
