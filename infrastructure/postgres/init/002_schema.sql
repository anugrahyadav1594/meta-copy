-- 002_schema.sql
-- Canonical normalized (~3NF) social-media schema — the source of truth.
-- Same DDL is used on every shard in SHARDED mode (application-level
-- sharding keeps the relational model identical across physical instances).
--
-- Foreign-key policy for sharding co-location:
--   * Columns that are ALSO the shard key keep hard FKs (rows are co-located).
--   * Cross-shard references (e.g. comment.user_id, like.user_id,
--     follow.following_id, notification.actor_id) intentionally have NO FK:
--     the referenced user may live on another PostgreSQL instance.
--
-- The shard DDL is generated from the SQLAlchemy models into
-- infrastructure/postgres/shards/shard-N/ via scripts/initialize_shards.py.

-- ---------------------------------------------------------------- users
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    username      VARCHAR(50)  NOT NULL,
    email         VARCHAR(254) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------- profiles
CREATE TABLE IF NOT EXISTS profiles (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    display_name    VARCHAR(100) NOT NULL,
    bio             TEXT,
    -- Application-managed reference into media(id); no hard FK to break the
    -- profiles <-> media circular dependency.
    avatar_media_id BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --------------------------------------------------------------- media
-- Created before posts so future posts/profiles can reference blobs;
-- metadata only (Haystack-inspired blob storage is a future module).
CREATE TABLE IF NOT EXISTS media (
    id          BIGSERIAL PRIMARY KEY,
    owner_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    storage_key VARCHAR(255) NOT NULL,
    media_type  VARCHAR(20)  NOT NULL,
    mime_type   VARCHAR(100) NOT NULL,
    size_bytes  BIGINT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_media_storage_key UNIQUE (storage_key),
    CONSTRAINT ck_media_size_nonneg CHECK (size_bytes >= 0),
    CONSTRAINT ck_media_type CHECK (
        media_type IN ('image', 'video', 'audio', 'document')
    )
);

-- --------------------------------------------------------------- posts
CREATE TABLE IF NOT EXISTS posts (
    id          BIGSERIAL PRIMARY KEY,
    author_id   BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content     TEXT   NOT NULL,
    visibility  VARCHAR(20) NOT NULL DEFAULT 'public',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_posts_visibility CHECK (
        visibility IN ('public', 'private', 'followers')
    )
);

-- ------------------------------------------------------------- follows
CREATE TABLE IF NOT EXISTS follows (
    follower_id  BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    following_id BIGINT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_follows PRIMARY KEY (follower_id, following_id),
    CONSTRAINT ck_follows_no_self_follow CHECK (follower_id <> following_id)
);

-- ------------------------------------------------------------ comments
CREATE TABLE IF NOT EXISTS comments (
    id         BIGSERIAL PRIMARY KEY,
    post_id    BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id    BIGINT NOT NULL,
    content    TEXT   NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --------------------------------------------------------------- likes
CREATE TABLE IF NOT EXISTS likes (
    id         BIGSERIAL PRIMARY KEY,
    post_id    BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id    BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_likes_post_user UNIQUE (post_id, user_id)
);

-- ------------------------------------------------------- notifications
CREATE TABLE IF NOT EXISTS notifications (
    id           BIGSERIAL PRIMARY KEY,
    recipient_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    actor_id     BIGINT,
    type         VARCHAR(20) NOT NULL,
    reference_id BIGINT,
    is_read      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_notifications_type CHECK (
        type IN ('like', 'comment', 'follow', 'mention', 'system')
    )
);

-- updated_at triggers on mutable tables
DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_profiles_updated_at ON profiles;
CREATE TRIGGER trg_profiles_updated_at BEFORE UPDATE ON profiles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_posts_updated_at ON posts;
CREATE TRIGGER trg_posts_updated_at BEFORE UPDATE ON posts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
