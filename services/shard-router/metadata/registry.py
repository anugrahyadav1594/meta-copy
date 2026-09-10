"""Shard metadata registries.

``MetadataRegistry`` is the abstract seam a future sophisticated metadata
service (e.g. etcd/consul-backed, or Member 5's topology service) implements.
``InMemoryShardRegistry`` is the current implementation: seeded from
environment configuration, fast, and fully functional for the demo.
"""

from __future__ import annotations

import abc
from collections.abc import Iterable

from common.enums import ShardStatus
from common.exceptions import ShardNotFoundError

from metadata.models import ShardMetadata


class MetadataRegistry(abc.ABC):
    """Abstract shard topology/metadata store."""

    @abc.abstractmethod
    async def add(self, shard: ShardMetadata) -> None: ...

    @abc.abstractmethod
    async def get(self, shard_id: str) -> ShardMetadata: ...

    @abc.abstractmethod
    async def list(self) -> list[ShardMetadata]: ...

    @abc.abstractmethod
    async def remove(self, shard_id: str) -> None: ...

    @abc.abstractmethod
    async def set_status(self, shard_id: str, status: ShardStatus) -> None: ...

    @abc.abstractmethod
    async def healthy_shards(self) -> list[ShardMetadata]: ...


class InMemoryShardRegistry(MetadataRegistry):
    def __init__(self, shards: Iterable[ShardMetadata] | None = None) -> None:
        self._shards: dict[str, ShardMetadata] = {}
        for shard in shards or []:
            self._shards[shard.id] = shard

    async def add(self, shard: ShardMetadata) -> None:
        self._shards[shard.id] = shard

    async def get(self, shard_id: str) -> ShardMetadata:
        try:
            return self._shards[shard_id]
        except KeyError:
            raise ShardNotFoundError(shard_id) from None

    async def list(self) -> list[ShardMetadata]:
        return [self._shards[k] for k in sorted(self._shards, key=_shard_sort_key)]

    async def remove(self, shard_id: str) -> None:
        self._shards.pop(shard_id, None)

    async def set_status(self, shard_id: str, status: ShardStatus) -> None:
        shard = await self.get(shard_id)
        shard.status = status
        shard.touch()

    async def healthy_shards(self) -> list[ShardMetadata]:
        return [s for s in await self.list() if s.status == ShardStatus.HEALTHY]

    # ----- synchronous convenience (routing hot path, avoids awaits) ------
    def get_sync(self, shard_id: str) -> ShardMetadata:
        try:
            return self._shards[shard_id]
        except KeyError:
            raise ShardNotFoundError(shard_id) from None

    def list_sync(self) -> list[ShardMetadata]:
        return [self._shards[k] for k in sorted(self._shards, key=_shard_sort_key)]

    def ids_sync(self) -> list[str]:
        return [s.id for s in self.list_sync()]

    def update_loads(
        self, request_counts: dict[str, int], *, hot_ids: set[str] | None = None
    ) -> None:
        """Recalculate each shard's fraction of total request volume."""
        total = sum(request_counts.values())
        for sid, shard in self._shards.items():
            shard.load = round(request_counts.get(sid, 0) / total, 4) if total else 0.0
            shard.is_hot = sid in (hot_ids or set())
            shard.touch()

    def set_row_counts(self, counts: dict[str, int]) -> None:
        for sid, n in counts.items():
            if sid in self._shards:
                self._shards[sid].row_count = n
                self._shards[sid].touch()


def _shard_sort_key(shard_id: str) -> tuple[int, str]:
    if "-" in shard_id and shard_id.split("-")[-1].isdigit():
        return (int(shard_id.split("-")[-1]), shard_id)
    return (10**9, shard_id)
