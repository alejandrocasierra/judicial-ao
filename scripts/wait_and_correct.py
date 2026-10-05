"""Espera a que termine process_all_pdfs.py y aplica post-corrección OCR."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROOT / "var" / "process_all_pdfs.pid"

CASE_ID = "182a09e0-deeb-4918-80f6-19cf350b4087"
ORG_ID = "b4e6d687-89b0-4b79-a0cc-69bd3532a7e9"
USER_ID = "fc7ced6f-58e9-46c5-aa7c-b155c424c0fd"


def process_alive(pid: int) -> bool:
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(1, False, pid)
    if handle == 0:
        return False
    kernel32.CloseHandle(handle)
    return True


def wait_for_batch(timeout_seconds: int = 8 * 3600) -> bool:
    start = time.time()
    while time.time() - start < timeout_seconds:
        if not PID_FILE.exists():
            time.sleep(10)
            continue
        pid = int(PID_FILE.read_text(encoding="utf-8-sig").strip())
        if not process_alive(pid):
            return True
        time.sleep(30)
    return False


def main() -> None:
    print("Esperando a que termine el batch OCR...")
    finished = wait_for_batch()
    if not finished:
        print("Timeout esperando batch.")
        sys.exit(1)
    print("Batch terminado. Aplicando post-corrección...")
    subprocess.run([
        str(ROOT / ".venv" / "Scripts" / "python.exe"),
        str(ROOT / "scripts" / "post_correct_ocr.py"),
        "--case-id", CASE_ID,
        "--org-id", ORG_ID,
        "--user-id", USER_ID,
    ], check=True)
    print("Post-corrección aplicada.")


if __name__ == "__main__":
    main()
