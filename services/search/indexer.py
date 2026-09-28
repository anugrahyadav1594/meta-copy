"""Member 10 — search indexing from canonical data + events.

The index is a **derived** structure: ``rebuild()`` re-creates it from
canonical PostgreSQL at any time, so losing the index never loses data.
Incremental updates arrive as domain events (POST_CREATED / POST_UPDATED /
POST_DELETED / MEDIA_CREATED …) published by the service layer.
"""

from __future__ import annotations

from typing import Any

from common.logging import get_logger
from search.provider import SearchProvider, tokenize
from sqlalchemy import select, text

logger = get_logger("search.indexer")

POSTS_INDEX = "posts"
USERS_INDEX = "users"


class SearchIndexer:
    """Keeps a :class:`SearchProvider` in step with canonical data."""

    def __init__(
        self,
        provider: SearchProvider,
        engine: Any = None,
        event_bus: Any = None,
        shard_manager: Any = None,
    ) -> None:
        self.provider = provider
        self.engine = engine
        self.events = event_bus
        # In SHARDED mode canonical PostgreSQL is not where the rows live:
        # the index must be rebuilt by scattering over the shards.
        self.shard_manager = shard_manager
        self.indexed = 0
        self.removals = 0
        self.errors = 0

    async def start(self) -> None:
        if self.events is None:
            return
        self.events.subscribe("POST_CREATED", self.on_post_created)
        self.events.subscribe("POST_UPDATED", self.on_post_updated)
        self.events.subscribe("POST_DELETED", self.on_post_deleted)

    # ---------------------------------------------------------------- events
    async def on_post_created(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", {}) or {})
        await self._index_post(
            post_id=int(getattr(event, "aggregate_id", 0) or payload.get("post_id", 0)),
            user_id=int(payload.get("user_id", 0)),
            content=payload.get("content", ""),
            created_at=payload.get("created_at"),
        )

    async def on_post_updated(self, event: Any) -> None:
        await self.on_post_created(event)

    async def on_post_deleted(self, event: Any) -> None:
        try:
            await self.provider.clear(POSTS_INDEX)
            await self.rebuild()
            self.removals += 1
        except Exception as exc:  # indexing must never break a write
            self.errors += 1
            logger.error("search_delete_failed", error=type(exc).__name__)

    @staticmethod
    def _searchable_text(content: str) -> tuple[str, list[str]]:
        """Content plus its hashtags as bare words.

        ``#metascale`` tokenizes to ``#metascale``, so a query for
        ``metascale`` would otherwise miss it. Indexing both forms keeps
        hashtag and free-text search consistent.
        """
        tags = sorted({t[1:] for t in tokenize(content or "") if t.startswith("#")})
        return " ".join([content or "", *tags]), tags

    async def _index_post(
        self, *, post_id: int, user_id: int, content: str, created_at: Any = None
    ) -> None:
        try:
            text, tags = self._searchable_text(content)
            await self.provider.index_document(
                POSTS_INDEX,
                {
                    "id": post_id,
                    "entity": "post",
                    "post_id": post_id,
                    "user_id": user_id,
                    "text": text,
                    "content": content,
                    "tags": tags,
                    "created_at": created_at,
                },
            )
            self.indexed += 1
        except Exception as exc:
            self.errors += 1
            logger.error("search_index_failed", post_id=post_id, error=type(exc).__name__)

    # --------------------------------------------------------------- rebuild
    async def _index_rows(self, rows: list[Any]) -> int:
        for row in rows:
            text, tags = self._searchable_text(row["content"])
            await self.provider.index_document(
                POSTS_INDEX,
                {
                    "id": row["post_id"],
                    "entity": "post",
                    "post_id": row["post_id"],
                    "user_id": row["user_id"],
                    "author_username": row.get("username"),
                    "text": text,
                    "content": row["content"],
                    "visibility": row.get("visibility"),
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "tags": tags,
                },
            )
        return len(rows)

    async def rebuild(self, limit: int = 5_000) -> int:
        """Rebuild both indexes from canonical PostgreSQL (and every shard).

        Returns the number of documents indexed.
        """
        if self.engine is None and self.shard_manager is None:
            return 0
        count = 0

        # --- shards first (in SHARDED mode the rows live there) -------------
        if self.shard_manager is not None:
            per_shard = max(limit // max(len(self.shard_manager.shard_ids), 1), 1)
            for shard_id in self.shard_manager.shard_ids:
                async with self.shard_manager.session_scope(shard_id, readonly=True) as session:
                    rows = (
                        (
                            await session.execute(
                                text(
                                    "SELECT p.post_id, p.user_id, p.content, p.visibility, "
                                    "p.created_at, u.username "
                                    "FROM posts p JOIN users u ON u.user_id = p.user_id "
                                    "ORDER BY p.created_at DESC LIMIT :limit"
                                ),
                                {"limit": per_shard},
                            )
                        )
                        .mappings()
                        .all()
                    )
                    count += await self._index_rows(list(rows))

        if self.engine is None:
            return count

        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        text(
                            "SELECT p.post_id, p.user_id, p.content, p.visibility, "
                            "p.created_at, u.username "
                            "FROM posts p JOIN users u ON u.user_id = p.user_id "
                            "ORDER BY p.created_at DESC LIMIT :limit"
                        ),
                        {"limit": limit},
                    )
                )
                .mappings()
                .all()
            )
            count += await self._index_rows(list(rows))

            users = (
                (
                    await conn.execute(
                        text(
                            "SELECT u.user_id, u.username, pr.display_name, pr.bio "
                            "FROM users u LEFT JOIN profiles pr ON pr.user_id = u.user_id "
                            "ORDER BY u.user_id LIMIT :limit"
                        ),
                        {"limit": limit},
                    )
                )
                .mappings()
                .all()
            )
            for row in users:
                await self.provider.index_document(
                    USERS_INDEX,
                    {
                        "id": row["user_id"],
                        "entity": "user",
                        "user_id": row["user_id"],
                        "username": row["username"],
                        "display_name": row["display_name"] or row["username"],
                        "text": f"{row['username']} {row['display_name'] or ''} {row['bio'] or ''}",
                    },
                )
                count += 1
        return count

    async def search(self, query: str, limit: int = 20) -> dict[str, Any]:
        return {
            "query": query,
            "posts": await self.provider.search(POSTS_INDEX, query, limit=limit),
            "users": await self.provider.search(USERS_INDEX, query, limit=max(limit // 2, 5)),
        }

    async def autocomplete(self, prefix: str, limit: int = 10) -> dict[str, Any]:
        return {
            "prefix": prefix,
            "posts": await self.provider.autocomplete(POSTS_INDEX, prefix, limit=limit),
            "users": await self.provider.autocomplete(USERS_INDEX, prefix, limit=limit),
        }

    def stats(self) -> dict[str, Any]:
        stats = self.provider.stats()
        stats.update(
            {
                "indexed_events": self.indexed,
                "rebuild_removals": self.removals,
                "errors": self.errors,
                "derived": True,
            }
        )
        return stats


__all__ = ["SearchIndexer", "POSTS_INDEX", "USERS_INDEX", "select"]
