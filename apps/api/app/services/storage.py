"""Object storage (SSD §6.3). Las claves NUNCA usan el nombre de archivo del usuario."""
from __future__ import annotations

import hashlib
import shutil
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings


def original_key(case_id: str, sha256: str, kind: str) -> str:
    return f"cases/{case_id}/{'originals' if kind == 'document' else 'media'}/{sha256}"


def normalize_prefix(prefix: str) -> str:
    """Normaliza la carpeta (prefijo) del bucket: 'judicial-ai/prod' -> 'judicial-ai/prod/'."""
    p = (prefix or "").strip("/")
    return (p + "/") if p else ""


def prefixed(prefix: str, key: str) -> str:
    """Clave física dentro del bucket (carpeta + clave lógica)."""
    return normalize_prefix(prefix) + key


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("storage key escapes root")
        return p

    def put(self, key: str, data: bytes, overwrite: bool = False) -> str:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        if overwrite or not p.exists():  # write-once por defecto: originales inmutables
            p.write_bytes(data)
        return f"local://{key}"

    def put_file(self, key: str, source: Path) -> str:
        """Copia un archivo local al storage sin cargarlo en memoria."""
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():  # write-once
            shutil.copy2(source, p)
        return f"local://{key}"

    def presign_put(self, key: str, content_type: str | None, expires: int = 3600) -> str | None:
        """El storage local no soporta subida directa (se sube por la API)."""
        return None

    def get(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    def get_range(self, key: str, start: int, end: int) -> bytes:
        """Rango inclusivo [start, end] para streaming (HTTP Range)."""
        with open(self._p(key), "rb") as f:
            f.seek(start)
            return f.read(end - start + 1)

    def sha256(self, key: str) -> str:
        h = hashlib.sha256()
        with open(self._p(key), "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def delete_prefix(self, prefix: str) -> int:
        """Borra todos los objetos bajo un prefijo (purga de expediente). Devuelve
        el número de archivos eliminados; best-effort si el árbol no existe."""
        p = self._p(prefix)
        if not p.exists():
            return 0
        count = sum(1 for f in p.rglob("*") if f.is_file())
        shutil.rmtree(p, ignore_errors=True)
        return count


class S3Storage:
    def __init__(self):
        import boto3
        s = get_settings()
        self.bucket = s.S3_BUCKET
        self.prefix = s.S3_PREFIX
        self.sse = (s.S3_SSE or "").strip()  # "" = sin cabecera (GCS cifra por defecto)
        self.c = boto3.client("s3", endpoint_url=s.S3_ENDPOINT_URL, region_name=s.S3_REGION,
                              aws_access_key_id=s.S3_ACCESS_KEY, aws_secret_access_key=s.S3_SECRET_KEY)

    def _key(self, key: str) -> str:
        """Clave física dentro del bucket (aplica la carpeta/prefijo configurado)."""
        return prefixed(self.prefix, key)

    def _extra(self) -> dict:
        return {"ServerSideEncryption": self.sse} if self.sse else {}

    def put(self, key: str, data: bytes, overwrite: bool = False) -> str:
        self.c.put_object(Bucket=self.bucket, Key=self._key(key), Body=data, **self._extra())
        return f"s3://{self.bucket}/{key}"

    def put_file(self, key: str, source: Path) -> str:
        self.c.upload_file(str(source), self.bucket, self._key(key), ExtraArgs=self._extra() or None)
        return f"s3://{self.bucket}/{key}"

    def presign_put(self, key: str, content_type: str | None, expires: int = 3600) -> str | None:
        """URL prefirmada para subir DIRECTO al bucket (evita el proxy de Cloudflare)."""
        params: dict = {"Bucket": self.bucket, "Key": self._key(key)}
        if content_type:
            params["ContentType"] = content_type
        params.update(self._extra())
        return self.c.generate_presigned_url("put_object", Params=params, ExpiresIn=expires)

    def get(self, key: str) -> bytes:
        return self.c.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()

    def get_range(self, key: str, start: int, end: int) -> bytes:
        """Rango inclusivo [start, end] (HTTP Range) directo desde S3."""
        return self.c.get_object(Bucket=self.bucket, Key=self._key(key),
                                 Range=f"bytes={start}-{end}")["Body"].read()

    def size(self, key: str) -> int | None:
        """Tamaño del objeto remoto, o None si no existe."""
        try:
            return int(self.c.head_object(Bucket=self.bucket, Key=self._key(key))["ContentLength"])
        except Exception:  # noqa: BLE001
            return None

    def sha256(self, key: str) -> str:
        h = hashlib.sha256()
        body = self.c.get_object(Bucket=self.bucket, Key=self._key(key))["Body"]
        for chunk in body.iter_chunks(1024 * 1024):
            h.update(chunk)
        return h.hexdigest()

    def delete_prefix(self, prefix: str) -> int:
        """Borra todos los objetos bajo un prefijo (purga de expediente)."""
        count = 0
        paginator = self.c.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self._key(prefix)):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self.c.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})
                count += len(objs)
        return count


