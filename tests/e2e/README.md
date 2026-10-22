# E2E de los módulos legales

Playwright sobre la web real y la API local. Cubre:

- pestañas `Expediente` / `Hechos` / `Auditoría`;
- tablero de hechos: `Probados` / `Con prueba` / `Discutidos` / `Todos`,
  lado `Demandante` / `Demandado` / `Ambos`, búsqueda y detalle;
- panel de auditoría: auditoría real, claims, hallazgos;
- botón `Solicitar segunda opinión` (el POST se intercepta: no gasta tokens);
- navegación y overflow horizontal en `360`, `768`, `1024` y `1440`;
- `checksums.json` actualizado (`corpus-manifest.spec.ts` ejecuta
  `scripts/build_checksums.py --check`).

## Requisitos

```bash
docker compose up -d api web
cd tests/e2e
npm install
npx playwright install chromium
```

Credenciales locales: el `global-setup` lee `E2E_EMAIL` / `E2E_PASSWORD` o, si
no existen, `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` del `.env` de
la raíz. No se hardcodean secretos.

Variables opcionales:

| Variable | Default |
|---|---|
| `E2E_BASE_URL` | `http://localhost:3000` |
| `E2E_API_URL` | `http://127.0.0.1:8000/v1` |
| `E2E_CASE_ID` | caso piloto `38865959-…` |

`E2E_BASE_URL` debe ser `localhost` (no `127.0.0.1`) porque `CORS_ALLOWED_ORIGINS`
del API local solo permite `http://localhost:3000`.

## Comandos

```bash
npm test                          # 4 proyectos × 6 pruebas = 24
npm run test:headed               # con navegador visible
npx playwright test --project=chromium-360
npx playwright test --project=chromium-768
npx playwright test --project=chromium-1024
npx playwright test --project=chromium-1440
npm run report                    # reporte HTML
```

Artefactos (traces, screenshots, video, storage state) quedan en
`tests/e2e/artifacts/` y no se versionan.
