# Frontend — Architecture Control Center

`frontend/` is a small React + TypeScript (Vite) application. It exists because
the integrated system had no UI and the integration contract needs one live
surface where the whole path is visible.

> Educational/research project inspired by publicly discussed database and
> infrastructure concepts. Not affiliated with, endorsed by, or a reproduction
> of Meta's proprietary systems.

## Run it

```bash
make frontend          # npm install + Vite dev server on :5173
# open http://localhost:5173/dashboard
```

The dev server proxies `/api`, `/health`, `/ready` and `/metrics` to the API on
`:8000`, so the browser only ever uses **relative URLs** — no CORS, no
hard-coded host, and it works behind any preview host.

For a single-origin deployment, build it and let the API serve it:

```bash
make frontend-build    # -> frontend/dist
# the API mounts dist at /dashboard, /observability and /benchmarks when present
```

## Pages

| Route | Contents |
| --- | --- |
| `/dashboard` | status dots for every component, architecture-mode panel, 13 action buttons, the 7-step request flow, live DB changes, live events, shard map, cache panel, denormalization side-by-side, replication panel, feed/search/media demos, database explorer |
| `/observability` | component health, latency histograms (P50/P95/P99), counters and gauges, per-subsystem metrics, raw Prometheus exposition |
| `/benchmarks` | run one operation, run the comparison, A–F catalogue with capabilities and trade-offs |

## Reusable components

| Component | File | Purpose |
| --- | --- | --- |
| 7-step process | `components/RequestFlow.tsx` | `REQUEST → SERVICE → CACHE → SHARD ROUTER → DATABASE → EVENT → DERIVED`, driven by the real trace (request id, `X-Cache`, shard, latency); steps not taken are shown as SKIPPED |
| Database explorer | `components/DbExplorer.tsx` | real tables, columns and rows, each row tagged with its shard |
| Live changes | `components/DbChanges.tsx` | the change stream with a before/after diff; changed columns highlighted |
| Shard map | `components/ShardMap.tsx` | real per-shard row counts, hot-shard threshold computed from those counts, labelled failure injection |
| Cache panel | `components/CachePanel.tsx` | reads the same post three times and shows the `X-Cache` result of each read (miss then hit) |
| Denormalization | `components/DenormPanel.tsx` | normalized vs denormalized SQL side by side, both measured live |
| Replication | `components/ReplicationPanel.tsx` | per-shard primary/replica state; the failover button is labelled FAILOVER SIMULATION |
| Events feed | `components/EventsFeed.tsx` | domain events with the unified contract fields |
| Badges | `components/common.tsx` | `SIMULATED: …` and `INSPIRED BY: …` labels |

## Rules the UI follows

1. **No invented state.** Every number comes from an API call; the UI never
   fabricates latency, hit rates, shard load, replication status or DB rows.
2. **No hard-coded corpus.** Search results, feed items and media metadata are
   whatever the API returns.
3. **Simulated subsystems are labelled.** `SIMULATED: failure injection`,
   `SIMULATED: failover demo`, `INSPIRED BY: Haystack-style blob storage`,
   and the search panel states that indexing is asynchronous.
4. **Unavailable is visible.** An operation the deployment cannot run shows
   `n/a` (or `available: false` with a reason), never a placeholder number.
5. **No ranking.** The benchmarks page explains trade-offs; it has no score.

## Tech notes

* React 18 + TypeScript (strict) + Vite 5; routing with `react-router-dom`.
* No component library and no CSS framework — one `styles.css`, so the bundle
  stays small and the build has no network dependency beyond `npm install`.
* `npm run build` runs `tsc -b` first, so type errors fail the build.
