# Replication (Member 5)

Replication sits **below** the shard router: Member 4 decides *which shard*,
Member 5 decides *which node inside that shard* (primary or replica).

## What is implemented

* `services/replication/provider.py` — a real `ReplicationAwareProvider` that
  holds the primary and (optionally) the replica URL of every shard, probes each
  endpoint (`reachable`, `in_recovery`, `replication_lag_seconds`) and routes
  reads to the replica only when it is healthy and inside the lag budget
  (`REPLICA_MAX_LAG_SECONDS`). Writes always go to the primary.
* `infrastructure/postgres/shards/*/002_replication.sql` + the compose
  `replication` profile create **real streaming standbys** (`pg_basebackup` +
  `standby.signal`) for shards 0–3.
* Per-shard status is exposed verbatim, including `replica_is_real_standby`, so
  the UI can tell a real standby from a configured-but-absent URL.

## What is a simulation

* `POST /api/v1/replication/demo/simulate-failover/{shard_id}` walks the
  provider's routing decision and logs it as `simulated_failover_demo`. It does
  **not** promote anything. The UI labels the button **FAILOVER SIMULATION**.
* `POST /api/v1/replication/promote/{shard_id}` marks a shard as reading from
  its promoted endpoint; with no real standby configured it stays labelled as
  the same simulation.

| Capability | Status |
| --- | --- |
| Primary/replica routing with health + lag probes | IMPLEMENTED |
| Real streaming standbys (compose `replication` profile) | IMPLEMENTED — **UNVERIFIED here** (no Docker daemon in the authoring environment) |
| Failover demo | SIMULATION (labelled everywhere it appears) |
| Automatic promotion of a real standby | NOT IMPLEMENTED |

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/replication/status` | per-shard primary/replica health, lag, `read_routing` |
| `GET /api/v1/replication/describe` | what the provider does and does not do |
| `POST /api/v1/replication/demo/simulate-failover/{shard}` | labelled simulation |
| `POST /api/v1/replication/promote/{shard}` | provider-level promotion decision |

## How to say it

* "With `SHARD_n_REPLICA_URL` configured and a real standby attached, reads are
  served from the replica while its lag is under the budget; writes always hit
  the primary."
* "Without a standby the system is primary-only, and the failover button is a
  labelled simulation — it does not stop or promote any server."

## Do not claim

* That a failover happened when no standby existed.
* Lag numbers that were never measured (the API returns `null`, not `0`).
