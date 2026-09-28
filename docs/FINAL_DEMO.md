# Final Demo — how to run the whole system

Three ways in, depending on what you have:

| Situation | Command | What you get |
| --- | --- | --- |
| No Docker, one command | `make demo-full` | starts a `SHARDED_CACHED` API on embedded PostgreSQL, runs the 20-step demo, stops the API |
| API already running | `make demo` | 20 steps against `http://localhost:8000` |
| Full stack with the UI | `make dev-full` in one shell, `make frontend` in another | API on `:8000`, Architecture Control Center on `:5173` |

> Educational/research project inspired by publicly discussed database and
> infrastructure concepts. Not affiliated with, endorsed by, or a reproduction
> of Meta's proprietary systems.

## A. fresh clone → running system

```bash
git clone <repo> && cd meta-copy
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]" -r requirements.txt      # or: make install

make test                                        # 141 tests, real PostgreSQL
make dev-full                                    # API, FULL_DISTRIBUTED, :8000
```

Second shell:

```bash
make frontend          # npm install + Vite dev server on :5173 (proxies /api)
# open http://localhost:5173/dashboard
```

With Docker instead:

```bash
cp .env.example .env
MODE=FULL_DISTRIBUTED CACHE_ENABLED=true DENORMALIZED_ENABLED=true \
  docker compose --profile distributed --profile observability up -d --build
curl localhost:8000/api/v1/health
```

## B. the 20-step scripted demo

`make demo` walks the system in order and prints every response:

1. health + readiness · 2. canonical user · 3. follow relationships ·
4. post (routed write) · 5. cache miss → hit · 6. comments · 7. likes ·
8. denormalized read model · 9. feed · 10. follow reads · 11. media upload ·
12. search index + autocomplete · 13. shard distribution · 14. shard routing ·
15. replication status + labelled failover simulation · 16. rebalance ·
17. canonical follower/following reads · 18. metrics (Prometheus) ·
19. read-model rebuild (proves it is derived) · 20. end-to-end summary.

## C. the interactive demo (Architecture Control Center)

`/dashboard` — buttons and what they really do:

| Button | Request | What to watch |
| --- | --- | --- |
| CREATE USER | `POST /api/v1/users` | change stream: `INSERT users` with the shard the router picked |
| CREATE POST | `POST /api/v1/posts` | `POST_CREATED` event → read model, feed, search index |
| LIKE POST | `POST /api/v1/posts/{id}/like` | `LIKE_CREATED` → denormalized counters |
| COMMENT | `POST /api/v1/posts/{id}/comments` | `COMMENT_CREATED` |
| FOLLOW USER | `POST /api/v1/users/{id}/follow` | `INSERT follows` with composite key; feed candidates change |
| GET POST | `GET /api/v1/posts/{id}` | `X-Cache` header: MISS the first time, HIT afterwards |
| GENERATE FEED | `GET /api/v1/users/{id}/feed` | follow graph → candidates → rank |
| SEARCH | `GET /api/v1/search?q=…` | derived index (asynchronous — reindex if empty) |
| MEDIA INFO | `GET /api/v1/media/_stats/backend` | MinIO or the local fallback adapter |
| RUN QUERY BENCHMARK | `POST /api/v1/benchmarks/compare` | measured percentiles, cold vs warm cache |
| SIMULATE SHARD FAILURE | `POST /api/v1/shards/{id}/simulate-down` | **labelled simulation**: the router marks the shard down, PostgreSQL keeps running |
| RUN REBALANCE | `POST /api/v1/shards/rebalance` | keys moved to the spare shard, distribution before/after |
| RUN ARCHITECTURE COMPARISON | `POST /api/v1/benchmarks/compare` + `/architectures` | capabilities and trade-offs, no ranking |

`/observability` — component health, real histograms (P50/P95/P99), Prometheus
exposition, per-subsystem metrics.

`/benchmarks` — run one operation or the whole comparison; the A–F catalogue
explains trade-offs and explicitly shows `n/a` where nothing was measured.

## D. five-minute talk track

1. **Start normalized.** One PostgreSQL, 3NF, joins on read. Create a user and a
   post; open the DB explorer and see one row per table.
2. **Add denormalization.** Create likes/comments; the read model answers a
   feed-shaped query from one table. Rebuild it to prove it is derived.
3. **Add sharding.** Look at the shard map: rows are spread over four shards,
   and every change record names the shard that executed it. Run one scatter-
   gather query and compare it with a targeted read.
4. **Add the cache.** Read the same post three times: MISS, HIT, HIT. Update the
   post: the key is invalidated and the next read misses again.
5. **Add the event bus.** Every write shows up as a domain event; the
   subscribers list shows which projections consume it.
6. **Break a shard.** Simulated failure → the topology turns red; the UI says
   clearly that no container was stopped.
7. **Compare.** Run the benchmark comparison and read the trade-off notes
   instead of a scoreboard.

## E. what NOT to claim

* Not a reproduction of Meta/Facebook/Instagram infrastructure.
* The shard failure and the replication failover are **labelled simulations**
  unless real replicas are configured.
* The search index is updated **asynchronously**; a new post may not be
  searchable for a moment.
* Benchmark numbers are measurements from one machine at one moment, not
  published results.
