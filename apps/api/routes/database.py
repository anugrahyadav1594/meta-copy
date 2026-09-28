"""Database introspection + live change stream (DB Explorer data source).

Everything returned here is read from the running PostgreSQL databases with
real ``SELECT``/``COUNT`` statements. Nothing is cached, synthesised or
hard-coded: if the database is empty, these endpoints report empty tables.

Three views:
* ``/overview``        — mode, per-table row counts, per-shard distribution
* ``/tables``          — canonical schema (columns, keys, shard key)
* ``/tables/{t}/rows`` — real rows, optionally restricted to one shard
* ``/changes``         — the live write stream recorded by
  ``observability.change_log.TrackedRepository``
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from observability.change_log import (
    COMPOSITE_KEYS,
    TABLE_PRIMARY_KEY,
    TABLE_SHARD_KEY,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import Platform, get_platform

router = APIRouter(prefix="/database", tags=["database"])

# Tables exposed to the explorer (canonical PostgreSQL — the source of truth).
EXPLORABLE_TABLES: tuple[str, ...] = (
    "users",
    "profiles",
    "posts",
    "comments",
    "likes",
    "follows",
    "media",
    "notifications",
    "hashtags",
    "post_hashtags",
)

# Columns never returned by the explorer.
HIDDEN_COLUMNS: frozenset[str] = frozenset({"password_hash"})


async def _targets(platform: Platform) -> list[tuple[str | None, Any]]:
    """Yield ``(shard_id | None, session_factory)`` pairs for the live system.

    In shard modes every shard is queried (scatter-gather) and each row is
    tagged with the shard it came from; in NORMALIZED mode there is a single
    canonical database and ``shard`` is ``None``.
    """
    if platform.shard_manager is not None and platform.shard_manager.shard_ids:
        return [(sid, platform.shard_manager) for sid in platform.shard_manager.shard_ids]
    if platform.canonical_engine is None:
        return []
    return [(None, platform.canonical_engine)]


async def _scalars(
    targets: list[tuple[str | None, Any]], sql: str, **params: Any
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for shard_id, target in targets:
        if shard_id is None:  # canonical engine
            async with AsyncSession(target, expire_on_commit=False) as session:
                result = await session.execute(text(sql), params)
                for row in result.mappings().all():
                    rows.append({**dict(row), "shard": None})
        else:
            async with target.session_scope(shard_id, readonly=True) as session:
                result = await session.execute(text(sql), params)
                for row in result.mappings().all():
                    rows.append({**dict(row), "shard": shard_id})
    return rows


async def _count(targets: list[tuple[str | None, Any]], table: str) -> dict[str, Any]:
    per_shard: dict[str, int] = {}
    total = 0
    for shard_id, target in targets:
        sql = f"SELECT count(*) AS n FROM {table}"  # table is validated upstream
        if shard_id is None:
            async with AsyncSession(target, expire_on_commit=False) as session:
                value = (await session.execute(text(sql))).scalar_one()
        else:
            async with target.session_scope(shard_id, readonly=True) as session:
                value = (await session.execute(text(sql))).scalar_one()
        count = int(value or 0)
        total += count
        per_shard[shard_id or "canonical"] = count
    return {"rows": total, "per_shard": per_shard}


def _validate_table(table: str) -> str:
    if table not in EXPLORABLE_TABLES:
        raise HTTPException(
            status_code=404,
            detail=f"unknown table '{table}' (explorable: {', '.join(EXPLORABLE_TABLES)})",
        )
    return table


# --------------------------------------------------------------------- schema
@router.get("/tables")
async def list_tables(platform: Platform = Depends(get_platform)) -> dict[str, Any]:
    """Canonical table catalogue: primary key, shard key, columns, row count."""
    targets = await _targets(platform)
    tables: list[dict[str, Any]] = []
    for table in EXPLORABLE_TABLES:
        counts = await _count(targets, table) if targets else {"rows": 0, "per_shard": {}}
        tables.append(
            {
                "table": table,
                "primary_key": list(COMPOSITE_KEYS.get(table) or [TABLE_PRIMARY_KEY.get(table)]),
                "shard_key": TABLE_SHARD_KEY.get(table),
                "rows": counts["rows"],
                "per_shard": counts["per_shard"],
                "columns": _columns_for(table),
            }
        )
    return {
        "mode": platform.settings.mode.value,
        "sharding_active": platform.sharding_available,
        "source_of_truth": "postgresql",
        "tables": tables,
    }


_COLUMN_CACHE: dict[str, list[dict[str, str]]] = {}


def _columns_for(table: str) -> list[dict[str, str]]:
    """Column list from the SQLAlchemy metadata (no extra DB round-trips)."""
    if table in _COLUMN_CACHE:
        return _COLUMN_CACHE[table]
    from models.base import Base

    model_table = Base.metadata.tables.get(table)
    if model_table is None:
        _COLUMN_CACHE[table] = []
        return []
    columns = [
        {
            "name": col.name,
            "type": str(col.type).split("(")[0],
            "nullable": bool(col.nullable),
            "primary_key": bool(col.primary_key),
        }
        for col in model_table.columns
        if col.name not in HIDDEN_COLUMNS
    ]
    _COLUMN_CACHE[table] = columns
    return columns


@router.get("/overview")
async def overview(platform: Platform = Depends(get_platform)) -> dict[str, Any]:
    """Mode + real row counts + per-shard distribution for every table."""
    targets = await _targets(platform)
    tables = {table: await _count(targets, table) for table in EXPLORABLE_TABLES}
    shards: list[dict[str, Any]] = []
    if platform.shard_manager is not None:
        for sid in platform.shard_manager.shard_ids:
            shards.append(
                {
                    "shard_id": sid,
                    "status": "down" if platform.shard_manager.is_down(sid) else "up",
                    "url": _redacted_url(platform.settings.shard_urls().get(sid, "")),
                }
            )
    return {
        "mode": platform.settings.mode.value,
        "sharding_active": platform.sharding_available,
        "sharding_strategy": platform.settings.sharding_strategy,
        "source_of_truth": "postgresql",
        "shards": shards,
        "tables": tables,
        "total_rows": sum(t["rows"] for t in tables.values()),
    }


def _redacted_url(url: str) -> str:
    """Show the host/db of a DSN but never its password."""
    if not url:
        return ""
    if "@" in url:
        return "***@" + url.split("@", 1)[1]
    return url


@router.get("/tables/{table}/rows")
async def table_rows(
    table: str,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    shard: str | None = Query(default=None, description="shard id, or omit for all shards"),
    platform: Platform = Depends(get_platform),
) -> dict[str, Any]:
    """Real rows straight from PostgreSQL (scatter-gather in shard modes)."""
    _validate_table(table)
    targets = await _targets(platform)
    if shard:
        targets = [t for t in targets if t[0] == shard]
        if not targets:
            raise HTTPException(status_code=404, detail=f"unknown shard '{shard}'")
    columns = [c["name"] for c in _columns_for(table)]
    if not columns:
        raise HTTPException(status_code=500, detail=f"no metadata for table '{table}'")
    select_list = ", ".join(columns)
    order = (COMPOSITE_KEYS.get(table) or (TABLE_PRIMARY_KEY.get(table, columns[0]),))[0]
    rows = await _scalars(
        targets,
        f"SELECT {select_list} FROM {table} ORDER BY {order} LIMIT :limit OFFSET :offset",
        limit=limit,
        offset=offset,
    )
    return {
        "table": table,
        "columns": columns,
        "rows": rows,
        "count": len(rows),
        "limit": limit,
        "offset": offset,
        "shards_queried": [s for s, _ in targets],
    }


# --------------------------------------------------------------- change stream
@router.get("/changes")
async def changes(
    limit: int = Query(default=50, ge=1, le=500),
    table: str | None = Query(default=None),
    operation: str | None = Query(default=None, pattern="^(INSERT|UPDATE|DELETE)$"),
    shard: str | None = Query(default=None),
    platform: Platform = Depends(get_platform),
) -> dict[str, Any]:
    """Live write stream: one record per canonical write (newest first).

    Every record was produced by a real repository call in this process —
    operation, table, primary key, shard, before/after, request id, service and
    measured latency. Secrets (password hashes) are never stored.
    """
    if platform.change_log is None:
        return {
            "changes": [],
            "stats": {},
            "tracking": "disabled",
            "note": "CHANGES_ENABLED=false — no writes are being recorded",
        }
    return {
        "changes": platform.change_log.recent(
            limit=limit, table=table, operation=operation, shard=shard
        ),
        "stats": platform.change_log.stats(),
        "tracking": "enabled",
    }


@router.get("/changes/stats")
async def change_stats(platform: Platform = Depends(get_platform)) -> dict[str, Any]:
    if platform.change_log is None:
        return {"tracking": "disabled", "recorded_total": 0}
    return {"tracking": "enabled", **platform.change_log.stats()}


@router.delete("/changes", status_code=204)
async def clear_changes(platform: Platform = Depends(get_platform)) -> None:
    """Clear the in-process ring buffer (does not touch the database)."""
    if platform.change_log is not None:
        platform.change_log.clear()
