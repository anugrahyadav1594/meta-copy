"""
MySQL access layer for the Query Performance Analyzer.

Runs the six target queries with EXPLAIN FORMAT=JSON, times the real SELECT,
and toggles secondary indexes so indexed vs table-scan plans can be compared.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

import mysql.connector
from mysql.connector import MySQLConnection
from mysql.connector.cursor import MySQLCursor

DB_CONFIG: dict[str, Any] = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "query_analyzer"),
    "charset": "utf8mb4",
    "collation": "utf8mb4_unicode_ci",
    "autocommit": False,
    "connection_timeout": 10,
}

INDEX_SPECS: tuple[tuple[str, str, str], ...] = (
    ("profiles", "idx_profiles_user_id", "user_id"),
    ("posts", "idx_posts_user_id", "user_id"),
    ("posts", "idx_posts_created_at", "created_at"),
    ("posts", "idx_posts_user_created", "user_id, created_at"),
    ("comments", "idx_comments_post_id", "post_id"),
    ("comments", "idx_comments_user_id", "user_id"),
    ("comments", "idx_comments_post_created", "post_id, created_at"),
    ("likes", "idx_likes_post_id", "post_id"),
    ("likes", "idx_likes_user_id", "user_id"),
    ("follows", "idx_follows_follower_id", "follower_id"),
    ("follows", "idx_follows_followee_id", "followee_id"),
    ("notifications", "idx_notifications_user_id", "user_id"),
    ("notifications", "idx_notifications_user_created", "user_id, created_at"),
)

FOREIGN_KEYS: tuple[tuple[str, str, str], ...] = (
    ("profiles", "fk_profiles_user", "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE"),
    ("posts", "fk_posts_user", "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE"),
    ("comments", "fk_comments_post", "FOREIGN KEY (post_id) REFERENCES posts (id) ON DELETE CASCADE ON UPDATE CASCADE"),
    ("comments", "fk_comments_user", "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE"),
    ("likes", "fk_likes_user", "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE"),
    ("likes", "fk_likes_post", "FOREIGN KEY (post_id) REFERENCES posts (id) ON DELETE CASCADE ON UPDATE CASCADE"),
    ("follows", "fk_follows_follower", "FOREIGN KEY (follower_id) REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE"),
    ("follows", "fk_follows_followee", "FOREIGN KEY (followee_id) REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE"),
    ("notifications", "fk_notifications_user", "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE"),
)

ACCESS_RANK: dict[str, int] = {
    "ALL": 0,
    "index": 1,
    "range": 2,
    "index_merge": 3,
    "ref_or_null": 4,
    "fulltext": 5,
    "ref": 6,
    "eq_ref": 7,
    "const": 8,
    "system": 9,
    "NULL": 10,
}


def connect(include_database: bool = True) -> MySQLConnection:
    cfg = dict(DB_CONFIG)
    if not include_database:
        cfg.pop("database", None)
    return mysql.connector.connect(**cfg)


@contextmanager
def get_connection() -> Iterator[MySQLConnection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def _scalar(cursor: MySQLCursor, sql: str, params: tuple[Any, ...] = ()) -> Any:
    cursor.execute(sql, params)
    row = cursor.fetchone()
    if row is None:
        return None
    return next(iter(row.values() if isinstance(row, dict) else row))


def fetch_sample_ids(conn: MySQLConnection) -> dict[str, int]:
    """Pick real keys so EXPLAIN/SELECT run against existing rows."""
    cur = conn.cursor()
    try:
        user_id = _scalar(cur, "SELECT id FROM users ORDER BY id LIMIT 1")
        follower_id = _scalar(
            cur,
            """
            SELECT follower_id
            FROM follows
            GROUP BY follower_id
            ORDER BY COUNT(*) DESC
            LIMIT 1
            """,
        ) or user_id
        followee_id = _scalar(
            cur,
            """
            SELECT followee_id
            FROM follows
            GROUP BY followee_id
            ORDER BY COUNT(*) DESC
            LIMIT 1
            """,
        ) or user_id
        post_id = _scalar(
            cur,
            """
            SELECT post_id
            FROM likes
            GROUP BY post_id
            ORDER BY COUNT(*) DESC
            LIMIT 1
            """,
        ) or _scalar(cur, "SELECT id FROM posts ORDER BY id LIMIT 1")
        comment_post_id = _scalar(
            cur,
            """
            SELECT post_id
            FROM comments
            GROUP BY post_id
            ORDER BY COUNT(*) DESC
            LIMIT 1
            """,
        ) or post_id
        notif_user_id = _scalar(
            cur,
            """
            SELECT user_id
            FROM notifications
            GROUP BY user_id
            ORDER BY COUNT(*) DESC
            LIMIT 1
            """,
        ) or user_id

        missing = [
            name
            for name, value in {
                "user_id": user_id,
                "follower_id": follower_id,
                "followee_id": followee_id,
                "post_id": post_id,
                "comment_post_id": comment_post_id,
                "notif_user_id": notif_user_id,
            }.items()
            if value is None
        ]
        if missing:
            raise RuntimeError(
                "Database looks empty ("
                + ", ".join(missing)
                + " missing). Run seed.py before analyzing queries."
            )
        return {
            "user_id": int(user_id),
            "follower_id": int(follower_id),
            "followee_id": int(followee_id),
            "post_id": int(post_id),
            "comment_post_id": int(comment_post_id),
            "notif_user_id": int(notif_user_id),
        }
    finally:
        cur.close()


def build_target_queries(ids: dict[str, int]) -> dict[str, dict[str, Any]]:
    """Six workload queries that benefit clearly from FK / covering indexes."""
    return {
        "Feed generation": {
            "description": "Home feed: recent posts from accounts the user follows.",
            "sql": """
                SELECT p.id, p.title, p.content, p.created_at, u.username
                FROM posts AS p
                INNER JOIN follows AS f ON f.followee_id = p.user_id
                INNER JOIN users AS u ON u.id = p.user_id
                WHERE f.follower_id = %s
                ORDER BY p.created_at DESC
                LIMIT 50
            """,
            "params": (ids["follower_id"],),
        },
        "Post + Author": {
            "description": "Single post with author account and profile bio.",
            "sql": """
                SELECT p.id, p.title, p.content, p.created_at,
                       u.username, u.email, pr.bio, pr.location
                FROM posts AS p
                INNER JOIN users AS u ON u.id = p.user_id
                LEFT JOIN profiles AS pr ON pr.user_id = u.id
                WHERE p.id = %s
            """,
            "params": (ids["post_id"],),
        },
        "Post + Likes": {
            "description": "Like count for one post (join + aggregate).",
            "sql": """
                SELECT p.id, p.title, COUNT(l.id) AS like_count
                FROM posts AS p
                LEFT JOIN likes AS l ON l.post_id = p.id
                WHERE p.id = %s
                GROUP BY p.id, p.title
            """,
            "params": (ids["post_id"],),
        },
        "Post + Comments": {
            "description": "Comment thread for a post, newest first.",
            "sql": """
                SELECT c.id, c.body, c.created_at, u.username
                FROM comments AS c
                INNER JOIN users AS u ON u.id = c.user_id
                WHERE c.post_id = %s
                ORDER BY c.created_at DESC
            """,
            "params": (ids["comment_post_id"],),
        },
        "Follower lookup": {
            "description": "Accounts that follow a given user.",
            "sql": """
                SELECT u.id, u.username, u.email, f.created_at
                FROM follows AS f
                INNER JOIN users AS u ON u.id = f.follower_id
                WHERE f.followee_id = %s
            """,
            "params": (ids["followee_id"],),
        },
        "Notification retrieval": {
            "description": "Latest unread-first notifications for a user.",
            "sql": """
                SELECT n.id, n.type, n.message, n.is_read, n.created_at
                FROM notifications AS n
                WHERE n.user_id = %s
                ORDER BY n.is_read ASC, n.created_at DESC
                LIMIT 20
            """,
            "params": (ids["notif_user_id"],),
        },
    }


def _constraint_exists(conn: MySQLConnection, table: str, name: str) -> bool:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.TABLE_CONSTRAINTS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND CONSTRAINT_NAME = %s
            LIMIT 1
            """,
            (table, name),
        )
        return cur.fetchone() is not None
    finally:
        cur.close()


