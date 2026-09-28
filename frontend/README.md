# Architecture Control Center (frontend)

React + TypeScript (Vite) UI for the integrated MetaScale system. It has one
job: show what the system is actually doing — the request path, the shard that
served a write, the cache verdict, the events a write produced and the metrics
that were measured.

> Educational/research project inspired by publicly discussed database and
> infrastructure concepts. Not affiliated with, endorsed by, or a reproduction
> of Meta's proprietary systems.

## Run

```bash
npm install
npm run dev          # http://localhost:5173/dashboard
```

The dev server proxies `/api`, `/health`, `/ready` and `/metrics` to the API on
`http://127.0.0.1:8000`, so the app only ever uses relative URLs. Start the API
first:

```bash
cd .. && make dev-full        # FULL_DISTRIBUTED on embedded PostgreSQL
```

## Build (served by the API)

```bash
npm run build        # -> dist/
```

When `dist/index.html` exists, the FastAPI app mounts it at `/dashboard`,
`/observability` and `/benchmarks`, so the whole system is one origin.

## Scripts

| Script | Does |
| --- | --- |
| `npm run dev` | Vite dev server on `0.0.0.0:5173` |
| `npm run build` | `tsc -b` (strict) then `vite build` |
| `npm run preview` | serve `dist/` |
| `npm run typecheck` | types only |

## Layout

```
src/
  api.ts                     typed fetch client; returns request id, X-Cache, latency
  App.tsx                    shell + routing + disclaimer
  pages/
    Dashboard.tsx            status, mode panel, 13 actions, all panels
    Observability.tsx        components, histograms, Prometheus output
    Benchmarks.tsx           run/compare, A-F catalogue (no ranking)
  components/
    RequestFlow.tsx          the 7-step process component
    DbExplorer.tsx           real tables and rows, tagged per shard
    DbChanges.tsx            live change stream with before/after diff
    ShardMap.tsx             per-shard counts, hot shard, labelled failure injection
    CachePanel.tsx           miss → hit → hit, real X-Cache headers
    DenormPanel.tsx          normalized vs denormalized, measured
    ReplicationPanel.tsx     primary/replica state; FAILOVER SIMULATION label
    EventsFeed.tsx           domain events
    Demos.tsx                feed / search / media demos
    common.tsx               status dots, cards, SIMULATED / INSPIRED badges
  styles.css                 single stylesheet, dark theme
```

## Rules

1. Every value comes from an API call — the UI never invents latency, hit
   rates, shard load, replication status or database rows.
2. Unavailable operations render `n/a` (or the API's `reason`), never a
   placeholder number.
3. Simulated subsystems carry a `SIMULATED:` badge; concept-inspired ones carry
   `INSPIRED BY:`.
4. No hard-coded search corpus, benchmark result or demo dataset anywhere in
   this directory.

See `../docs/FRONTEND_DEMO.md` for the full walkthrough.
