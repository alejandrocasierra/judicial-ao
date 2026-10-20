"""Monitor de procesamiento ASR para el caso 11001310302120180036100.

Modos:
    python scripts/monitor_asr.py              # snapshot una sola vez
    python scripts/monitor_asr.py --watch      # loop cada 60 s
    python scripts/monitor_asr.py --repair     # detecta y repara fallos/ huérfanos

Reparaciones (requieren conexión a BD; usa las mismas credenciales que la API):
- Videos en FAILED -> estado ASR_PENDING + nuevo job file_ingest.
- Videos ASR_RUNNING/ASR_PENDING sin job activo -> nuevo job file_ingest.
- Videos ASR_COMPLETE sin diarización pendiente/completa -> encolar media_diarize.
"""
from __future__ import annotations

import argparse
import tempfile
import os
import pathlib
import sys
import time
from datetime import datetime, timezone
from typing import Any

import psutil
import requests
from sqlalchemy import text


# Cargar .env si no vienen variables de entorno.
if not os.environ.get("JUDICIAL_ADMIN_PASSWORD"):
    try:
        from dotenv import load_dotenv
        load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] {exc}")

BASE = os.environ.get("JUDICIAL_API_BASE", "http://127.0.0.1:8000/v1")
CASE = os.environ.get("JUDICIAL_CASE_ID", "")
if not CASE:
    raise SystemExit("Falta JUDICIAL_CASE_ID (uuid del expediente a monitorear)")
ORG_ID = os.environ.get("JUDICIAL_ORG_ID", "")
USER_ID = os.environ.get("JUDICIAL_USER_ID", "")
EMAIL = os.environ.get("JUDICIAL_ADMIN_EMAIL", "admin@judicial.ai")
PASSWORD = os.environ.get("JUDICIAL_ADMIN_PASSWORD") or os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
if not PASSWORD:
    raise SystemExit("Falta JUDICIAL_ADMIN_PASSWORD (o BOOTSTRAP_ADMIN_PASSWORD en .env)")

LOCK_FILE = pathlib.Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "Temp" / "opencode" / "monitor_asr.lock"


def acquire_lock() -> bool:
    """Evita que corran dos instancias del monitor (lock atómico en Windows)."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    current_pid = os.getpid()

    # 1) Intento atómico de crear el lockfile. Si ya existe, falla.
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as fh:
            fh.write(str(current_pid))
        return True
    except FileExistsError:
        pass

    # 2) Lockfile existente: verificar si el dueño sigue vivo.
    try:
        old_pid = int(LOCK_FILE.read_text().strip())
        if old_pid != current_pid and psutil.pid_exists(old_pid):
            try:
                cmd = " ".join(psutil.Process(old_pid).cmdline())
                if "monitor_asr.py" in cmd:
                    print(f"[lock] Ya hay un monitor corriendo (PID {old_pid}).")
                    return False
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] no se pudo inspeccionar el PID {old_pid}: {exc}")
        # Lock abandonado: reutilizar.
        LOCK_FILE.write_text(str(current_pid))
        return True
    except Exception:
        # Si no podemos leer, intentar forzar la creación.
        try:
            LOCK_FILE.write_text(str(current_pid))
            return True
        except Exception:
            return False


def release_lock() -> None:
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] {exc}")


# Nombres a seguir (opcional): JUDICIAL_MEDIA_NAMES="a.mp4,b.mov". Vacío = todos.
_NAMES_ENV = os.environ.get("JUDICIAL_MEDIA_NAMES", "").strip()
TARGET_NAMES = {n.strip() for n in _NAMES_ENV.split(",") if n.strip()}

_STALL_MINUTES = 30  # sin actualización -> considerado colgado


def login() -> str:
    r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    token = r.json()["access_token"]
    # Organización y usuario salen de /auth/me: no se hardcodean en el script.
    global ORG_ID, USER_ID
    if not ORG_ID or not USER_ID:
        me = requests.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=30)
        me.raise_for_status()
        data = me.json()
        ORG_ID = ORG_ID or str(data.get("organization_id") or "")
        USER_ID = USER_ID or str(data.get("id") or "")
    if not ORG_ID or not USER_ID:
        raise SystemExit("No se pudo obtener organization_id/user_id de /auth/me")
    return token


def _fmt_dt(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        ago = (datetime.now(timezone.utc) - dt).total_seconds()
        if ago < 60:
            return f"hace {int(ago)}s"
        if ago < 3600:
            return f"hace {int(ago/60)}m"
        return f"hace {int(ago/3600)}h {int((ago%3600)/60)}m"
    except Exception:
        return str(iso)[:19]


def _db():
    """Lazily import módulos de BD."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "apps" / "api"))
    sys.path.insert(0, str(repo_root / "scripts"))
    from app.core.db import rows, tx
    return rows, tx