def _index_exists(conn: MySQLConnection, table: str, name: str) -> bool:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
            LIMIT 1
            """,
            (table, name),
        )
        return cur.fetchone() is not None
    finally:
        cur.close()


def indexes_enabled(conn: MySQLConnection) -> bool:
    """True when the named secondary indexes from INDEX_SPECS are present."""
    return all(_index_exists(conn, table, name) for table, name, _ in INDEX_SPECS)


def _drop_foreign_keys(conn: MySQLConnection) -> None:
    cur = conn.cursor()
    try:
        for table, name, _ in FOREIGN_KEYS:
            if _constraint_exists(conn, table, name):
                cur.execute(f"ALTER TABLE `{table}` DROP FOREIGN KEY `{name}`")
        conn.commit()
    finally:
        cur.close()


def _add_foreign_keys(conn: MySQLConnection) -> None:
    cur = conn.cursor()
    try:
        for table, name, ddl in FOREIGN_KEYS:
            if not _constraint_exists(conn, table, name):
                cur.execute(f"ALTER TABLE `{table}` ADD CONSTRAINT `{name}` {ddl}")
        conn.commit()
    finally:
        cur.close()


def enable_indexes(conn: MySQLConnection) -> None:
    """Create covering/FK indexes, then restore foreign keys."""
    cur = conn.cursor()
    try:
        for table, name, columns in INDEX_SPECS:
            if not _index_exists(conn, table, name):
                cur.execute(f"CREATE INDEX `{name}` ON `{table}` ({columns})")
        conn.commit()
    finally:
        cur.close()
    _add_foreign_keys(conn)


def _drop_all_secondary_indexes(conn: MySQLConnection) -> None:
    """Remove every non-PRIMARY index on demo tables (including InnoDB leftovers)."""
    demo_tables = (
        "profiles",
        "posts",
        "comments",
        "likes",
        "follows",
        "notifications",
        "users",
    )
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT DISTINCT TABLE_NAME, INDEX_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND INDEX_NAME <> 'PRIMARY'
              AND TABLE_NAME IN ({})
            """.format(",".join(["%s"] * len(demo_tables))),
            demo_tables,
        )
        to_drop = list(cur.fetchall())
        for table, name in to_drop:
            cur.execute(f"DROP INDEX `{name}` ON `{table}`")
        conn.commit()
    finally:
        cur.close()


