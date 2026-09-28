"""Member 8 — Haystack-inspired blob storage (integrated).

Preserves the strongest idea from ``shivam_member_8`` (SHA-256
**content-addressable** storage with de-duplication) and the stricter upload
handling from ``haystack-by-Saurabh`` (streamed writes, size cap, type
allow-list), behind one interface so the rest of the system never cares where
the bytes live.

* ``LocalBlobStore``   — IMPLEMENTED, always available, filesystem-backed.
* ``MinioBlobStore``   — IMPLEMENTED, OPTIONAL (``MEDIA_BACKEND=minio``,
  needs the ``media`` compose profile and the ``minio`` package).

Honesty rule: the local store is **not** MinIO. ``describe()`` always reports
which backend is actually in use, and PostgreSQL stores metadata only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from common.logging import get_logger

logger = get_logger("media")

# Haystack-inspired layout: <root>/<aa>/<bb>/<sha256>.<ext>
SHARD_DEPTH = 2


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def storage_key_for(checksum: str, extension: str = "") -> str:
    """Content-addressed key, prefix-sharded so a directory never explodes."""
    parts = [checksum[i * 2 : (i + 1) * 2] for i in range(SHARD_DEPTH)]
    name = f"{checksum}{extension}" if extension else checksum
    return "/".join([*parts, name])


class MediaBlobStore:
    """Interface: put / get / delete / exists (see events.contracts)."""

    backend = "abstract"

    async def put(
        self, storage_key: str, data: bytes, content_type: str = ""
    ) -> str:  # pragma: no cover
        raise NotImplementedError

    async def get(self, storage_key: str) -> bytes:  # pragma: no cover
        raise NotImplementedError

    async def delete(self, storage_key: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    async def exists(self, storage_key: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"backend": self.backend}


class LocalBlobStore(MediaBlobStore):
    """Filesystem-backed object storage.

    This is a **local** adapter with the same interface as MinIO — it is not
    pretending to be object storage. Paths are content-addressed, so a blob is
    written exactly once no matter how many posts reference it.
    """

    backend = "local_filesystem"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.writes = 0
        self.deduplicated = 0

    def _path(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        if not str(candidate).startswith(str(self.root.resolve())):
            raise ValueError("illegal storage key (path traversal)")
        return candidate

    async def put(self, storage_key: str, data: bytes, content_type: str = "") -> str:
        path = self._path(storage_key)
        if path.exists():
            # Content-addressable de-duplication: the bytes are already there.
            self.deduplicated += 1
            return storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".upload")
        tmp.write_bytes(data)
        tmp.replace(path)
        self.writes += 1
        return storage_key

    async def get(self, storage_key: str) -> bytes:
        path = self._path(storage_key)
        if not path.exists():
            raise FileNotFoundError(storage_key)
        return path.read_bytes()

    async def delete(self, storage_key: str) -> bool:
        path = self._path(storage_key)
        if not path.exists():
            return False
        path.unlink()
        return True

    async def exists(self, storage_key: str) -> bool:
        return self._path(storage_key).exists()

    def describe(self) -> dict[str, Any]:
        files = sum(1 for _ in self.root.rglob("*") if _.is_file())
        return {
            "backend": self.backend,
            "note": "local filesystem adapter — NOT object storage (MinIO is optional)",
            "root": str(self.root),
            "blobs": files,
            "writes": self.writes,
            "deduplicated_writes": self.deduplicated,
        }


class MinioBlobStore(MediaBlobStore):
    """MinIO / S3-compatible object storage (OPTIONAL).

    Requires ``MEDIA_BACKEND=minio`` and the ``minio`` package. If the SDK is
    missing or the endpoint is unreachable, construction raises and the
    platform falls back to :class:`LocalBlobStore` — after logging it, never
    silently.
    """

    backend = "minio"

    def __init__(
        self,
        endpoint: str,
        access_key: str | None,
        secret_key: str | None,
        bucket: str,
        secure: bool = False,
    ) -> None:
        try:
            from minio import Minio  # imported lazily: optional dependency
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError("MEDIA_BACKEND=minio requires the optional 'minio' package") from exc

        self.bucket = bucket
        self.endpoint = endpoint
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)
        except Exception as exc:  # pragma: no cover - network dependent
            raise RuntimeError(f"MinIO unreachable at {self.endpoint}: {exc}") from exc

    async def put(self, storage_key: str, data: bytes, content_type: str = "") -> str:
        import io

        self._client.put_object(
            self.bucket,
            storage_key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type or "application/octet-stream",
        )
        return storage_key

    async def get(self, storage_key: str) -> bytes:
        response = self._client.get_object(self.bucket, storage_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    async def delete(self, storage_key: str) -> bool:
        self._client.remove_object(self.bucket, storage_key)
        return True

    async def exists(self, storage_key: str) -> bool:
        try:
            self._client.stat_object(self.bucket, storage_key)
            return True
        except Exception:  # noqa: BLE001 - MinIO raises its own error types
            return False

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "endpoint": self.endpoint,
            "bucket": self.bucket,
        }


def build_blob_store(settings: Any) -> MediaBlobStore:
    """Factory used by the composition root."""
    if (settings.media_backend or "local").lower() == "minio":
        try:
            return MinioBlobStore(
                endpoint=settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                bucket=settings.minio_bucket,
                secure=settings.minio_secure,
            )
        except Exception as exc:
            logger.warning(
                "minio_unavailable_using_local",
                error=type(exc).__name__,
                detail=str(exc)[:200],
            )
    return LocalBlobStore(settings.media_root)
