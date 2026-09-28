# Architecture (Mermaid)

Machine-readable companion to [ARCHITECTURE.md](ARCHITECTURE.md). Every box is
code that exists in this repository; dashed boxes are external services that are
optional and fail-open.

> Educational/research project inspired by publicly discussed database and
> infrastructure concepts. Not affiliated with, endorsed by, or a reproduction
> of Meta's proprietary systems.

## System overview

```mermaid
flowchart TB
  subgraph client["Client"]
    UI["Architecture Control Center<br/>(React + TS)"]
  end

  subgraph app["FastAPI — /api/v1"]
    R["routes: users, posts, feed, search,<br/>media, shards, cache, database,<br/>events, observability, benchmarks"]
    S["services: user, post, shard,<br/>media, feed"]
    C["Cache adapter (M6)<br/>cache-aside, TTL, fail-open"]
    RP["Repository<br/>canonical | sharded"]
    SR["Shard Router (M4)<br/>consistent hash + virtual nodes"]
    RR["Replication provider (M5)<br/>primary / replica"]
    EB["EventBus<br/>InMemory | RabbitMQ"]
  end

  subgraph data["PostgreSQL — source of truth"]
    CN[("canonical")]
    S0[("shard-0")]
    S1[("shard-1")]
    S2[("shard-2")]
    S3[("shard-3")]
    S4[("shard-4 (spare)")]
  end

  subgraph derived["Derived — rebuildable projections"]
    DN[("denormalized_post_feed (M3)")]
    FE[("feed fan-out (M7)")]
    IX[("search index (M10)")]
    MD[("media metadata (M8)")]
  end

  subgraph ext["Optional external services"]
    RD[("Redis")]
    MQ[("RabbitMQ")]
    OS[("OpenSearch")]
    MO[("MinIO")]
    PM[("Prometheus")]
  end

  UI --> R --> S --> C --> RP --> SR --> RR
  RR --> CN
  RR --> S0 & S1 & S2 & S3 & S4
  RP -. scatter-gather .-> S0 & S1 & S2 & S3
  S -- "publish after commit" --> EB
  EB --> DN & FE & IX & MD
  CN -. rebuild .-> DN & FE & IX
  C -.-> RD
  EB -.-> MQ
  IX -.-> OS
  MD -.-> MO
  R --> PM
```

## Request path (the 7 steps the UI animates)

```mermaid
sequenceDiagram
  participant U as Client
  participant A as API route
  participant S as Service
  participant C as Cache (M6)
  participant R as Shard Router (M4)
  participant D as PostgreSQL (M5)
  participant E as EventBus
  participant P as Projections

  U->>A: HTTP + X-Request-ID
  A->>S: validated request
  S->>C: get(key)
  alt cache HIT
    C-->>S: value (X-Cache: HIT)
  else cache MISS / BYPASS
    C-->>S: none
    S->>R: route(shard_key)
    R-->>S: shard-N
    S->>D: SQL on shard-N (primary or replica)
    D-->>S: rows
    S->>C: set(key, ttl)
  end
  opt write
    S->>D: COMMIT
    S->>E: DomainEvent
    E->>P: fan-out to projections
  end
  S-->>A: response
  A-->>U: 200 + X-Request-ID
```

## Deployment modes

```mermaid
flowchart LR
  A["A · NORMALIZED"] --> B["B · DENORMALIZED"]
  B --> C["C · SHARDED"]
  C --> D["D · SHARDED_REPLICATED"]
  C --> E["E · SHARDED_CACHED"]
  D --> F["F · FULL_DISTRIBUTED"]
  E --> F
```

| Mode | Adds |
| --- | --- |
| A | canonical PostgreSQL, joins on read |
| B | denormalized read model, event-driven |
| C | shard router over independent PostgreSQL shards |
| D | read replicas inside each shard |
| E | Redis cache-aside above the repository |
| F | sharding + replicas + cache + derived systems together |

## Excluded this iteration

```mermaid
flowchart LR
  G["Member 9 — TAO-inspired social graph"] -- "EXCLUDED" --> L["legacy/member9-graph/<br/>reference only, not imported"]
  FOL["follows table"] -- "canonical source" --> API["/api/v1/users/{id}/followers<br/>/following"]
```