def disable_indexes(conn: MySQLConnection) -> None:
    """
    Force table-scan plans: drop foreign keys (InnoDB requires an index on
    every FK), then drop all secondary indexes. Foreign keys are NOT restored
    here — adding them back would make InnoDB recreate supporting indexes.
    """
    _drop_foreign_keys(conn)
    _drop_all_secondary_indexes(conn)


def apply_index_mode(conn: MySQLConnection, enabled: bool) -> None:
    if enabled:
        enable_indexes(conn)
    else:
        disable_indexes(conn)


def _as_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_explain_json(plan: dict[str, Any]) -> dict[str, Any]:
    """
    Walk EXPLAIN FORMAT=JSON and extract:
      - access_type: worst (least selective) access among tables
      - rows_examined: sum of rows_examined_per_scan (optimizer estimate)
      - query_cost: optimizer cost from query_block.cost_info
    """
    tables: list[dict[str, Any]] = []
    costs: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return

        cost_info = node.get("cost_info")
        if isinstance(cost_info, dict) and "query_cost" in cost_info:
            parsed = _as_number(cost_info.get("query_cost"))
            if parsed is not None:
                costs.append(parsed)

        table = node.get("table")
        if isinstance(table, dict) and "access_type" in table:
            rows_scan = table.get("rows_examined_per_scan")
            if rows_scan is None:
                rows_scan = table.get("rows")
            tables.append(
                {
                    "table_name": table.get("table_name") or table.get("table"),
                    "access_type": str(table.get("access_type") or "unknown"),
                    "rows_examined_per_scan": _as_number(rows_scan),
                    "rows_produced_per_join": _as_number(table.get("rows_produced_per_join")),
                    "filtered": _as_number(table.get("filtered")),
                    "key": table.get("key"),
                    "used_key_parts": table.get("used_key_parts"),
                    "ref": table.get("ref"),
                    "using_index": table.get("using_index"),
                }
            )

        for key, value in node.items():
            if key == "table":
                # Recurse into nested structures inside the table object
                # (attached_condition is a string; skip walking strings).
                if isinstance(value, dict):
                    for inner in value.values():
                        if isinstance(inner, (dict, list)):
                            walk(inner)
                continue
            walk(value)

    walk(plan)

    rows_examined = 0.0
    for item in tables:
        scan = item["rows_examined_per_scan"]
        if scan is not None:
            rows_examined += scan

    worst = None
    worst_rank = 10**9
    for item in tables:
        rank = ACCESS_RANK.get(item["access_type"], 5)
        if rank < worst_rank:
            worst_rank = rank
            worst = item["access_type"]

    query_cost = max(costs) if costs else None

    return {
        "access_type": worst or "unknown",
        "rows_examined": int(round(rows_examined)),
        "query_cost": query_cost,
        "tables": tables,
    }


