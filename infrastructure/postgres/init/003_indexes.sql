-- 003_indexes.sql
-- Realistic indexes for the documented access patterns. Every index below
-- has a stated purpose (see docs/DATABASE_SCHEMA.md for full rationale).

-- users: login / lookup identifiers (unique)
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email    ON users (email);

-- profiles: 1:1 lookup by user_id (unique)
CREATE UNIQUE INDEX IF NOT EXISTS ix_profiles_user_id ON profiles (user_id);

-- posts: author timeline ("posts by author, newest first") — composite
CREATE INDEX IF NOT EXISTS ix_posts_author_created ON posts (author_id, created_at DESC);
-- posts: global recency (scatter-gather merge sorts on created_at)
CREATE INDEX IF NOT EXISTS ix_posts_created_at ON posts (created_at DESC);

-- comments: "comments of a post" in chronological order — composite
CREATE INDEX IF NOT EXISTS ix_comments_post_created ON comments (post_id, created_at);
-- comments: "comments written by a user" (may fan out across shards)
CREATE INDEX IF NOT EXISTS ix_comments_user_id ON comments (user_id);

-- likes: who liked a post / like lists (the unique constraint already backs
-- (post_id,user_id); add the reverse lookup for "everything a user liked")
CREATE INDEX IF NOT EXISTS ix_likes_post_id ON likes (post_id);
CREATE INDEX IF NOT EXISTS ix_likes_user_id ON likes (user_id);

-- follows: "who follows this user" (incoming follows / followers listing)
CREATE INDEX IF NOT EXISTS ix_follows_following ON follows (following_id);
-- (the composite PRIMARY KEY already indexes follower_id lookups)

-- media: a user's uploads
CREATE INDEX IF NOT EXISTS ix_media_owner ON media (owner_id);

-- notifications: inbox, newest first (composite); unread filter (composite)
CREATE INDEX IF NOT EXISTS ix_notifications_recipient_created
    ON notifications (recipient_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_notifications_recipient_unread
    ON notifications (recipient_id, is_read);
