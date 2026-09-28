-- MetaScale
-- Member 1: baseline indexes for common relational lookups.
-- These are not the distributed optimizations; they are basic database indexes.

CREATE INDEX idx_posts_user_created
    ON posts(user_id, created_at DESC);

CREATE INDEX idx_media_post
    ON media(post_id);

CREATE INDEX idx_follows_following
    ON follows(following_id);

CREATE INDEX idx_likes_post
    ON likes(post_id);

CREATE INDEX idx_comments_post_created
    ON comments(post_id, created_at);

CREATE INDEX idx_comments_parent
    ON comments(parent_comment_id);

CREATE INDEX idx_post_hashtags_hashtag
    ON post_hashtags(hashtag_id);

CREATE INDEX idx_notifications_user_created
    ON notifications(user_id, created_at DESC);

CREATE INDEX idx_notifications_unread
    ON notifications(user_id, is_read, created_at DESC);
