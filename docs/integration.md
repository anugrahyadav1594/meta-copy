# Integration Guide

Person 8 owns the media-storage subsystem.

## Service contract

Other modules can use:

```text
POST /media/upload
GET  /media/{media_id}
GET  /media/{media_id}/file
DELETE /media/{media_id}
GET  /media/stats/summary
```

## Current database design

The module uses:

```text
user_id
file_name
file_type
file_size
file_hash
file_path
uploaded_at
```

The final project can connect `user_id` to the shared `users` table.

For example:

```sql
ALTER TABLE media
ADD CONSTRAINT fk_media_user
FOREIGN KEY (user_id) REFERENCES users(user_id);
```

Use the exact primary-key name from the team's final users table.

## Why this design?

The relational database stores metadata, not large binary files.

The actual files remain in media storage.

This makes the database smaller and demonstrates separation of metadata and media storage.
