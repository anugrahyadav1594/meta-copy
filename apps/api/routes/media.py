"""Media API (Member 8) — upload/download/list/delete + backend info.

Blobs live in a :class:`MediaBlobStore` (local filesystem by default, MinIO
when ``MEDIA_BACKEND=minio``). PostgreSQL stores metadata only.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from api.dependencies import Platform, get_platform

router = APIRouter(prefix="/media", tags=["media"])


def _service(platform: Platform = Depends(get_platform)) -> Any:
    if platform.media_service is None:
        raise HTTPException(status_code=503, detail="media service is not initialised")
    return platform.media_service


@router.post("/upload")
async def upload_media(
    owner_id: int = Form(..., gt=0),
    file: UploadFile = File(...),
    post_id: int | None = Form(default=None),
    svc: Any = Depends(_service),
) -> dict[str, Any]:
    """Store the blob (content-addressed by SHA-256) + a metadata row."""
    data = await file.read()
    try:
        return await svc.upload(
            owner_id=owner_id,
            filename=file.filename or "upload",
            data=data,
            content_type=file.content_type,
            post_id=post_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc


@router.get("/{media_id}")
async def download_media(media_id: int, svc: Any = Depends(_service)) -> Response:
    try:
        data, row = await svc.download(media_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=data, media_type=row.mime_type)


@router.get("/")
async def list_media(
    owner_id: int = Query(..., gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    svc: Any = Depends(_service),
) -> dict[str, Any]:
    return {"owner_id": owner_id, "items": await svc.list_for_owner(owner_id, limit=limit)}


@router.delete("/{media_id}")
async def delete_media(media_id: int, svc: Any = Depends(_service)) -> dict[str, Any]:
    deleted = await svc.delete(media_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"media {media_id} not found")
    return {"deleted": True, "media_id": media_id}


@router.get("/_stats/backend")
async def backend_stats(svc: Any = Depends(_service)) -> dict[str, Any]:
    """Which blob backend is ACTUALLY in use (local is never reported as MinIO)."""
    return svc.stats()