def fetch_state(token: str) -> dict[str, Any]:
    """Trae jobs activos, progreso y lista de archivos media del caso."""
    active = requests.get(f"{BASE}/cases/processing/active", headers={"Authorization": f"Bearer {token}"}, timeout=30).json()
    jobs = requests.get(f"{BASE}/cases/{CASE}/processing", headers={"Authorization": f"Bearer {token}"}, timeout=30).json()

    rows, tx = _db()
    org_id = ORG_ID
    user_id = USER_ID
    with tx(org_id, user_id) as conn:
        media_rows = rows(conn, """
            SELECT m.id, m.filename, m.processing_status, m.duration_ms, m.folder_id, m.created_at,
                   pr.pct AS live_pct, pr.detail AS live_detail
            FROM media m
            LEFT JOIN media_asr_progress pr ON pr.media_id = m.id
            WHERE m.case_id = :c
            ORDER BY m.filename
        """, c=CASE)
    return {"active": active, "media": media_rows, "jobs": jobs}


def _active_for_media(active: dict, media_id: str) -> dict | None:
    for job in active.get("jobs", []):
        for item in job.get("items", []):
            if item.get("id") == media_id:
                return {**job, "item": item}
    return None


def build_report(state: dict) -> list[dict]:
    active_jobs = state["active"]
    all_media = state["media"]

    report: list[dict] = []
    for it in all_media:
        if TARGET_NAMES and it.get("filename") not in TARGET_NAMES:
            continue
        mid = str(it["id"])
        aj = _active_for_media(active_jobs, mid)
        job_id = str(aj["id"]) if aj else None
        job_status = aj["status"] if aj else None
        job_updated = aj.get("updated_at") if aj else None
        # Progreso: si hay job activo con detalle, usarlo; si no, usar media_asr_progress.
        if aj:
            progress = aj["item"]["progress"]
            detail = aj["item"].get("detail")
        elif it.get("live_pct") is not None:
            progress = min(99, int(it["live_pct"]))
            detail = it.get("live_detail")
        elif it["processing_status"] in ("ASR_COMPLETE", "REVIEW_REQUIRED"):
            progress = 100
            detail = None
        else:
            progress = 0
            detail = None

        # Determinar si está colgado: RUNNING sin actualización reciente.
        stalled = False
        if job_status == "RUNNING" and job_updated:
            try:
                updated = datetime.fromisoformat(job_updated.replace("Z", "+00:00"))
                stalled = (datetime.now(timezone.utc) - updated).total_seconds() > _STALL_MINUTES * 60
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] no se pudo parsear la fecha del job: {exc}")

        report.append({
            "media_id": mid,
            "filename": it["filename"],
            "status": it["processing_status"],
            "progress": progress,
            "detail": detail,
            "job_id": job_id,
            "job_status": job_status,
            "updated": job_updated,
            "stalled": stalled,
        })
    return report


