"""Member 5 — replication: replica selection BELOW the shard router.

Boundary recap (never blurred):

    Member 4 chooses the **shard**.
    Member 5 chooses the **endpoint inside that shard** (primary vs replica).
    Member 6 caches **above** the repository.

What is IMPLEMENTED here
------------------------
* :class:`ReplicationAwareProvider` — a real ``ShardConnectionProvider``:
  writes always go to the primary; read-only traffic goes to a configured
  replica when it is reachable and its measured replication lag is within
  ``replica_max_lag_seconds``. Every decision is counted and reported.
* Real health checks (``SELECT 1``) and real lag measurement from PostgreSQL
  (``pg_is_in_recovery`` + ``pg_last_xact_replay_timestamp`` on the standby).
  When the configured URL is **not** a physical standby, the provider says so
  (``in_recovery: false``) instead of inventing a lag number.
* ``promote_replica()``: real failover over a real standby — it calls
  ``pg_promote()`` and then points the primary URL at the promoted node.

What is SIMULATED
-----------------
* ``simulate_failover()`` reproduces the demo from the ``Satyam`` branch
  (which only ``print``ed, slept and set an environment variable). It is kept
  solely for teaching, is flagged ``"simulated": true`` everywhere it
  appears, and never touches routing state.

Environment limitation: PostgreSQL streaming replication needs a second
server per shard. The ``replication`` compose profile ships real standby
containers, but they cannot be started in this sandbox (no Docker), so the
integration test verifies the **primary-only** path plus the status contract.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from common.logging import get_logger
from db.engine import build_connect_args
from db.shard_engine import ConnectionEndpoint, ShardConnectionProvider
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = get_logger("replication")


@dataclass
class EndpointHealth:
    shard_id: str
    role: str
    url: str
    reachable: bool
    latency_ms: float | None = None
    in_recovery: bool | None = None  # True only for a real physical standby
    lag_seconds: float | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "shard_id": self.shard_id,
            "role": self.role,
            "reachable": self.reachable,
            "latency_ms": self.latency_ms,
            "in_recovery": self.in_recovery,
            "replication_lag_seconds": self.lag_seconds,
            "error": self.error,
        }


@dataclass
class ReplicationStats:
    reads_primary: int = 0
    reads_replica: int = 0
    writes_primary: int = 0
    replica_skipped_unhealthy: int = 0
    replica_skipped_lag: int = 0
    promotions: int = 0
    simulated_failovers: int = 0
    last_promotion: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


class ReplicationAwareProvider(ShardConnectionProvider):
    """Primary/replica endpoint selection for the shard router.

    Drop-in replacement for ``PrimaryOnlyProvider``: it implements the same
    ``resolve()`` seam, so the shard engine manager and every repository stay
    unaware of replication.
    """

    def __init__(
        self,
        primary_urls: dict[str, str],
        replica_urls: dict[str, str] | None = None,
        *,
        settings: Any = None,
        read_from_replicas: bool = True,
        max_lag_seconds: float = 5.0,
    ) -> None:
        self.primary_urls = dict(primary_urls)
        self.replica_urls = dict(replica_urls or {})
        self.settings = settings
        self.read_from_replicas = read_from_replicas
        self.max_lag_seconds = max_lag_seconds
        self.stats = ReplicationStats()
        self._engines: dict[str, AsyncEngine] = {}

    # --------------------------------------------------------------- engines
    def _engine(self, url: str) -> AsyncEngine:
        engine = self._engines.get(url)
        if engine is None:
            engine = create_async_engine(
                url,
                pool_size=getattr(self.settings, "db_pool_size", 5),
                max_overflow=getattr(self.settings, "db_max_overflow", 5),
                pool_timeout=getattr(self.settings, "db_pool_timeout", 5),
                pool_pre_ping=True,
                connect_args=build_connect_args(self.settings) if self.settings else {},
            )
            self._engines[url] = engine
        return engine

    async def aclose(self) -> None:
        for engine in self._engines.values():
            await engine.dispose()
        self._engines.clear()

    # ---------------------------------------------------------------- resolve
    async def resolve(
        self,
        shard_id: str,
        metadata: Any = None,
        *,
        for_write: bool = False,
    ) -> ConnectionEndpoint:
        """Member 5's decision: which physical endpoint serves this request."""
        primary_url = self.primary_urls.get(shard_id)
        if primary_url is None:
            from common.exceptions import ShardNotFoundError

            raise ShardNotFoundError(shard_id)

        if for_write:
            self.stats.writes_primary += 1
            return ConnectionEndpoint(shard_id=shard_id, url=primary_url, role="primary")

        replica_url = self.replica_urls.get(shard_id)
        if not (self.read_from_replicas and replica_url):
            self.stats.reads_primary += 1
            return ConnectionEndpoint(shard_id=shard_id, url=primary_url, role="primary")

        health = await self.check_endpoint(shard_id, replica_url, role="replica")
        if not health.reachable:
            self.stats.replica_skipped_unhealthy += 1
            self.stats.reads_primary += 1
            logger.warning("replica_unhealthy_using_primary", shard_id=shard_id, error=health.error)
            return ConnectionEndpoint(shard_id=shard_id, url=primary_url, role="primary")
        if health.lag_seconds is not None and health.lag_seconds > self.max_lag_seconds:
            self.stats.replica_skipped_lag += 1
            self.stats.reads_primary += 1
            return ConnectionEndpoint(shard_id=shard_id, url=primary_url, role="primary")

        self.stats.reads_replica += 1
        return ConnectionEndpoint(shard_id=shard_id, url=replica_url, role="replica")

    # ----------------------------------------------------------------- probes
    async def check_endpoint(
        self, shard_id: str, url: str, role: str = "primary"
    ) -> EndpointHealth:
        """Real health check + lag probe (no values are invented)."""
        start = time.perf_counter()
        try:
            engine = self._engine(url)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                in_recovery = bool(await conn.scalar(text("SELECT pg_is_in_recovery()")) or False)
                lag: float | None = None
                if in_recovery:
                    # A physical standby: measure real replay delay.
                    value = await conn.scalar(
                        text(
                            "SELECT EXTRACT(EPOCH FROM (now() - "
                            "COALESCE(pg_last_xact_replay_timestamp(), now())))"
                        )
                    )
                    lag = float(value or 0.0)
            latency = round((time.perf_counter() - start) * 1000.0, 3)
            return EndpointHealth(
                shard_id=shard_id,
                role=role,
                url=_redact(url),
                reachable=True,
                latency_ms=latency,
                in_recovery=in_recovery,
                lag_seconds=lag,
            )
        except Exception as exc:  # noqa: BLE001 - dependency probing
            return EndpointHealth(
                shard_id=shard_id,
                role=role,
                url=_redact(url),
                reachable=False,
                latency_ms=round((time.perf_counter() - start) * 1000.0, 3),
                error=type(exc).__name__,
            )

    async def status(self) -> dict[str, Any]:
        """Per-shard replication status (real values only)."""
        shards: list[dict[str, Any]] = []
        for shard_id, primary_url in sorted(self.primary_urls.items()):
            primary = await self.check_endpoint(shard_id, primary_url, role="primary")
            replica_url = self.replica_urls.get(shard_id)
            replica = (
                await self.check_endpoint(shard_id, replica_url, role="replica")
                if replica_url
                else None
            )
            shards.append(
                {
                    "shard_id": shard_id,
                    "primary": primary.as_dict(),
                    "replica": replica.as_dict() if replica else None,
                    "replica_configured": replica_url is not None,
                    "replica_is_real_standby": bool(replica and replica.in_recovery),
                    "read_routing": (
                        "replica"
                        if replica and replica.reachable and self.read_from_replicas
                        else "primary"
                    ),
                }
            )

        total_reads = self.stats.reads_primary + self.stats.reads_replica
        return {
            "replication_active": bool(self.replica_urls),
            "read_from_replicas": self.read_from_replicas,
            "max_lag_seconds": self.max_lag_seconds,
            "shards": shards,
            "counters": {
                "reads_primary": self.stats.reads_primary,
                "reads_replica": self.stats.reads_replica,
                "writes_primary": self.stats.writes_primary,
                "replica_skipped_unhealthy": self.stats.replica_skipped_unhealthy,
                "replica_skipped_lag": self.stats.replica_skipped_lag,
                "replica_read_ratio": (
                    round(self.stats.reads_replica / total_reads, 4) if total_reads else 0.0
                ),
                "promotions": self.stats.promotions,
            },
            "note": (
                "Replicas are optional. With no SHARD_n_REPLICA_URL configured the "
                "provider serves everything from the primary and reports that "
                "honestly rather than simulating a standby."
            ),
        }

    # --------------------------------------------------------------- failover
    async def promote_replica(self, shard_id: str) -> dict[str, Any]:
        """REAL failover: promote a physical standby and re-point the primary.

        Requires the replica to be a genuine standby (``pg_is_in_recovery`` is
        true). Otherwise this raises — the demo path is
        :meth:`simulate_failover`, which is explicitly labelled simulated.
        """
        replica_url = self.replica_urls.get(shard_id)
        if not replica_url:
            raise RuntimeError(f"no replica configured for {shard_id}")

        health = await self.check_endpoint(shard_id, replica_url, role="replica")
        if not health.reachable:
            raise RuntimeError(f"replica for {shard_id} is unreachable")
        if not health.in_recovery:
            raise RuntimeError(
                f"replica for {shard_id} is not a physical standby "
                "(pg_is_in_recovery() = false); cannot promote. Use the "
                "clearly-labelled simulated failover demo instead."
            )

        engine = self._engine(replica_url)
        async with engine.connect() as conn:
            promoted = await conn.scalar(text("SELECT pg_promote(wait := true)"))
        if not promoted:
            raise RuntimeError(f"pg_promote() refused on {shard_id}")

        # Ownership moves: the promoted node becomes the primary endpoint.
        old_primary = self.primary_urls.get(shard_id)
        self.primary_urls[shard_id] = replica_url
        self.replica_urls.pop(shard_id, None)
        self.stats.promotions += 1
        event = {
            "shard_id": shard_id,
            "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "new_primary": _redact(replica_url),
            "previous_primary": _redact(old_primary) if old_primary else None,
            "simulated": False,
        }
        self.stats.last_promotion = event
        self.stats.history.append(event)
        logger.warning("replica_promoted", shard_id=shard_id)
        return event

    async def simulate_failover(self, shard_id: str) -> dict[str, Any]:
        """DEMO / SIMULATION ONLY — reproduces Satyam's failover script.

        It prints the steps the original ``failover_engine.py`` printed, waits,
        and returns a plan. It does **not** contact PostgreSQL, does not
        promote anything and does not change routing. Every field says
        ``simulated: true``.
        """
        replica_url = self.replica_urls.get(shard_id)
        self.stats.simulated_failovers += 1
        plan = {
            "simulated": True,
            "shard_id": shard_id,
            "would_promote": _redact(replica_url) if replica_url else None,
            "steps": [
                "detect primary unhealthy (docker inspect / health check)",
                "SELECT pg_promote() on the standby  [NOT EXECUTED IN SIMULATION]",
                "re-point shard routing at the promoted node  [NOT EXECUTED]",
            ],
            "note": (
                "SIMULATION: no PostgreSQL command is issued and routing is "
                "unchanged. Real promotion requires a physical standby — see "
                "promote_replica() and the `replication` compose profile."
            ),
        }
        self.stats.history.append(plan)
        logger.warning("simulated_failover_demo", shard_id=shard_id)
        return plan

    def describe(self) -> dict[str, Any]:
        return {
            "provider": "ReplicationAwareProvider",
            "replicas_configured": sorted(self.replica_urls),
            "read_from_replicas": self.read_from_replicas,
            "max_lag_seconds": self.max_lag_seconds,
        }


def _redact(url: str) -> str:
    """Never expose database credentials through the API."""
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    _, host = rest.split("@", 1)
    return f"{scheme}://***@{host}"
