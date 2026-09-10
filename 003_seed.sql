-- MetaScale
-- Member 1: small demo dataset for testing.

BEGIN;

INSERT INTO users (email, password_hash)
VALUES
    ('alice@example.com', 'demo_hash_alice'),
    ('bob@example.com', 'demo_hash_bob'),
    ('charlie@example.com', 'demo_hash_charlie'),
    ('diana@example.com', 'demo_hash_diana');

INSERT INTO profiles (user_id, username, display_name, bio)
VALUES
    (1, 'alice', 'Alice Shah', 'Computer science student'),
    (2, 'bob', 'Bob Mehta', 'Database enthusiast'),
    (3, 'charlie', 'Charlie Rao', 'Backend developer'),
    (4, 'diana', 'Diana Patel', 'Tech and design');

INSERT INTO posts (user_id, caption)
VALUES
    (1, 'Building our distributed database project!'),
    (2, 'Learning about PostgreSQL indexes.'),
    (3, 'Testing a new backend service.'),
    (4, 'Designing the project dashboard.');

INSERT INTO media (post_id, media_url, media_type, file_size_bytes)
VALUES
    (1, 'https://example.com/media/post1.jpg', 'image', 125000),
    (4, 'https://example.com/media/post4.jpg', 'image', 98000);

INSERT INTO follows (follower_id, following_id)
VALUES
    (1, 2),
    (1, 3),
    (2, 1),
    (3, 1),
    (4, 1);

INSERT INTO likes (user_id, post_id)
VALUES
    (2, 1),
    (3, 1),
    (4, 1),
    (1, 2),
    (1, 3);

INSERT INTO comments (post_id, user_id, comment_text)
VALUES
    (1, 2, 'Looks good!'),
    (1, 3, 'The normalization part is interesting.'),
    (4, 1, 'The dashboard will help with benchmarking.');

INSERT INTO hashtags (tag)
VALUES
    ('database'),
    ('postgresql'),
    ('distributed');

INSERT INTO post_hashtags (post_id, hashtag_id)
VALUES
    (1, 1),
    (1, 3),
    (2, 2),
    (3, 3);

INSERT INTO notifications (user_id, actor_user_id, post_id, notification_type)
VALUES
    (1, 2, 1, 'like'),
    (1, 3, 1, 'comment'),
    (1, 4, NULL, 'follow');

COMMIT;
