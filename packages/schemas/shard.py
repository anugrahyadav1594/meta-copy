"""Pydantic schemas for the shard admin / sharded-data APIs (Member 4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from common.enums import QueryType, RoutingStrategy, ShardStatus
from pydantic import BaseModel, Field


class ErrorEnvelope(BaseModel):
    error: str
    message: str
    details: dict[str, Any] | None = None


class RouteResponse(BaseModel):
    key: str
    shard_id: str
    strategy: RoutingStrategy
    shard_count: int
    entity: str | None = None
    virtual_node: str | None = None
    routing_latency_ms: float | None = None


class ShardSummary(BaseModel):
    id: str
    status: ShardStatus
    host: str | None = None
    port: int | None = None
    database: str | None = None
    load: float = Field(description="Fraction of total request load, 0..1")
    row_count: int = 0
    capacity: int = 0
    active_connections: int = 0
    virtual_nodes: int = 0
    requests: int = 0
    is_hot: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ShardListResponse(BaseModel):
    strategy: RoutingStrategy
    shard_count: int
    shards: list[ShardSummary]


class TargetedTiming(BaseModel):
    shard_contacted: str
    routing_latency_ms: float
    database_latency_ms: float
    total_latency_ms: float
    query_type: QueryType = QueryType.TARGETED


class ScatterShardResult(BaseModel):
    shard_id: str
    rows: int
    latency_ms: float
    status: str
    error: str | None = None


class ScatterGatherTiming(BaseModel):
    shards_contacted: list[str]
    shard_results: list[ScatterShardResult]
    total_rows: int
    execution_time_ms: float
    merge_time_ms: float
    total_latency_ms: float
    unavailable_shards: list[str] = Field(default_factory=list)
    query_type: QueryType = QueryType.SCATTER_GATHER


class CrossShardTiming(BaseModel):
    shards_contacted: list[str]
    network_hops: int
    latency_ms: float
    query_type: QueryType = QueryType.CROSS_SHARD
    note: str = (
        "Rows were fetched separately and joined in the application; "
        "no local SQL JOIN across independent PostgreSQL instances is possible."
    )


class HealthCheckResult(BaseModel):
    shard_id: str
    status: ShardStatus
    reachable: bool
    latency_ms: float | None = None
    detail: str | None = None
    checked_at: datetime


class DistributionPoint(BaseModel):
    shard_id: str
    keys: int
    percentage: float


class DistributionResponse(BaseModel):
    strategy: RoutingStrategy
    sample_size: int
    virtual_nodes_per_shard: int
    distribution: list[DistributionPoint]


class RebalanceRequest(BaseModel):
    # Educational/demo endpoint. Add a brand new shard and migrate key ranges.
    new_shard_id: str | None = Field(
        default=None, description="Defaults to shard-<N> with N=current count"
    )
    new_shard_url: str | None = None
    source_shard_id: str | None = Field(
        default=None,
        description="Defaults to the currently hottest shard (auto-detected)",
    )
    table: str = "users"
    # SIMULATION flag: when the destination URL is not provided the workflow
    # still computes the plan and checksums against registered engines and
    # reports what *would* move; data movement against a live engine only
    # happens when both shards are registered.
    dry_run: bool = False


class RebalanceStep(BaseModel):
    phase: str
    detail: str
    at: datetime


class RebalanceResponse(BaseModel):
    migration_id: str
    source_shard_id: str
    destination_shard_id: str
    table: str
    status: str
    keys_to_move: int
    rows_migrated: int
    source_checksum: dict[str, Any]
    destination_checksum: dict[str, Any]
    duration_ms: float
    steps: list[RebalanceStep]
    simulation: bool
