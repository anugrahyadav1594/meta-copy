from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse

from app.config import MEDIA_ROOT, ALLOWED_TYPES
from app.database import get_connection
from app.storage import (
    ensure_storage,
    media_directory,
    save_upload,
    calculate_hash,
)

app = FastAPI(
    title="MetaScale Media Storage Service",
    description="Haystack-inspired media storage subsystem.",
    version="1.0.0",
)

ensure_storage()


@app.get("/")
def health():
    return {
        "service": "media-storage-service",
        "status": "running",
    }


@app.post("/media/upload")
def upload_media(
    user_id: int = Form(...),
    file: UploadFile = File(...),
):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Use a supported image or video format.",
        )

    directory_name = media_directory(file.content_type)
    original_name = Path(file.filename or "uploaded_file").name

    temp_path = Path(MEDIA_ROOT) / directory_name / f".upload_{uuid4().hex}.tmp"

    try:
        size = save_upload(file, temp_path)
        file_hash, verified_size = calculate_hash(temp_path)

        if size != verified_size:
            raise HTTPException(
                status_code=500,
                detail="File size verification failed.",
            )

        connection = get_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT media_id, file_path
            FROM media
            WHERE file_hash = %s
            LIMIT 1
            """,
            (file_hash,),
        )
        duplicate = cursor.fetchone()

        if duplicate:
            temp_path.unlink(missing_ok=True)
            file_path = duplicate["file_path"]
            is_duplicate = True
        else:
            final_name = f"{file_hash}_{original_name}"
            final_path = Path(MEDIA_ROOT) / directory_name / final_name
            temp_path.replace(final_path)
            file_path = str(final_path).replace("\\", "/")
            is_duplicate = False

        cursor.execute(
            """
            INSERT INTO media
            (user_id, file_name, file_type, file_size, file_hash, file_path)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                original_name,
                file.content_type,
                size,
                file_hash,
                file_path,
            ),
        )

        media_id = cursor.lastrowid
        connection.commit()

        cursor.close()
        connection.close()

        return {
            "message": "Media uploaded successfully",
            "media_id": media_id,
            "file_name": original_name,
            "file_type": file.content_type,
            "file_size": size,
            "sha256": file_hash,
            "duplicate": is_duplicate,
            "stored_path": file_path,
        }

    except HTTPException:
        temp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/media/{media_id}")
def get_media_metadata(media_id: int):
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute(
        "SELECT * FROM media WHERE media_id = %s",
        (media_id,),
    )
    media = cursor.fetchone()

    cursor.close()
    connection.close()

    if not media:
        raise HTTPException(status_code=404, detail="Media not found")

    return media


@app.get("/media/{media_id}/file")
def download_media(media_id: int):
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute(
        "SELECT file_name, file_type, file_path FROM media WHERE media_id = %s",
        (media_id,),
    )
    media = cursor.fetchone()

    cursor.close()
    connection.close()

    if not media:
        raise HTTPException(status_code=404, detail="Media not found")

    path = Path(media["file_path"])

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Physical media file not found",
        )

    return FileResponse(
        path=str(path),
        media_type=media["file_type"],
        filename=media["file_name"],
    )


@app.delete("/media/{media_id}")
def delete_media(media_id: int):
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute(
        "SELECT file_path, file_hash FROM media WHERE media_id = %s",
        (media_id,),
    )
    media = cursor.fetchone()

    if not media:
        cursor.close()
        connection.close()
        raise HTTPException(status_code=404, detail="Media not found")

    cursor.execute(
        "DELETE FROM media WHERE media_id = %s",
        (media_id,),
    )
    connection.commit()

    cursor.execute(
        "SELECT COUNT(*) AS count FROM media WHERE file_hash = %s",
        (media["file_hash"],),
    )
    remaining = cursor.fetchone()["count"]

    physical_deleted = False

    if remaining == 0:
        path = Path(media["file_path"])
        if path.exists():
            path.unlink()
            physical_deleted = True

    cursor.close()
    connection.close()

    return {
        "message": "Media record deleted",
        "physical_file_deleted": physical_deleted,
    }


@app.get("/media/stats/summary")
def media_stats():
    connection = get_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT
            COUNT(*) AS total_media_records,
            COUNT(DISTINCT file_hash) AS unique_files,
            COALESCE(SUM(file_size), 0) AS logical_storage_bytes,
            COALESCE(
                SUM(CASE WHEN file_type LIKE 'image/%' THEN 1 ELSE 0 END),
                0
            ) AS images,
            COALESCE(
                SUM(CASE WHEN file_type LIKE 'video/%' THEN 1 ELSE 0 END),
                0
            ) AS videos
        FROM media
        """
    )
    stats = cursor.fetchone()

    cursor.execute(
        """
        SELECT COALESCE(SUM(file_size), 0) AS unique_storage_bytes
        FROM (
            SELECT file_hash, MAX(file_size) AS file_size
            FROM media
            GROUP BY file_hash
        ) AS unique_media
        """
    )
    unique_stats = cursor.fetchone()

    cursor.close()
    connection.close()

    logical_storage = int(stats["logical_storage_bytes"])
    unique_storage = int(unique_stats["unique_storage_bytes"])

    return {
        "total_media_records": int(stats["total_media_records"]),
        "unique_files": int(stats["unique_files"]),
        "images": int(stats["images"]),
        "videos": int(stats["videos"]),
        "logical_storage_bytes": logical_storage,
        "physical_storage_bytes": unique_storage,
        "estimated_storage_saved_bytes": max(
            logical_storage - unique_storage, 0
        ),
    }
