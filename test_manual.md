# Manual Test Plan

## 1. Start

```bash
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/docs
```

## 2. Upload

Use `/media/upload` and provide:

```text
user_id = 1
file = sample.jpg
```

Expected:

```text
duplicate = false
```

## 3. Upload the same file again

Expected:

```text
duplicate = true
```

The SHA-256 should be identical.

## 4. Check metadata

```text
GET /media/{media_id}
```

## 5. Download

```text
GET /media/{media_id}/file
```

## 6. Check statistics

```text
GET /media/stats/summary
```

## 7. Delete

```text
DELETE /media/{media_id}
```