class GcsStorage:
    """Google Cloud Storage nativo (usa la cuenta de servicio de GOOGLE_APPLICATION_CREDENTIALS).

    Reutiliza S3_BUCKET como nombre del bucket y S3_PREFIX como carpeta
    (p. ej. bucket=welladvisor, prefijo=judicial-ai/dev). El URI lógico es
    `gs://{bucket}/{clave}`; el prefijo se aplica solo a la clave física.
    """

    def __init__(self):
        from google.cloud import storage as gcs
        s = get_settings()
        self.bucket_name = s.S3_BUCKET
        self.prefix = s.S3_PREFIX
        self.client = gcs.Client()  # credenciales desde GOOGLE_APPLICATION_CREDENTIALS
        self.bucket = self.client.bucket(self.bucket_name)

    def _key(self, key: str) -> str:
        return prefixed(self.prefix, key)

    def put(self, key: str, data: bytes, overwrite: bool = False) -> str:
        self.bucket.blob(self._key(key)).upload_from_string(data)
        return f"gs://{self.bucket_name}/{key}"

    def put_file(self, key: str, source: Path) -> str:
        self.bucket.blob(self._key(key)).upload_from_filename(str(source))
        return f"gs://{self.bucket_name}/{key}"

    def presign_put(self, key: str, content_type: str | None, expires: int = 3600) -> str | None:
        """URL firmada (v4) para subir DIRECTO al bucket (evita el proxy de Cloudflare)."""
        from datetime import timedelta
        blob = self.bucket.blob(self._key(key))
        return blob.generate_signed_url(version="v4", expiration=timedelta(seconds=expires),
                                        method="PUT", content_type=content_type or "application/octet-stream")

    def get(self, key: str) -> bytes:
        return self.bucket.blob(self._key(key)).download_as_bytes()

    def get_range(self, key: str, start: int, end: int) -> bytes:
        """Rango inclusivo [start, end] (HTTP Range); GCS usa fin exclusivo."""
        return self.bucket.blob(self._key(key)).download_as_bytes(start=start, end=end + 1)

    def size(self, key: str) -> int | None:
        """Tamaño del objeto remoto, o None si no existe."""
        blob = self.bucket.blob(self._key(key))
        try:
            blob.reload()
            return int(blob.size or -1)
        except Exception:  # noqa: BLE001
            return None

    def sha256(self, key: str) -> str:
        h = hashlib.sha256()
        with self.bucket.blob(self._key(key)).open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def delete_prefix(self, prefix: str) -> int:
        count = 0
        for blob in self.client.list_blobs(self.bucket_name, prefix=self._key(prefix)):
            blob.delete()
            count += 1
        return count


@lru_cache
def storage():
    s = get_settings()
    if s.STORAGE_BACKEND == "gcs":
        return GcsStorage()
    if s.STORAGE_BACKEND == "s3":
        return S3Storage()
    return LocalStorage(s.path(s.STORAGE_LOCAL_ROOT))


def key_from_uri(uri: str) -> str:
    if uri.startswith("local://"):
        return uri[len("local://"):]
    return uri.split("/", 3)[3]
