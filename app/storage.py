from pathlib import Path
import hashlib
from fastapi import UploadFile
from app.config import MEDIA_ROOT, ALLOWED_TYPES, MAX_FILE_SIZE

def ensure_storage():
    root = Path(MEDIA_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    (root / "images").mkdir(exist_ok=True)
    (root / "videos").mkdir(exist_ok=True)

def media_directory(content_type: str):
    return ALLOWED_TYPES[content_type]

def save_upload(upload: UploadFile, destination: Path):
    size = 0

    with destination.open("wb") as output:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break

            size += len(chunk)
            if size > MAX_FILE_SIZE:
                output.close()
                destination.unlink(missing_ok=True)
                raise ValueError(
                    f"File exceeds {MAX_FILE_SIZE // (1024 * 1024)} MB limit"
                )

            output.write(chunk)

    return size

def calculate_hash(file_path: Path):
    sha256 = hashlib.sha256()
    size = 0

    with file_path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            sha256.update(chunk)
            size += len(chunk)

    return sha256.hexdigest(), size
