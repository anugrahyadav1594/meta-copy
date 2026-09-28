-- 002_schema.sql
-- CANONICAL normalized (~3NF) MetaScale schema — the single source of truth.
--
-- Identifier convention (Member 1 canonical, adopted by every module):
--   users.user_id, profiles.profile_id, posts.post_id, comments.comment_id,
--   likes.like_id, media.media_id, notifications.notification_id,
--   hashtags.hashtag_id, post_hashtags(post_id, hashtag_id)
--
-- The same DDL runs on every shard in SHARDED mode (application-level
-- sharding keeps the relational model identical across physical instances).
--
-- Foreign-key policy for sharding co-location:
--   * Columns that are ALSO the shard key keep hard FKs (rows are co-located).
--   * Cross-shard references (comment.user_id, like.user_id,
--     follow.following_id, notification.actor_user_id, media.post_id)
--     intentionally have NO FK: the referenced row may live on another
--     PostgreSQL instance.
--
-- Shard DDL is generated from the SQLAlchemy models into
-- infrastructure/postgres/shards/shard-N/ by scripts/initialize_shards.py and
-- is parity-tested against this file.

-- ---------------------------------------------------------------- users
CREATE TABLE IF NOT EXISTS users (
    user_id       BIGSERIAL PRIMARY KEY,
    username      VARCHAR(50)  NOT NULL,
    email         VARCHAR(254) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------- profiles
CREATE TABLE IF NOT EXISTS profiles (
    profile_id      BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    display_name    VARCHAR(100) NOT NULL,
    bio             TEXT,
    -- Application-managed reference into media(media_id); no hard FK to break
    -- the profiles <-> media circular dependency.
    avatar_media_id BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- media
-- Metadata only: the blob lives in object storage (Member 8, Haystack-
-- inspired). checksum_sha256 is the content-addressable de-duplication key.
CREATE TABLE IF NOT EXISTS media (
    media_id         BIGSERIAL PRIMARY KEY,
    owner_id         BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    -- Optional post attachment; no FK (post may live on another shard).
    post_id          BIGINT,
    storage_key      VARCHAR(255) NOT NULL,
    checksum_sha256  VARCHAR(64),
    media_type       VARCHAR(20)  NOT NULL,
    mime_type        VARCHAR(100) NOT NULL,
    size_bytes       BIGINT NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_media_storage_key UNIQUE (storage_key),
    CONSTRAINT ck_media_size_nonneg CHECK (size_bytes >= 0),
    CONSTRAINT ck_media_type CHECK (
        media_type IN ('image', 'video', 'audio', 'document')
    )
);

-- ---------------------------------------------------------------- posts
CREATE TABLE IF NOT EXISTS posts (
    post_id      BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    content      TEXT   NOT NULL,
    visibility   VARCHAR(20) NOT NULL DEFAULT 'public',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_posts_visibility CHECK (
        visibility IN ('public', 'private', 'followers')
    )
);

-- -------------------------------------------------------------- follows
CREATE TABLE IF NOT EXISTS follows (
    follower_id  BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    -- No FK: the followee may live on another shard.
    following_id BIGINT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_follows PRIMARY KEY (follower_id, following_id),
    CONSTRAINT ck_follows_no_self_follow CHECK (follower_id <> following_id)
);

-- ------------------------------------------------------------- comments
CREATE TABLE IF NOT EXISTS comments (
    comment_id        BIGSERIAL PRIMARY KEY,
    post_id           BIGINT NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
    -- No FK: the comment author may live on another shard.
    user_id           BIGINT NOT NULL,
    parent_comment_id BIGINT,
    content           TEXT   NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- likes
CREATE TABLE IF NOT EXISTS likes (
    like_id    BIGSERIAL PRIMARY KEY,
    post_id    BIGINT NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
    -- No FK: the liker may live on another shard.
    user_id    BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_likes_post_user UNIQUE (post_id, user_id)
);

-- ------------------------------------------------------------- hashtags
CREATE TABLE IF NOT EXISTS hashtags (
    hashtag_id BIGSERIAL PRIMARY KEY,
    tag        VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_hashtags_tag UNIQUE (tag)
);

-- -------------------------------------------------------- post_hashtags
CREATE TABLE IF NOT EXISTS post_hashtags (
    post_id    BIGINT NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
    -- No FK: the hashtag row may live on another shard.
    hashtag_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_post_hashtags PRIMARY KEY (post_id, hashtag_id)
);

-- -------------------------------------------------------- notifications
CREATE TABLE IF NOT EXISTS notifications (
    notification_id BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    -- No FK: the actor may live on another shard.
    actor_user_id   BIGINT,
    type            VARCHAR(20) NOT NULL,
    post_id         BIGINT,
    is_read         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_notifications_type CHECK (
        type IN ('like', 'comment', 'follow', 'mention', 'system')
    )
);

-- updated_at triggers on mutable tables (rows updated outside the ORM)
DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_profiles_updated_at ON profiles;
CREATE TRIGGER trg_profiles_updated_at BEFORE UPDATE ON profiles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_posts_updated_at ON posts;
CREATE TRIGGER trg_posts_updated_at BEFORE UPDATE ON posts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
