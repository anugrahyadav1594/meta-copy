# MetaScale Media Storage Service

GitHub-ready implementation of Person 8's media-storage subsystem.

## Purpose

This service demonstrates a Haystack-inspired design:

- Large image/video files are stored outside MySQL.
- MySQL stores only media metadata.
- SHA-256 identifies file content.
- Duplicate binaries are stored only once.
- REST APIs support upload, retrieval, download, deletion, and statistics.

## Stack

- Python 3.10+
- FastAPI
- MySQL 8+
- Local file storage
- SHA-256

## Setup

1. Create the database:

```sql
CREATE DATABASE metascale;
```

2. Run:

```bash
mysql -u root -p metascale < schema.sql
```

3. Create a virtual environment:

```bash
python -m venv venv
```

Windows:
```bash
venv\Scripts\activate
```

Linux/macOS:
```bash
source venv/bin/activate
```

4. Install packages:

```bash
pip install -r requirements.txt
```

5. Copy `.env.example` to `.env` and enter your MySQL password.

6. Start:

```bash
uvicorn app.main:app --reload
```

7. Open the API documentation:

```text
http://127.0.0.1:8000/docs
```

## API

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | Health check |
| POST | `/media/upload` | Upload image/video |
| GET | `/media/{media_id}` | Metadata |
| GET | `/media/{media_id}/file` | Download file |
| DELETE | `/media/{media_id}` | Delete media |
| GET | `/media/stats/summary` | Storage statistics |

## Example upload

```bash
curl -X POST "http://127.0.0.1:8000/media/upload" -F "user_id=1" -F "file=@sample.jpg"
```

Upload the same file again. The second response should show:

```json
"duplicate": true
```

## Architecture

```text
User
 |
 v
FastAPI
 |
 +---- SHA-256
 |
 +---- MySQL metadata
 |
 +---- media-storage/images or videos
```

## Integration

The `user_id` is currently an integer so this service can connect to the team's final `users` table later. See `docs/integration.md`.
