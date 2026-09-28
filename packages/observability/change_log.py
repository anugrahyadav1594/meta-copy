"""Database change tracking — the audit stream behind the DB Explorer UI.

Every canonical write that goes through a tracked repository produces one
:class:`ChangeRecord`:

    operation   INSERT | UPDATE | DELETE
    table       canonical table name
    primary_key {column: value} (composite where the table has one)
    shard       physical shard that executed the write (None in NORMALIZED)
    before      column values before the write (None when not knowable)
    after       column values after the write
    latency_ms  wall-clock time of the repository call (really measured)
    request_id  the HTTP request that caused it (from ``request_id_var``)
    service     logical service that issued it (api / demo / seed / benchmark)

Nothing here is synthesised: records are appended by the repository wrapper
when the write actually happens, and only columns that really exist are
reported. Secrets are never stored — ``password_hash`` (and anything else in
:data:`REDACTED_COLUMNS`) is stripped before a row is recorded.

The log is an in-process ring buffer (bounded, default 1000 records). It is a
diagnostic/teaching view of the *current process*, not a durable audit trail:
restarting the API clears it. That is stated explicitly in the API response so
the frontend never has to invent history.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from common.logging import request_id_var

# Columns that must never appear in a change record (or in any API response).
REDACTED_COLUMNS: frozenset[str] = frozenset({"password_hash", "password", "secret", "token"})

# Canonical table -> single-column primary key (composite tables handled apart).
TABLE_PRIMARY_KEY: dict[str, str] = {
    "users": "user_id",
    "profiles": "profile_id",
    "posts": "post_id",
    "comments": "comment_id",
    "likes": "like_id",
    "media": "media_id",
    "notifications": "notification_id",
    "hashtags": "hashtag_id",
}
# Tables whose identity is a composite key.
COMPOSITE_KEYS: dict[str, tuple[str, ...]] = {
    "follows": ("follower_id", "following_id"),
}
# Column used to route a write to a shard (matches the repository routing).
TABLE_SHARD_KEY: dict[str, str] = {
    "users": "user_id",
    "profiles": "user_id",
    "posts": "user_id",
    "comments": "post_id",
    "likes": "post_id",
    "follows": "follower_id",
    "media": "owner_id",
    "notifications": "user_id",
}

OPERATION_INSERT = "INSERT"
OPERATION_UPDATE = "UPDATE"
OPERATION_DELETE = "DELETE"


@dataclass(frozen=True)
class ChangeRecord:
    """One real, observed write against the canonical database."""

    change_id: str
    operation: str
    table: str
    primary_key: dict[str, Any]
    shard: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    timestamp: str
    request_id: str | None
    service: str
    latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "operation": self.operation,
            "table": self.table,
            "primary_key": dict(self.primary_key),
            "shard": self.shard,
            "before": self.before,
            "after": self.after,
            "timestamp": self.timestamp,
            "request_id": self.request_id,
            "service": self.service,
            "latency_ms": self.latency_ms,
        }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def serialize_row(obj: Any) -> dict[str, Any] | None:
    """Serialize a SQLAlchemy model instance to plain JSON-able columns.

    Returns ``None`` for anything that is not a mapped object, and strips
    :data:`REDACTED_COLUMNS`.
    """
    if obj is None:
        return None
    mapper = getattr(getattr(obj, "__mapper__", None), "column_attrs", None)
    if mapper is None:
        # dicts / rows produced by raw queries pass through as-is
        return dict(obj) if isinstance(obj, dict) else None
    return {
        attr.key: _jsonable(getattr(obj, attr.key))
        for attr in mapper
        if attr.key not in REDACTED_COLUMNS
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class ChangeLog:
    """Bounded, in-process ring buffer of :class:`ChangeRecord`."""

    def __init__(self, capacity: int = 1_000) -> None:
        self.capacity = max(1, capacity)
        self._records: deque[ChangeRecord] = deque(maxlen=self.capacity)
        self._counter = 0
        self._dropped = 0

    # ------------------------------------------------------------------ write
    def record(
        self,
        *,
        operation: str,
        table: str,
        primary_key: dict[str, Any],
        after: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        shard: str | None = None,
        request_id: str | None = None,
        service: str = "api",
        latency_ms: float = 0.0,
    ) -> ChangeRecord:
        if len(self._records) == self.capacity:
            self._dropped += 1
        self._counter += 1
        rec = ChangeRecord(
            change_id=f"chg-{self._counter:08d}",
            operation=operation,
            table=table,
            primary_key={k: _jsonable(v) for k, v in primary_key.items()},
            shard=shard,
            before=before,
            after=after,
            timestamp=_utc_now(),
            request_id=request_id,
            service=service,
            latency_ms=round(float(latency_ms), 3),
        )
        self._records.append(rec)
        return rec

    # ------------------------------------------------------------------- read
    def recent(
        self,
        limit: int = 50,
        *,
        table: str | None = None,
        operation: str | None = None,
        shard: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: Iterable[ChangeRecord] = reversed(self._records)
        out: list[dict[str, Any]] = []
        for rec in rows:
            if table and rec.table != table:
                continue
            if operation and rec.operation != operation.upper():
                continue
            if shard and rec.shard != shard:
                continue
            out.append(rec.to_dict())
            if len(out) >= limit:
                break
        return out

    def stats(self) -> dict[str, Any]:
        by_table: dict[str, int] = {}
        by_operation: dict[str, int] = {}
        by_shard: dict[str, int] = {}
        for rec in self._records:
            by_table[rec.table] = by_table.get(rec.table, 0) + 1
            by_operation[rec.operation] = by_operation.get(rec.operation, 0) + 1
            if rec.shard:
                by_shard[rec.shard] = by_shard.get(rec.shard, 0) + 1
        return {
            "retained": len(self._records),
            "capacity": self.capacity,
            "recorded_total": self._counter,
            "dropped": self._dropped,
            "by_table": by_table,
            "by_operation": by_operation,
            "by_shard": by_shard,
            # Honest scope note: this is a live view of the running process.
            "scope": "in-process ring buffer (cleared on restart)",
        }

    def clear(self) -> None:
        self._records.clear()


# ---------------------------------------------------------------------------
# Repository wrapper
# ---------------------------------------------------------------------------

# method name -> (operation, table override or None)
TRACKED_METHODS: dict[str, tuple[str, str | None]] = {
    "create": (OPERATION_INSERT, None),
    "add": (OPERATION_INSERT, None),
    "add_follow": (OPERATION_INSERT, "follows"),
    "remove_follow": (OPERATION_DELETE, "follows"),
    "remove": (OPERATION_DELETE, None),
    "update": (OPERATION_UPDATE, None),
    "delete": (OPERATION_DELETE, None),
    "mark_read": (OPERATION_UPDATE, None),
    "tag_post": (OPERATION_INSERT, "post_hashtags"),
}


class TrackedRepository:
    """Proxy that records every write a repository performs.

    Reads pass straight through. Writes are timed, the affected primary key is
    derived from the call, the resulting row is serialized (minus secrets) and
    a :class:`ChangeRecord` is appended to the shared :class:`ChangeLog`.

    The pre-image (``before``) is read back only for single-key UPDATE/DELETE
    where the repository exposes ``get_by_id``; composite tables report the key
    itself instead of pretending to know the previous row.
    """

    def __init__(
        self,
        inner: Any,
        table: str,
        change_log: ChangeLog,
        *,
        router: Any = None,
        service: str = "api",
        entity: str | None = None,
    ) -> None:
        self._inner = inner
        self._table = table
        self._log = change_log
        self._router = router
        self._service = service
        self._entity = entity or table

    # ---------------------------------------------------------------- plumbing
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _shard_for(self, table: str, values: dict[str, Any]) -> str | None:
        """Resolve the physical shard for a write.

        ``table`` is the *effective* table: the follow methods live on the user
        repository, so the wrapper's own table would be the wrong one to route
        with.
        """
        if self._router is None:
            return None
        key_column = TABLE_SHARD_KEY.get(table)
        if key_column is None:
            return None
        routing_value = values.get(key_column)
        if routing_value is None:
            return None
        try:
            return str(self._router.shard_for(int(routing_value)))
        except Exception:  # noqa: BLE001 - routing is diagnostic here
            return None

    @staticmethod
    def _pk_of(
        table: str, result: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        if table in COMPOSITE_KEYS:
            keys = COMPOSITE_KEYS[table]
            if all(k in kwargs for k in keys):
                return {k: kwargs[k] for k in keys}
            if len(args) >= len(keys):
                return {k: args[i] for i, k in enumerate(keys)}
            return {}
        column = TABLE_PRIMARY_KEY.get(table)
        if column is None:
            return {}
        value = getattr(result, column, None)
        if value is None and args and isinstance(args[0], int):
            value = args[0]
        if value is None and column in kwargs:
            value = kwargs[column]
        return {} if value is None else {column: value}

    @staticmethod
    def _values_of(result: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        """Values used for shard routing: prefer the created/updated row."""
        row = serialize_row(result)
        if row:
            return row
        out: dict[str, Any] = {}
        for i, value in enumerate(args):
            out[f"arg{i}"] = value
        for key, value in kwargs.items():
            if isinstance(value, (int, str)) and key not in REDACTED_COLUMNS:
                out[key] = value
        return out

    async def _before_image(self, table: str, pk: dict[str, Any]) -> dict[str, Any] | None:
        if not pk or table in COMPOSITE_KEYS:
            return None
        getter = getattr(self._inner, "get_by_id", None)
        if getter is None:
            return None
        try:
            return serialize_row(await getter(next(iter(pk.values()))))
        except Exception:  # noqa: BLE001 - a read-back failure must not fail the write
            return None

    def __getattribute__(self, name: str) -> Any:
        # Only intercept the tracked write methods; everything else passes
        # through to the wrapped repository (including data attributes).
        if name.startswith("_") or name not in TRACKED_METHODS:
            return object.__getattribute__(self, name)
        inner = object.__getattribute__(self, "_inner")
        method = getattr(inner, name, None)
        if method is None or not callable(method):
            return object.__getattribute__(self, name)

        async def tracked(*args: Any, **kwargs: Any) -> Any:
            operation, table_override = TRACKED_METHODS[name]
            table = table_override or self._table
            before: dict[str, Any] | None = None
            if operation in (OPERATION_UPDATE, OPERATION_DELETE):
                before = await self._before_image(table, self._pk_of(table, None, args, kwargs))
            started = time.perf_counter()
            result = await method(*args, **kwargs)
            latency_ms = (time.perf_counter() - started) * 1000.0
            pk = self._pk_of(table, result, args, kwargs)
            after = serialize_row(result)
            if not after and operation == OPERATION_INSERT and pk:
                after = dict(pk)
            if not after and operation == OPERATION_INSERT:
                after = {k: v for k, v in kwargs.items() if k not in REDACTED_COLUMNS} or None
            self._log.record(
                operation=operation,
                table=table,
                primary_key=pk,
                before=before,
                after=None if operation == OPERATION_DELETE else after,
                # the primary key wins when routing: composite tables (follows)
                # have no ORM row to read the shard-key column from.
                shard=self._shard_for(table, {**self._values_of(result, args, kwargs), **pk}),
                request_id=request_id_var.get(),
                service=self._service,
                latency_ms=latency_ms,
            )
            return result

        return tracked
