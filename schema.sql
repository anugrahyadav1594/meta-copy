-- Query Performance Analyzer
-- Social-graph schema with named PKs/FKs so indexes can be toggled for EXPLAIN demos.

CREATE DATABASE IF NOT EXISTS query_analyzer
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE query_analyzer;

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS notifications;
DROP TABLE IF EXISTS likes;
DROP TABLE IF EXISTS comments;
DROP TABLE IF EXISTS follows;
DROP TABLE IF EXISTS posts;
DROP TABLE IF EXISTS profiles;
DROP TABLE IF EXISTS users;

SET FOREIGN_KEY_CHECKS = 1;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

CREATE TABLE users (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  username      VARCHAR(50)     NOT NULL,
  email         VARCHAR(255)    NOT NULL,
  password_hash CHAR(60)        NOT NULL,
  created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE profiles (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id     BIGINT UNSIGNED NOT NULL,
  bio         VARCHAR(500)    NULL,
  avatar_url  VARCHAR(500)    NULL,
  location    VARCHAR(120)    NULL,
  website     VARCHAR(255)    NULL,
  updated_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  CONSTRAINT fk_profiles_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE posts (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id     BIGINT UNSIGNED NOT NULL,
  title       VARCHAR(200)    NOT NULL,
  content     TEXT            NOT NULL,
  created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  CONSTRAINT fk_posts_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE comments (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  post_id     BIGINT UNSIGNED NOT NULL,
  user_id     BIGINT UNSIGNED NOT NULL,
  body        TEXT            NOT NULL,
  created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  CONSTRAINT fk_comments_post
    FOREIGN KEY (post_id) REFERENCES posts (id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_comments_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE likes (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id     BIGINT UNSIGNED NOT NULL,
  post_id     BIGINT UNSIGNED NOT NULL,
  created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  CONSTRAINT fk_likes_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_likes_post
    FOREIGN KEY (post_id) REFERENCES posts (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE follows (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  follower_id  BIGINT UNSIGNED NOT NULL,
  followee_id  BIGINT UNSIGNED NOT NULL,
  created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  CONSTRAINT fk_follows_follower
    FOREIGN KEY (follower_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_follows_followee
    FOREIGN KEY (followee_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE notifications (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id     BIGINT UNSIGNED NOT NULL,
  type        VARCHAR(40)     NOT NULL,
  message     VARCHAR(500)    NOT NULL,
  is_read     TINYINT(1)      NOT NULL DEFAULT 0,
  created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  CONSTRAINT fk_notifications_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- CREATE INDEXES
-- InnoDB requires an index on every foreign key. These named indexes are the
-- ones the dashboard toggles. Composite keys also cover ORDER BY / lookup.
-- ---------------------------------------------------------------------------

/*
CREATE INDEX idx_profiles_user_id          ON profiles (user_id);
CREATE INDEX idx_posts_user_id             ON posts (user_id);
CREATE INDEX idx_posts_created_at          ON posts (created_at);
CREATE INDEX idx_posts_user_created        ON posts (user_id, created_at);
CREATE INDEX idx_comments_post_id          ON comments (post_id);
CREATE INDEX idx_comments_user_id          ON comments (user_id);
CREATE INDEX idx_comments_post_created     ON comments (post_id, created_at);
CREATE INDEX idx_likes_post_id             ON likes (post_id);
CREATE INDEX idx_likes_user_id             ON likes (user_id);
CREATE INDEX idx_follows_follower_id       ON follows (follower_id);
CREATE INDEX idx_follows_followee_id       ON follows (followee_id);
CREATE INDEX idx_notifications_user_id     ON notifications (user_id);
CREATE INDEX idx_notifications_user_created ON notifications (user_id, created_at);
*/

-- ---------------------------------------------------------------------------
-- DROP INDEXES
-- InnoDB will not drop an index that is still required by a foreign key.
-- Drop the FKs first, then drop the indexes. Do not re-add FKs until you
-- recreate covering indexes — otherwise InnoDB silently rebuilds FK indexes
-- and EXPLAIN will not show table scans.
-- The Streamlit app does this automatically via the "Enable Indexes" toggle.
-- ---------------------------------------------------------------------------

/*
ALTER TABLE profiles      DROP FOREIGN KEY fk_profiles_user;
ALTER TABLE posts         DROP FOREIGN KEY fk_posts_user;
ALTER TABLE comments      DROP FOREIGN KEY fk_comments_post;
ALTER TABLE comments      DROP FOREIGN KEY fk_comments_user;
ALTER TABLE likes         DROP FOREIGN KEY fk_likes_user;
ALTER TABLE likes         DROP FOREIGN KEY fk_likes_post;
ALTER TABLE follows       DROP FOREIGN KEY fk_follows_follower;
ALTER TABLE follows       DROP FOREIGN KEY fk_follows_followee;
ALTER TABLE notifications DROP FOREIGN KEY fk_notifications_user;

DROP INDEX idx_profiles_user_id           ON profiles;
DROP INDEX idx_posts_user_id              ON posts;
DROP INDEX idx_posts_created_at           ON posts;
DROP INDEX idx_posts_user_created         ON posts;
DROP INDEX idx_comments_post_id           ON comments;
DROP INDEX idx_comments_user_id           ON comments;
DROP INDEX idx_comments_post_created      ON comments;
DROP INDEX idx_likes_post_id              ON likes;
DROP INDEX idx_likes_user_id              ON likes;
DROP INDEX idx_follows_follower_id        ON follows;
DROP INDEX idx_follows_followee_id        ON follows;
DROP INDEX idx_notifications_user_id      ON notifications;
DROP INDEX idx_notifications_user_created ON notifications;
*/
