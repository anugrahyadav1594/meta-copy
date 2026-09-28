# Events — one contract for the whole system

Every derived system in MetaScale is fed by the **same** domain-event contract.
PostgreSQL stays the source of truth: an event describes a fact that has already
committed, never a request to do something.

## Contract

```python
@dataclass(frozen=True)
class DomainEvent:
    event_id: str            # unique per event (uuid4 hex)
    event_type: str          # POST_CREATED, LIKE_CREATED, ...
    entity_type: str         # aggregate: post | user | media | follow
    entity_id: int           # primary key of that aggregate
    timestamp: datetime      # UTC, set when the event is created
    payload: dict            # event-specific fields (never secrets)
    source: str              # service that published it
    shard_id: str | None     # physical origin, known in shard modes
```

The dataclass field names in `packages/events/bus.py` are
`aggregate` / `aggregate_id` / `occurred_at`; the API exposes the contract names
above (`entity_type`, `entity_id`, `timestamp`) so the frontend and the docs
agree with the integration contract.

## Transports

| Transport | Setting | Reality |
| --- | --- | --- |
| `InMemoryEventBus` | `EVENT_BUS=memory` (default) | **IMPLEMENTED.** Handlers really run in-process, fan-out is sequential and async, a failing handler is isolated and counted. Retains a history for `/api/v1/events/recent`. |
| `RabbitMQEventBus` | `EVENT_BUS=rabbitmq` | **IMPLEMENTED, OPTIONAL.** Publishes to a real exchange when `aio-pika` and `RABBITMQ_URL` are available; degrades to in-memory otherwise and logs it. No local history is retained — `/api/v1/events/recent` says `retained: false` instead of inventing rows. |
| `NullEventBus` | `EVENT_BUS=off` | No-op, used by tests that only want the write path. |

`EVENT_BUS_BACKEND` is accepted as an alias of `EVENT_BUS`.

## Event types and consumers

| Event | Published by | Consumed by |
| --- | --- | --- |
| `USER_CREATED` | `UserService.create` | search index (users) |
| `POST_CREATED` | `PostService.create` | denormalized read model, feed fan-out (push), search index |
| `POST_UPDATED` | `PostService.update` | denormalized read model, search index |
| `POST_DELETED` | `PostService.delete` | denormalized read model, feed fan-out, search index |
| `COMMENT_CREATED` | `PostService.comment` | denormalized counters |
| `LIKE_CREATED` / `LIKE_DELETED` | `PostService.like` / `unlike` | denormalized counters |
| `FOLLOW_CREATED` / `FOLLOW_DELETED` | `UserService.follow` / `unfollow` | feed fan-out |
| `MEDIA_CREATED` | `MediaService.upload` | media metadata projection |

## Rules

1. **Publish after commit.** A service publishes only once the canonical write
   has committed; a projection can therefore never be "ahead" of the truth.
2. **Never fail a write because of a projection.** Publishing is wrapped: a bus
   or handler error is counted (`events.failures`) and logged, never raised.
3. **Projections are rebuildable.** Every consumer can be dropped and refilled
   from PostgreSQL (`POST /api/v1/read-model/rebuild`,
   `POST /api/v1/search/reindex`, `POST /api/v1/feed/rebuild`).
4. **No secrets in payloads.** Payloads carry ids and small fields only.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/events` | contract, transport, counters, **real** subscriber list |
| `GET /api/v1/events/types` | event types and which projection consumes each |
| `GET /api/v1/events/recent?limit=&event_type=` | most recent events (in-memory transport only; `retained: false` otherwise) |

## Failure handling

```text
canonical write committed
        │
        ▼
bus.publish(event)
        │
        ├─ handler ok        → delivered += 1
        └─ handler raises    → failures += 1, logged, write already committed
```

This is why the change stream (`/api/v1/database/changes`) is the authority for
"what changed" and the event feed is the authority for "what the projections
were told".
