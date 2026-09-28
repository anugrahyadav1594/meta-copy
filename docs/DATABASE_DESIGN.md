# Database design — MetaScale

One relational engine: **PostgreSQL**. It is the canonical store, the shard
store and the replica store. Everything else (Redis, search index,
denormalized feed table, object store) is derived or external and can be
rebuilt or thrown away.

## Tables (canonical, 3NF)

| Table | Primary key | Notable columns | Shard key |
| --- | --- | --- | --- |
| `users` | `user_id` | `username`, `email`, `password_hash` (never exposed) | `user_id` |
| `profiles` | `profile_id` | `user_id`, `display_name`, `bio`, `avatar_media_id` | `user_id` |
| `posts` | `post_id` | `user_id`, `content`, `visibility`, timestamps | `user_id` |
| `comments` | `comment_id` | `post_id`, `user_id`, `parent_comment_id`, `content` | `post_id` |
| `likes` | `like_id` | `post_id`, `user_id` | `post_id` |
| `follows` | `(follower_id, following_id)` | — (no FK on `following_id`: the followee may live on another shard) | `follower_id` |
| `media` | `media_id` | `owner_id`, `mime_type`, `size_bytes`, `checksum_sha256`, `storage_key` | `owner_id` |
| `notifications` | `notification_id` | `user_id`, `actor_user_id`, `type`, `post_id`, `read_at` | `user_id` |
| `hashtags` | `hashtag_id` | `tag` | `tag` |
| `post_hashtags` | `post_id`, `hashtag_id` | — | `post_id` |

Derived tables (never a source of truth): `denormalized_post_feed` (M3),
`feed_entries` (M7 push strategy). Both are truncated and refilled from
canonical rows on demand.

## Identifier decision (hybrid)

Every entity uses a natural, readable name (`user_id`, `post_id`, …) holding a
64-bit application-generated value. This keeps foreign keys obvious while
making keys shardable and sortable. `follows` keeps its natural composite key.

## Indexes

| Index | Serves |
| --- | --- |
| `ix_follows_following (following_id)` | "who follows this user" — the followers listing |
| PK on `follows (follower_id, following_id)` | "who does this user follow" (targeted, co-located with the follower) |
| `posts (user_id, created_at)` | author timelines, feed candidate lookup |
| `comments (post_id)` / `likes (post_id)` | per-post aggregation and counters |
| `post_hashtags (hashtag_id)` | tag → posts |
| `media (checksum_sha256)` | upload de-duplication |

Index choices are documented here and measured through the benchmark API
(`normalized_read` vs `denormalized_read`); we do not quote query plans that
were never captured.

## Sharding layout

* Shard keys are chosen to co-locate the hottest joins (a user's posts, a
  post's comments and likes, a follower's outgoing follows).
* Post ids are **key-aligned**: `route(post.post_id) == route(post.user_id)`, so
  a post and its engagement live together.
* `following_id` has no foreign key because the followee may be on another
  shard — cross-shard integrity is enforced in the service layer, not by the
  database.
* Queries without a shard key use scatter-gather: every shard is queried in
  parallel and the results are merged in the API process (`packages/db/shard_scan.py`).

## Change tracking

Every canonical write produces a record with `operation`, `table`, primary key,
`shard`, `before`, `after`, `timestamp`, `request_id`, `service` and measured
`latency_ms` (`packages/observability/change_log.py`). `password_hash` and
anything else sensitive is stripped before the record is stored, so the record
can be shown in the UI safely. The buffer is bounded and in-process — see
`GET /api/v1/database/changes`.

## Consistency model

| Store | Consistency | On conflict |
| --- | --- | --- |
| Canonical PostgreSQL | strong (single writer per row) | constraints + transactions |
| Replicas | eventual (lag-aware routing) | lag above threshold → primary |
| Redis cache | TTL-bounded | invalidated on write |
| Denormalized read model | eventual (event-driven) | rebuild from canonical |
| Search index | eventual (async indexing) | reindex from canonical |
| Feed (push) | eventual | rebuild fan-out |

## What the database is *not*

* Not a graph database — relationships are ordinary rows in `follows`.
* Not multi-master — one primary per shard, replicas are read-only.
* Not a document store — media metadata is relational; only the bytes live in
  the object store.