def explain_query(
    conn: MySQLConnection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> dict[str, Any]:
    cur = conn.cursor()
    try:
        cur.execute("EXPLAIN FORMAT=JSON " + sql, params)
        row = cur.fetchone()
        if not row:
            raise RuntimeError("EXPLAIN returned no rows.")
        raw = row[0] if not isinstance(row, dict) else next(iter(row.values()))
        plan = json.loads(raw) if isinstance(raw, str) else raw
        parsed = parse_explain_json(plan)
        parsed["raw_plan"] = plan
        return parsed
    finally:
        cur.close()


def execute_timed(
    conn: MySQLConnection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> dict[str, Any]:
    cur = conn.cursor()
    try:
        started = time.perf_counter()
        cur.execute(sql, params)
        rows = cur.fetchall()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        columns = [desc[0] for desc in (cur.description or [])]
        preview = []
        for row in rows[:8]:
            if isinstance(row, dict):
                preview.append(row)
            else:
                preview.append(dict(zip(columns, row)))
        return {
            "elapsed_ms": elapsed_ms,
            "row_count": len(rows),
            "preview": preview,
        }
    finally:
        cur.close()


def analyze_query(
    conn: MySQLConnection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> dict[str, Any]:
    plan = explain_query(conn, sql, params)
    timed = execute_timed(conn, sql, params)
    return {
        "access_type": plan["access_type"],
        "rows_examined": plan["rows_examined"],
        "query_cost": plan["query_cost"],
        "tables": plan["tables"],
        "raw_plan": plan["raw_plan"],
        "elapsed_ms": timed["elapsed_ms"],
        "row_count": timed["row_count"],
        "preview": timed["preview"],
        "sql": sql.strip(),
        "params": params,
    }


def healthcheck() -> dict[str, Any]:
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT DATABASE(), VERSION()")
        db, version = cur.fetchone()
        cur.close()
        return {
            "ok": True,
            "database": db,
            "version": version,
            "indexes_enabled": indexes_enabled(conn),
        }
    finally:
        conn.close()
