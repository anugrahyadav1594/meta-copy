from __future__ import annotations

from typing import Any


class FeedService:
    def __init__(self, feed_repo: Any) -> None:
        self.feed = feed_repo

    async def pull_feed(self, user_id: int, limit: int = 20) -> list[Any]:
        return await self.feed.get_feed(user_id, limit=limit)