def print_report(report: list[dict]) -> None:
    print(f"\n{'='*100}")
    print(f"Monitor ASR - caso {CASE} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'Archivo':<55} {'Estado':<18} {'Progreso':>10} {'Job':>10} {'JobStatus':>12} {'Actualizado':>16}")
    print("-" * 100)
    for r in report:
        flag = " [COLGADO]" if r["stalled"] else ""
        job_short = r["job_id"][:8] if r["job_id"] else "—"
        print(f"{r['filename']:<55} {r['status']:<18} {r['progress']:>9}%{flag:<10} {job_short:>10} {str(r['job_status']):>12} {_fmt_dt(r['updated']):>16}")

    total = len(report)
    done = sum(1 for r in report if r["status"] in ("ASR_COMPLETE", "REVIEW_REQUIRED"))
    failed = sum(1 for r in report if r["status"] == "FAILED")
    stalled = sum(1 for r in report if r["stalled"])
    running = sum(1 for r in report if r["status"] == "ASR_RUNNING")
    print("-" * 100)
    print(f"Total: {total} | Completados: {done} | ASR en curso: {running} | Fallados: {failed} | Colgados: {stalled}")
    print(f"{'='*100}\n")


def _db_modules():
    """Importa los módulos de la API para reparaciones (requiere estar en apps/api)."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "apps" / "api"))
    sys.path.insert(0, str(repo_root / "scripts"))
    from app.core.config import get_settings
    from app.core.db import one, rows, tx
    from app.workers.dispatcher import create_job, enqueue_job
    return get_settings, one, rows, tx, create_job, enqueue_job


def repair(report: list[dict]) -> list[str]:
    """Repara videos fallidos o huérfanos usando la BD directamente."""
    get_settings, one, rows, tx, create_job, enqueue_job = _db_modules()
    s = get_settings()
    org_id = ORG_ID
    user_id = USER_ID

    actions: list[str] = []

    for r in report:
        mid = r["media_id"]
        status = r["status"]
        stalled = r["stalled"]
        needs_reingest = False
        reason = ""

        # Sólo reingestamos en casos concretos para evitar duplicados masivos.
        if status == "FAILED":
            needs_reingest = True
            reason = "estado FAILED"
        elif stalled:
            needs_reingest = True
            reason = "job RUNNING colgado >30 min"
        elif status in ("UPLOADED", "ASR_PENDING"):
            # Si ya hay un job activo visible, no tocamos.
            if r["job_id"]:
                pass
            else:
                # Si hay algún job reciente en estados finales/activos (no CANCELLED),
                # no duplicamos; el worker debe encolar la siguiente etapa.
                with tx(org_id, user_id) as conn:
                    recent = one(conn, """
                        SELECT id, status, EXTRACT(EPOCH FROM (now()-updated_at))/3600 AS hours
                        FROM jobs
                        WHERE case_id = :c AND input_ids @> ARRAY[:m]::uuid[]
                          AND status IN ('QUEUED','RUNNING','RETRYING','SUCCEEDED','FAILED')
                        ORDER BY updated_at DESC LIMIT 1
                    """, c=CASE, m=mid)
                if recent and recent["status"] in ("QUEUED", "RUNNING", "RETRYING"):
                    pass
                elif recent and recent["status"] == "SUCCEEDED" and recent["hours"] < 24:
                    pass
                else:
                    needs_reingest = True
                    reason = "sin job reciente útil"

        if needs_reingest:
            try:
                with tx(org_id, user_id) as conn:
                    media = one(conn, "SELECT id, filename, sha256, storage_uri, folder_id FROM media WHERE id = :m AND case_id = :c",
                                m=mid, c=CASE)
                    if not media:
                        actions.append(f"[SKIP] {r['filename']}: no encontrado en BD")
                        continue
                    # Cancelar jobs activos huérfanos antes de crear uno nuevo
                    conn.execute(text("""
                        UPDATE jobs SET status = 'CANCELLED', updated_at = now()
                        WHERE case_id = :c AND input_ids @> ARRAY[:m]::uuid[]
                          AND status IN ('QUEUED', 'RUNNING', 'RETRYING')
                    """), {"c": CASE, "m": mid})
                    # Resetear estado
                    one(conn, "UPDATE media SET processing_status = 'ASR_PENDING' WHERE id = :m RETURNING id", m=mid)
                    # Crear nuevo job file_ingest con clave única de reintento
                    ts = int(time.time())
                    job = create_job(conn, org_id=org_id, case_id=CASE, job_type="file_ingest",
                                     input_ids=[mid], actor_id=user_id,
                                     key_parts=["file_ingest", CASE, mid, "repair", ts],
                                     pipeline_version=s.PIPELINE_VERSION, model_version=s.LLM_MODEL)
                enqueue_job(job["id"], org_id, user_id, "file_ingest")
                actions.append(f"[REINGEST] {r['filename']} ({reason}) -> job {str(job['id'])[:8]}")
            except Exception as exc:
                actions.append(f"[ERROR] {r['filename']}: {exc}")

        # Videos ASR_COMPLETE que aún no tengan diarización en curso ni completa.
        if status in ("ASR_COMPLETE", "REVIEW_REQUIRED"):
            try:
                with tx(org_id, user_id) as conn:
                    # Nunca duplicar si YA hay una diarización activa (la consulta
                    # anterior solo miraba la última y podía repetir el job).
                    active = one(conn, """
                        SELECT id FROM jobs
                        WHERE case_id = :c AND job_type = 'media_diarize'
                          AND status IN ('QUEUED','RUNNING','RETRYING')
                          AND :m = ANY(input_ids)
                        LIMIT 1
                    """, c=CASE, m=mid)
                    last = one(conn, """
                        SELECT id, status FROM jobs
                        WHERE case_id = :c AND job_type = 'media_diarize'
                          AND :m = ANY(input_ids)
                        ORDER BY created_at DESC LIMIT 1
                    """, c=CASE, m=mid)
                    if not active and (not last or last["status"] == "FAILED"):
                        ts = int(time.time())
                        job = create_job(conn, org_id=org_id, case_id=CASE, job_type="media_diarize",
                                         input_ids=[mid], actor_id=user_id,
                                         key_parts=["media_diarize", CASE, mid, "repair", ts],
                                         pipeline_version=s.PIPELINE_VERSION, model_version=s.LLM_MODEL)
                        enqueue_job(job["id"], org_id, user_id, "media_diarize")
                        actions.append(f"[DIARIZE] {r['filename']} -> job {str(job['id'])[:8]}")
            except Exception as exc:
                actions.append(f"[ERROR diarize] {r['filename']}: {exc}")

    return actions


class _LogFile:
    """Envuelve un archivo para que cada write haga flush (logs en tiempo real)."""
    def __init__(self, path: pathlib.Path):
        self._fh = open(path, "a", encoding="utf-8")

    def write(self, s: str) -> int:
        n = self._fh.write(s)
        self._fh.flush()
        return n

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Monitor de ASR")
    p.add_argument("--watch", action="store_true", help="Loop continuo cada 60 s")
    p.add_argument("--repair", action="store_true", help="Repara fallos/huérfanos detectados")
    p.add_argument("--interval", type=int, default=60, help="Segundos entre ciclos (default 60)")
    p.add_argument("--log-file", type=pathlib.Path,
                   default=pathlib.Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "Temp" / "opencode" / "monitor_asr.log",
                   help="Archivo de log")
    args = p.parse_args()

    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    log_fh = _LogFile(args.log_file)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = log_fh
    sys.stderr = log_fh
    try:
        if not acquire_lock():
            return 2
        try:
            token = login()
            print("[login] OK", flush=True)

            while True:
                try:
                    state = fetch_state(token)
                    report = build_report(state)
                    print_report(report)

                    if args.repair:
                        actions = repair(report)
                        if actions:
                            print("Reparaciones aplicadas:")
                            for a in actions:
                                print(f"  {a}")
                        else:
                            print("No se requirieron reparaciones.")
                        print()

                    if not args.watch:
                        break
                    time.sleep(args.interval)
                except requests.exceptions.HTTPError as exc:
                    if exc.response.status_code == 401:
                        print("[token] renovando...", flush=True)
                        token = login()
                    else:
                        print(f"[ERROR] {exc}", flush=True)
                        if not args.watch:
                            return 1
                        time.sleep(args.interval)
                except Exception as exc:
                    print(f"[ERROR] {exc}", flush=True)
                    if not args.watch:
                        return 1
                    time.sleep(args.interval)

            return 0
        finally:
            release_lock()
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        log_fh.close()


if __name__ == "__main__":
    sys.exit(main())
