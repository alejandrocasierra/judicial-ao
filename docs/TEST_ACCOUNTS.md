# Cuentas de prueba / Test accounts

Definidas en `apps/api/seeds/data/seed_data.yaml`. **Sintéticas**: ninguna persona, radicado ni empresa es real.

- Correo: `<usuario>@${SEED_EMAIL_DOMAIN}`. Por defecto el dominio es `example.test`, un dominio reservado por el RFC 2606 que nunca entrega correo.
- Contraseña: el valor de `SEED_DEFAULT_PASSWORD` en tu `.env`. Es aleatoria por entorno y la genera `gen_env.py`.
- El sembrado está **bloqueado** en `staging` y en `production`.

| Organización | Usuario | Rol org. | Rol en expediente "pago $80.000.000" | Para probar |
|---|---|---|---|---|
| alfa (es) | `admin.alfa` | ORG_ADMIN | — (acceso por rol) | legal hold, auditoría, solicitudes de borrado |
| alfa | `gestor.alfa` | CASE_MANAGER | OWNER | membresías, procesamiento |
| alfa | `abogada.alfa` | LAWYER | LAWYER | carga, consultas IA, edición |
| alfa | `revisor.alfa` | REVIEWER | REVIEWER | revisión humana |
| alfa | `analista.alfa` | ANALYST (locale en) | REVIEWER | intersección de permisos: puede consultar, no revisar ni descargar |
| alfa | `lector.alfa` | READ_ONLY | VIEWER | sólo lectura |
| alfa | `externo.alfa` | LAWYER | *no miembro* | ve 404, no 403 |
| alfa | `inactivo.alfa` | LAWYER | — | cuenta desactivada: mismo error que credenciales inválidas |
| beta (en) | `admin.beta` | ORG_ADMIN | — | aislamiento entre tenants |
| beta | `lawyer.beta` | LAWYER | OWNER del caso beta | marcador confidencial que nunca debe filtrarse |

## Expedientes semilla (org alfa)

**Proceso verbal "pago $80.000.000"**, radicado `11001310300120240012300`. Contiene:

- Cuatro PDFs:
  - Demanda.
  - Contestación.
  - Soporte de transferencia, con OCR de baja confianza (0.71).
  - Anexo de correo con un intento de *prompt injection*.
- Una audiencia en video con tres hablantes, uno en cada estado: CONFIRMED, PROBABLE y UNRESOLVED.
- Cuatro claims, dos hechos en disputa, cinco eventos y una contradicción entre documento y testimonio.

**Expediente vacío**, radicado `05001310300220240045600`. Sirve para probar el caso de evidencia insuficiente.

## Rotar credenciales
Borra `.env` y `.env.test`, y después ejecuta `bash scripts/setup_cloud_session.sh` o `bash scripts/bootstrap.sh`.
