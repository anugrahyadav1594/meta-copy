import hashlib
import os
from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel

app = FastAPI(title="MetaScale Media Service - Shivam (Member 8)")

# Local folder jo object storage (MinIO/S3 simulation) ki tarah kaam karega
UPLOAD_DIR = "./storage_bucket"
os.makedirs(UPLOAD_DIR, exist_ok=True)

class MediaResponse(BaseModel):
    status: str
    message: str
    media_id: str
    checksum_sha256: str
    file_size_bytes: int
    storage_path: str

@app.post("/media/upload", response_model=MediaResponse)
async def upload_media(file: UploadFile = File(...)):
    # 1. File ke bytes read karo
    file_bytes = await file.read()
    file_size = len(file_bytes)
    
    if file_size == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # 2. Haystack-Inspired: SHA-256 Hash nikalo (Content-Addressable Storage)
    sha256_hash = hashlib.sha256(file_bytes).hexdigest()
    
    # 3. File extension nikalo
    file_extension = os.path.splitext(file.filename)[1]
    
    # Unique filename based on hash (Deduplication mechanism)
    unique_filename = f"{sha256_hash}{file_extension}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)
    
    # 4. Check karo ki kya yeh file pehle se stored hai?
    file_exists = os.path.exists(file_path)
    
    if not file_exists:
        with open(file_path, "wb") as buffer:
            buffer.write(file_bytes)
        message = "File uploaded and stored successfully."
    else:
        message = "Duplicate media detected! Reused existing storage via content-addressable hash."

    media_id = f"med_{sha256_hash[:12]}"

    return {
        "status": "success",
        "message": message,
        "media_id": media_id,
        "checksum_sha256": sha256_hash,
        "file_size_bytes": file_size,
        "storage_path": file_path
    }

@app.get("/media/{media_id}")
def get_media_metadata(media_id: str):
    return {
        "media_id": media_id,
        "status": "active",
        "storage_type": "Haystack-inspired Object Storage Mock"
    }

@app.get("/")
def health_check():
    return {"service": "Media Storage Service", "status": "Running", "owner": "Shivam (Member 8)"}
