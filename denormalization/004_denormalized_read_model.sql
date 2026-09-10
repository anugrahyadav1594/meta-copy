BEGIN;

DROP TABLE IF EXISTS denormalized_post_feed CASCADE;

CREATE TABLE denormalized_post_feed (
    post_id BIGINT PRIMARY KEY,
    author_id BIGINT NOT NULL,
    author_username VARCHAR(50) NOT NULL,
    author_display_name VARCHAR(100) NOT NULL,
    content TEXT,
    like_count BIGINT DEFAULT 0,
    comment_count BIGINT DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL
);

-- Index for rapid feed retrieval based on recency
CREATE INDEX idx_denorm_feed_created_at ON denormalized_post_feed(created_at DESC);

-- Migrate existing data matching the actual schema layout
INSERT INTO denormalized_post_feed (
    post_id, author_id, author_username, author_display_name, 
    content, like_count, comment_count, created_at
)
SELECT 
    p.id AS post_id,
    p.author_id,
    u.username AS author_username,
    COALESCE(pr.display_name, u.username) AS author_display_name,
    p.content,
    (SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) AS like_count,
    (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count,
    p.created_at
FROM posts p
JOIN users u ON p.author_id = u.id
LEFT JOIN profiles pr ON pr.user_id = u.id;

COMMIT;