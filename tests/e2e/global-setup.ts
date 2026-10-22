import { request } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * Autentica UNA vez y deja la sesión persistida para todos los proyectos.
 * Evita el rate limit del endpoint /auth/login y no guarda secretos en el repo
 * (el archivo vive en tests/e2e/artifacts/, ignorado).
 */
const ROOT = path.resolve(__dirname, "..", "..");
const API = process.env.E2E_API_URL || "http://127.0.0.1:8000/v1";
const WEB = process.env.E2E_BASE_URL || "http://localhost:3000";

function readEnvFile(): Record<string, string> {
  const file = path.join(ROOT, ".env");
  if (!fs.existsSync(file)) return {};
  const out: Record<string, string> = {};
  for (const raw of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const index = line.indexOf("=");
    const key = line.slice(0, index).trim();
    let value = line.slice(index + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

export default async function globalSetup() {
  const env = readEnvFile();
  const email = process.env.E2E_EMAIL || env.BOOTSTRAP_ADMIN_EMAIL;
  const password = process.env.E2E_PASSWORD || env.BOOTSTRAP_ADMIN_PASSWORD;
  if (!email || !password) {
    throw new Error(
      "Faltan credenciales E2E. Define E2E_EMAIL/E2E_PASSWORD o " +
        "BOOTSTRAP_ADMIN_EMAIL/BOOTSTRAP_ADMIN_PASSWORD en .env (local).",
    );
  }

  const context = await request.newContext();
  try {
    const login = await context.post(`${API}/auth/login`, {
      data: { email, password },
      headers: { "Content-Type": "application/json" },
    });
    if (!login.ok()) {
      throw new Error(
        `Login E2E falló (${login.status()}). Verifica API en ${API}.`,
      );
    }
    const tokens = await login.json();
    const me = await context.get(`${API}/auth/me`, {
      headers: { Authorization: `Bearer ${tokens.access_token}` },
    });
    if (!me.ok()) throw new Error(`No se pudo leer /auth/me (${me.status()}).`);
    const user = await me.json();
    const authStorage = JSON.stringify({
      state: {
        user,
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token,
        isAuthenticated: true,
      },
      version: 0,
    });
    const state = {
      cookies: [],
      origins: [
        {
          origin: WEB,
          localStorage: [
            { name: "access_token", value: tokens.access_token },
            { name: "refresh_token", value: tokens.refresh_token },
            { name: "auth-storage", value: authStorage },
          ],
        },
      ],
    };
    const target = path.join(__dirname, "artifacts", "storage-state.json");
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, JSON.stringify(state, null, 2));
  } finally {
    await context.dispose();
  }
}
