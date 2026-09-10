CREATE DATABASE IF NOT EXISTS metascale;
USE metascale;

CREATE TABLE IF NOT EXISTS media (
    media_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_type VARCHAR(100) NOT NULL,
    file_size BIGINT NOT NULL,
    file_hash CHAR(64) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_media_user (user_id),
    INDEX idx_media_hash (file_hash)
);
