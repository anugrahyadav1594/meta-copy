"""
Populate query_analyzer with enough rows for meaningful EXPLAIN comparisons.

Defaults:
  2,000 users (+ profiles)
  20,000 posts
  50,000 comments
  50,000 likes
  ~16,000 follows
  20,000 notifications
"""

from __future__ import annotations

import hashlib
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

from faker import Faker
from mysql.connector import Error

from database import DB_CONFIG, connect, enable_indexes

FAKE = Faker()
Faker.seed(42)
random.seed(42)

USER_COUNT = 2_000
POST_COUNT = 20_000
COMMENT_COUNT = 50_000
LIKE_COUNT = 50_000
FOLLOWS_PER_USER = 8
NOTIFICATION_COUNT = 20_000
BATCH_SIZE = 1_000
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

NOTIFICATION_TYPES = ("like", "comment", "follow", "mention", "system")


def _hash_password(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf8")).hexdigest()[:60]


def _chunks(rows: list[tuple], size: int = BATCH_SIZE):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _progress(label: str, done: int, total: int) -> None:
    width = 28
    filled = int(width * done / total) if total else width
    bar = "#" * filled + "-" * (width - filled)
    sys.stdout.write(f"\r  {label:<18} [{bar}] {done:,}/{total:,}")
    sys.stdout.flush()
    if done >= total:
        sys.stdout.write("\n")


def ensure_database() -> None:
    root = connect(include_database=False)
    try:
        cur = root.cursor()
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{DB_CONFIG['database']}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        root.commit()
        cur.close()
    finally:
        root.close()


def _strip_sql_comments(sql: str) -> str:
    without_blocks: list[str] = []
    i = 0
    while i < len(sql):
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end == -1:
                break
            i = end + 2
            continue
        without_blocks.append(sql[i])
        i += 1
    lines = []
    for line in "".join(without_blocks).splitlines():
        if line.strip().startswith("--"):
            continue
        lines.append(line)
    return "\n".join(lines)


def apply_schema(conn) -> None:
    text = _strip_sql_comments(SCHEMA_PATH.read_text(encoding="utf-8"))
    # Execute only the live DDL; commented CREATE/DROP INDEX blocks are stripped.
    statements = [part.strip() for part in text.split(";") if part.strip()]
    cur = conn.cursor()
    try:
        for stmt in statements:
            upper = stmt.lstrip().upper()
            if upper.startswith("CREATE DATABASE") or upper.startswith("USE "):
                continue
            cur.execute(stmt)
        conn.commit()
    finally:
        cur.close()


def _random_past(days: int = 365) -> datetime:
    return datetime.now() - timedelta(
        days=random.randint(0, days),
        seconds=random.randint(0, 86_399),
    )


def insert_users(conn) -> None:
    sql = (
        "INSERT INTO users (username, email, password_hash, created_at) "
        "VALUES (%s, %s, %s, %s)"
    )
    cur = conn.cursor()
    try:
        inserted = 0
        batch: list[tuple] = []
        password = _hash_password("Passw0rd!")
        for i in range(1, USER_COUNT + 1):
            batch.append(
                (
                    f"user{i:04d}",
                    f"user{i:04d}@example.com",
                    password,
                    _random_past(700),
                )
            )
            if len(batch) >= BATCH_SIZE:
                cur.executemany(sql, batch)
                inserted += len(batch)
                batch.clear()
                _progress("users", inserted, USER_COUNT)
        if batch:
            cur.executemany(sql, batch)
            inserted += len(batch)
            _progress("users", inserted, USER_COUNT)
        conn.commit()
    finally:
        cur.close()


def insert_profiles(conn) -> None:
    sql = (
        "INSERT INTO profiles (user_id, bio, avatar_url, location, website) "
        "VALUES (%s, %s, %s, %s, %s)"
    )
    cur = conn.cursor()
    try:
        rows = [
            (
                user_id,
                FAKE.sentence(nb_words=12)[:500],
                f"https://i.pravatar.cc/150?u={user_id}",
                FAKE.city(),
                FAKE.url(),
            )
            for user_id in range(1, USER_COUNT + 1)
        ]
        inserted = 0
        for chunk in _chunks(rows):
            cur.executemany(sql, chunk)
            inserted += len(chunk)
            _progress("profiles", inserted, USER_COUNT)
        conn.commit()
    finally:
        cur.close()


def insert_posts(conn) -> None:
    sql = (
        "INSERT INTO posts (user_id, title, content, created_at) "
        "VALUES (%s, %s, %s, %s)"
    )
    cur = conn.cursor()
    try:
        inserted = 0
        batch: list[tuple] = []
        for _ in range(POST_COUNT):
            batch.append(
                (
                    random.randint(1, USER_COUNT),
                    FAKE.sentence(nb_words=6)[:200],
                    FAKE.paragraph(nb_sentences=4),
                    _random_past(400),
                )
            )
            if len(batch) >= BATCH_SIZE:
                cur.executemany(sql, batch)
                inserted += len(batch)
                batch.clear()
                _progress("posts", inserted, POST_COUNT)
        if batch:
            cur.executemany(sql, batch)
            inserted += len(batch)
            _progress("posts", inserted, POST_COUNT)
        conn.commit()
    finally:
        cur.close()


def insert_comments(conn) -> None:
    sql = (
        "INSERT INTO comments (post_id, user_id, body, created_at) "
        "VALUES (%s, %s, %s, %s)"
    )
    cur = conn.cursor()
    try:
        inserted = 0
        batch: list[tuple] = []
        for _ in range(COMMENT_COUNT):
            batch.append(
                (
                    random.randint(1, POST_COUNT),
                    random.randint(1, USER_COUNT),
                    FAKE.sentence(nb_words=18),
                    _random_past(400),
                )
            )
            if len(batch) >= BATCH_SIZE:
                cur.executemany(sql, batch)
                inserted += len(batch)
                batch.clear()
                _progress("comments", inserted, COMMENT_COUNT)
        if batch:
            cur.executemany(sql, batch)
            inserted += len(batch)
            _progress("comments", inserted, COMMENT_COUNT)
        conn.commit()
    finally:
        cur.close()


def insert_likes(conn) -> None:
    sql = (
        "INSERT INTO likes (user_id, post_id, created_at) VALUES (%s, %s, %s)"
    )
    seen: set[tuple[int, int]] = set()
    rows: list[tuple] = []
    attempts = 0
    while len(rows) < LIKE_COUNT and attempts < LIKE_COUNT * 8:
        attempts += 1
        pair = (random.randint(1, USER_COUNT), random.randint(1, POST_COUNT))
        if pair in seen:
            continue
        seen.add(pair)
        rows.append((pair[0], pair[1], _random_past(400)))

    if len(rows) < LIKE_COUNT:
        raise RuntimeError(f"Could only generate {len(rows):,} unique likes.")

    cur = conn.cursor()
    try:
        inserted = 0
        for chunk in _chunks(rows):
            cur.executemany(sql, chunk)
            inserted += len(chunk)
            _progress("likes", inserted, LIKE_COUNT)
        conn.commit()
    finally:
        cur.close()


def insert_follows(conn) -> None:
    sql = (
        "INSERT INTO follows (follower_id, followee_id, created_at) "
        "VALUES (%s, %s, %s)"
    )
    seen: set[tuple[int, int]] = set()
    rows: list[tuple] = []
    for follower in range(1, USER_COUNT + 1):
        targets = set()
        while len(targets) < FOLLOWS_PER_USER:
            followee = random.randint(1, USER_COUNT)
            if followee != follower:
                targets.add(followee)
        for followee in targets:
            pair = (follower, followee)
            if pair in seen:
                continue
            seen.add(pair)
            rows.append((follower, followee, _random_past(500)))

    total = len(rows)
    cur = conn.cursor()
    try:
        inserted = 0
        for chunk in _chunks(rows):
            cur.executemany(sql, chunk)
            inserted += len(chunk)
            _progress("follows", inserted, total)
        conn.commit()
    finally:
        cur.close()


def insert_notifications(conn) -> None:
    sql = (
        "INSERT INTO notifications (user_id, type, message, is_read, created_at) "
        "VALUES (%s, %s, %s, %s, %s)"
    )
    cur = conn.cursor()
    try:
        inserted = 0
        batch: list[tuple] = []
        for _ in range(NOTIFICATION_COUNT):
            batch.append(
                (
                    random.randint(1, USER_COUNT),
                    random.choice(NOTIFICATION_TYPES),
                    FAKE.sentence(nb_words=10)[:500],
                    random.choice((0, 0, 0, 1)),
                    _random_past(120),
                )
            )
            if len(batch) >= BATCH_SIZE:
                cur.executemany(sql, batch)
                inserted += len(batch)
                batch.clear()
                _progress("notifications", inserted, NOTIFICATION_COUNT)
        if batch:
            cur.executemany(sql, batch)
            inserted += len(batch)
            _progress("notifications", inserted, NOTIFICATION_COUNT)
        conn.commit()
    finally:
        cur.close()


def verify(conn) -> None:
    cur = conn.cursor()
    try:
        print("\nRow counts")
        for table in (
            "users",
            "profiles",
            "posts",
            "comments",
            "likes",
            "follows",
            "notifications",
        ):
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            print(f"  {table:<16} {cur.fetchone()[0]:>10,}")
    finally:
        cur.close()


def main() -> int:
    print("Query Performance Analyzer — seeder")
    print(
        f"Target {DB_CONFIG['user']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/"
        f"{DB_CONFIG['database']}"
    )
    try:
        ensure_database()
        conn = connect()
    except Error as exc:
        print(
            "\nCould not connect to MySQL.\n"
            "Set MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, "
            "MYSQL_DATABASE if needed.\n"
            f"Details: {exc}"
        )
        return 1

    try:
        print("Applying schema.sql …")
        apply_schema(conn)
        print("Inserting rows (this can take a few minutes) …")
        insert_users(conn)
        insert_profiles(conn)
        insert_posts(conn)
        insert_comments(conn)
        insert_likes(conn)
        insert_follows(conn)
        insert_notifications(conn)
        print("Creating secondary indexes …")
        enable_indexes(conn)
        verify(conn)
        print("\nSeed complete. Start the dashboard with:  streamlit run app.py")
        return 0
    except Error as exc:
        conn.rollback()
        print(f"\nMySQL error: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    # Allow MYSQL_* overrides before connect() reads os.environ in database.py
    os.environ.setdefault("MYSQL_DATABASE", "query_analyzer")
    raise SystemExit(main())
