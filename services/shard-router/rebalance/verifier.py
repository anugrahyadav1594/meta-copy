"""Checksum verification before ownership switch.

For the set of rows that were migrated we compare, source vs destination:

1. row count;
2. the full primary-key set (detects missing/extra/duplicate rows);
3. an order-independent content digest — every row is serialized to JSON,
   hashed with md5, and the per-row hashes concatenated in PK order and hashed
   again (detects column-level differences).

Any mismatch raises :class:`ChecksumMismatchError`; the migrator then refuses
to switch routing ownership and leaves source data untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from common.exceptions import ChecksumMismatchError

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

# Static whitelist: identifiers are never interpolated from user input.
KNOWN_TABLES = {
    "users",
    "profiles",
    "posts",
    "comments",
    "likes",
    "follows",
    "media",
    "notifications",
}


@dataclass(frozen=True)
class TableChecksum:
    table: str
    row_count: int
    primary_keys: str  # comma-joined ordered PK values
    content_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "row_count": self.row_count,
            "primary_keys_sample": (
                self.primary_keys[:400] + ("..." if len(self.primary_keys) > 400 else "")
            ),
            "primary_key_count": len([p for p in self.primary_keys.split(",") if p]),
            "content_hash": self.content_hash,
        }


class ChecksumVerifier:
    async def checksum_for_keys(
        self,
        session: AsyncSession,
        table: str,
        key_column: str,
        keys: list[int],
    ) -> TableChecksum:
        if table not in KNOWN_TABLES:
            raise ValueError(f"refusing to checksum unknown table: {table!r}")
        if not keys:
            return TableChecksum(table=table, row_count=0, primary_keys="", content_hash="")

        # Identifiers come from the static whitelist / model metadata; values
        # are bound parameters. No raw user string is ever interpolated.
        pk = "follower_id" if table == "follows" else "id"
        sql = f"""
            WITH subset AS (
                SELECT {pk} AS pk, to_jsonb(t) AS row_json
                FROM {table} t
                WHERE {key_column} = ANY(:keys)
            )
            SELECT count(*) AS n,
                   coalesce(string_agg(pk::text, ',' ORDER BY pk), '') AS pks,
                   coalesce(md5(string_agg(md5(row_json::text), '' ORDER BY pk)), '') AS digest
            FROM subset
        """
        from sqlalchemy import text

        res = await session.execute(text(sql), {"keys": list(keys)})
        row = res.one()
        return TableChecksum(
            table=table,
            row_count=int(row.n),
            primary_keys=row.pks or "",
            content_hash=row.digest or "",
        )

    async def assert_match(
        self,
        source_session: AsyncSession,
        destination_session: AsyncSession,
        table: str,
        key_column: str,
        keys: list[int],
    ) -> tuple[TableChecksum, TableChecksum]:
        src = await self.checksum_for_keys(source_session, table, key_column, keys)
        dst = await self.checksum_for_keys(destination_session, table, key_column, keys)
        if (
            src.row_count != dst.row_count
            or src.primary_keys != dst.primary_keys
            or src.content_hash != dst.content_hash
        ):
            raise ChecksumMismatchError(src.as_dict(), dst.as_dict())
        return src, dst
