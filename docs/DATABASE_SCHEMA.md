# Canonical database schema

The canonical schema is approximately 3NF and is defined **once**, as
SQLAlchemy 2 models in `packages/models/`, from which both Alembic migrations
and the per-shard DDL (`scripts/initialize_shards.py --write-sql`) are derived.
The hand-written Docker first-boot SQL in `infrastructure/postgres/init/` is
kept byte-structurally in parity by an integration test. PostgreSQL is the
canonical source of truth; shards carry the same DDL partitioned by
application-level shard keys.

```mermaid
erDiagram
    users ||--o| profiles : "1:1"
    users ||--o{ posts : authors
    users ||--o{ media : owns
    users ||--o{ follows : follower
    users ||--o{ notifications : recipient
    posts ||--o{ comments : has
    posts ||--o{ likes : receives
    users {
        bigint id PK
        varchar username UK
        varchar email UK
        varchar password_hash
        timestamptz created_at
        timestamptz updated_at
    }
    profiles {
        bigint id PK
        bigint user_id FK
        varchar display_name
        text bio
        bigint avatar_media_id
    }
    posts {
        bigint id PK
        bigint author_id FK
        text content
        varchar visibility
    }
    comments { bigint id PK; bigint post_id FK; bigint user_id; text content }
    likes    { bigint id PK; bigint post_id FK; bigint user_id }
    follows  { bigint follower_id PK_FK; bigint following_id PK }
    media    { bigint id PK; bigint owner_id FK; varchar storage_key UK; varchar media_type }
    notifications { bigint id PK; bigint recipient_id FK; bigint actor_id; varchar type; bigint reference_id; bool is_read }
```

## Tables

- **users**: account/identity. `username`, `email` unique; credentials only.
- **profiles**: 1:1 display data (`user_id` unique). Separating profile
  attributes keeps `users` narrow and enforces 3NF. `avatar_media_id` is an
  application reference (no hard FK) to avoid a profiles↔media DDL cycle.
- **posts**: `author_id` FK (co-located in sharded mode because posts shard by
  `author_id`); `visibility` CHECK ∈ public/private/followers.
- **comments**: `post_id` FK (co-located; comments shard by `post_id`).
  `user_id` has **no FK** — a commenter may live on another shard.
- **likes**: unique `(post_id, user_id)`; `post_id` FK (co-located), `user_id`
  no FK.
- **follows**: composite PK `(follower_id, following_id)`, self-follow CHECK;
  hard FK only on `follower_id` (edges shard by follower).
- **media**: blob **metadata** only; the bytes belong to the future
  Haystack-inspired `MediaBlobStore`. Unique `storage_key`, type CHECK,
  non-negative size CHECK.
- **notifications**: shard by recipient; `actor_id`/`reference_id` are
  polymorphic pointers with no FK (may cross shards); `type` CHECK.

## Foreign-key policy under sharding

| Column | FK? | Reason |
|---|---|---|
| profiles.user_id | yes | profile co-located with user |
| posts.author_id | yes | posts shard by author → co-located |
| comments.post_id | yes | comments shard by post → co-located |
| likes.post_id | yes | likes shard by post → co-located |
| follows.follower_id | yes | follows shard by follower → co-located |
| media.owner_id | yes | media shard by owner → co-located |
| notifications.recipient_id | yes | notifications shard by recipient → co-located |
| comments.user_id, likes.user_id, follows.following_id, notifications.actor_id | **no** | may point across shards; assembled in app |

`ON DELETE CASCADE` is used on the co-located hard-FK paths, so deleting a user
cleans profile, posts, comments, likes, follows-as-follower, owned media, and
inbox notifications within the owning shard.

## Indexes and why they exist

| Index | Column(s) | Access pattern served |
|---|---|---|
| `ix_users_username` (unique) | username | login / lookup |
| `ix_users_email` (unique) | email | login / lookup |
| `ix_profiles_user_id` (unique) | user_id | 1:1 profile fetch |
| `ix_posts_author_created` | author_id, created_at DESC | author timeline, newest first |
| `ix_posts_created_at` | created_at DESC | global recency; scatter-gather merge key |
| `ix_comments_post_created` | post_id, created_at | "comments of a post", chronological |
| `ix_comments_user_id` | user_id | "comments by a user" (may fan out) |
| unique `uq_likes_post_user` | post_id, user_id | idempotency + like list by post |
| `ix_likes_user_id` | user_id | "posts a user liked" (may fan out) |
| PK `pk_follows` | follower_id, following_id | outgoing follows (shard-local) |
| `ix_follows_following` | following_id | followers listing (scatter-gather) |
| `ix_media_owner` | owner_id | a user's uploads |
| `ix_notifications_recipient_created` | recipient_id, created_at DESC | inbox, newest first |
| `ix_notifications_recipient_unread` | recipient_id, is_read | unread inbox filter |

## IDs

- Canonical mode: `BIGSERIAL`-backed application inserts (database sequences).
- Sharded mode: independent shard sequences would collide, so the application
  mints 63-bit Snowflake-like ids (`db/ids.py`). Posts/media ids are then
  *key-aligned* so `route(post.id) == route(author_id)` (see SHARDING.md §3).

## Constraints & checks

`CHECK`s validate post visibility, notification/media types, non-negative
media size, and no self-follow. All data access uses ORM/bound parameters —
there is no raw SQL string interpolation (an injection payload is asserted to
be stored verbatim in an integration test).

## Operational metadata (not part of the 3NF domain schema)

`metadata/repository.py` optionally creates `shard_registry_state` and
`shard_migration_log` on the canonical instance for topology and migration
audit; these are operational tables, not social-domain data.
