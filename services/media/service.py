"""Member 8 — media service: object storage + canonical metadata.

Flow (as required):

    POST /api/v1/media/upload
        -> MediaService
        -> MediaBlobStore (LocalBlobStore | MinioBlobStore)
        -> metadata row in canonical PostgreSQL `media`
        -> MEDIA_CREATED domain event

PostgreSQL stores **metadata only** (storage key, checksum, size, type). The
blob is content-addressed by SHA-256, so an identical upload is stored once
and simply re-linked — de-duplication is real, not claimed.
"""

from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import Any

from common.logging import get_logger
from media.blobstore import MediaBlobStore, sha256_hex, storage_key_for

logger = get_logger("media")

ALLOWED_TYPES: dict[str, tuple[str, ...]] = {
    "image": (".jpg", ".jpeg", ".png", ".gif", ".webp"),
    "video": (".mp4", ".webm", ".mov"),
    "audio": (".mp3", ".ogg", ".wav"),
    "document": (".pdf", ".txt", ".csv"),
}


def classify(filename: str | None, content_type: str | None) -> tuple[str, str, str]:
    """Return ``(media_type, mime_type, extension)`` or raise ValueError."""
    suffix = Path(filename or "").suffix.lower()
    mime = (content_type or mimetypes.guess_type(filename or "")[0] or "").lower()
    for kind, extensions in ALLOWED_TYPES.items():
        if suffix in extensions:
            return kind, mime or f"{kind}/{suffix.lstrip('.')}", suffix
    if mime.startswith("image/"):
        return "image", mime, suffix or ".bin"
    if mime.startswith("video/"):
        return "video", mime, suffix or ".bin"
    if mime.startswith("audio/"):
        return "audio", mime, suffix or ".bin"
    raise ValueError(
        "unsupported media type; allowed: "
        + ", ".join(ext for exts in ALLOWED_TYPES.values() for ext in exts)
    )


class MediaService:
    """Upload/download/delete: blob store + canonical metadata row."""

    def __init__(
        self,
        media_repo: Any,
        blob_store: MediaBlobStore,
        event_bus: Any = None,
        *,
        max_upload_mb: int = 25,
    ) -> None:
        self.media = media_repo
        self.blobs = blob_store
        self.events = event_bus
        self.max_upload_bytes = max_upload_mb * 1024 * 1024
        self.uploads = 0
        self.deduplicated = 0
        self.bytes_stored = 0

    async def upload(
        self,
        *,
        owner_id: int,
        filename: str,
        data: bytes,
        content_type: str | None = None,
        post_id: int | None = None,
    ) -> dict[str, Any]:
        if not data:
            raise ValueError("uploaded file is empty")
        if len(data) > self.max_upload_bytes:
            raise ValueError(f"file exceeds {self.max_upload_bytes // (1024 * 1024)} MB limit")
        media_type, mime_type, extension = classify(filename, content_type)
        checksum = sha256_hex(data)
        key = storage_key_for(checksum, extension)

        # Content-addressable de-duplication: if a metadata row with this
        # checksum already exists, the blob is stored once and we simply
        # re-link it. storage_key is UNIQUE, so inserting a second row with
        # the same key would violate the constraint.
        duplicate = await self.media.find_by_checksum(checksum)
        if duplicate is not None:
            self.deduplicated += 1
            row = duplicate
        else:
            await self.blobs.put(key, data, mime_type)
            self.bytes_stored += len(data)
            self.uploads += 1
            row = await self.media.create(
                owner_id=owner_id,
                storage_key=key,
                media_type=media_type,
                mime_type=mime_type,
                size_bytes=len(data),
                checksum_sha256=checksum,
                post_id=post_id,
            )

        if self.events is not None:
            from events.bus import DomainEvent

            await self.events.publish(
                DomainEvent(
                    event_type="MEDIA_CREATED",
                    aggregate="media",
                    aggregate_id=int(getattr(row, "media_id", 0)),
                    payload={
                        "owner_id": owner_id,
                        "storage_key": key,
                        "checksum_sha256": checksum,
                        "size_bytes": len(data),
                        "media_type": media_type,
                        "post_id": post_id,
                        "deduplicated": duplicate is not None,
                    },
                )
            )

        payload = self._serialize(row)
        payload["deduplicated"] = duplicate is not None
        payload["backend"] = self.blobs.backend
        return payload

    async def download(self, media_id: int) -> tuple[bytes, Any]:
        row = await self.media.get_by_id(media_id)
        if row is None:
            raise LookupError(f"media {media_id} not found")
        data = await self.blobs.get(row.storage_key)
        return data, row

    async def delete(self, media_id: int) -> bool:
        row = await self.media.get_by_id(media_id)
        if row is None:
            return False
        await self.media.delete(media_id)
        # Only remove the blob when no other metadata row references it
        # (content-addressed blobs are shared between uploads).
        still_linked = await self.media.find_by_checksum(row.checksum_sha256 or "")
        if still_linked is None:
            await self.blobs.delete(row.storage_key)
        return True

    async def list_for_owner(self, owner_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self.media.list_by_owner(owner_id, limit=limit)
        return [self._serialize(r) for r in rows]

    @staticmethod
    def _serialize(row: Any) -> dict[str, Any]:
        created = getattr(row, "created_at", None)
        return {
            "media_id": getattr(row, "media_id", None),
            "owner_id": getattr(row, "owner_id", None),
            "post_id": getattr(row, "post_id", None),
            "storage_key": getattr(row, "storage_key", None),
            "checksum_sha256": getattr(row, "checksum_sha256", None),
            "media_type": getattr(row, "media_type", None),
            "mime_type": getattr(row, "mime_type", None),
            "size_bytes": getattr(row, "size_bytes", 0),
            "created_at": created.isoformat() if created else None,
        }

    def stats(self) -> dict[str, Any]:
        stats = {
            "backend": self.blobs.backend,
            "uploads": self.uploads,
            "deduplicated_uploads": self.deduplicated,
            "bytes_stored": self.bytes_stored,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        stats.update(self.blobs.describe())
        return stats
