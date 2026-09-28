/**
 * Architecture Control Center — the main dashboard.
 *
 * Every number and every row on this page comes from the running API: system
 * status from /api/v1/health, topology from /api/v1/database/overview, changes
 * from the change stream, events from the bus, metrics from the observability
 * registry. Nothing is faked and simulated subsystems are labelled.
 */

import { useCallback, useEffect, useState } from 'react';
import { endpoints, type Json } from '../api';
import { CachePanel } from '../components/CachePanel';
import { DbChanges } from '../components/DbChanges';
import { DbExplorer } from '../components/DbExplorer';
import { FeedDemo, MediaDemo, SearchDemo } from '../components/Demos';
import { DenormPanel } from '../components/DenormPanel';
import { EventsFeed } from '../components/EventsFeed';
import { ReplicationPanel } from '../components/ReplicationPanel';
import { RequestFlow, type Trace } from '../components/RequestFlow';
import { ShardMap } from '../components/ShardMap';
import { Card, ErrorBox, StatusDot, kv } from '../components/common';

const DISCLAIMER =
  'This is an independent educational/research project inspired by publicly discussed database ' +
  'and infrastructure concepts associated with large-scale social platforms. It is not ' +
  'affiliated with, endorsed by, or a reproduction of Meta’s proprietary systems.';

const MODES: { id: string; label: string; command: string }[] = [
  { id: 'NORMALIZED', label: 'A · NORMALIZED', command: 'make dev' },
  { id: 'DENORMALIZED', label: 'B · DENORMALIZED', command: 'MODE=DENORMALIZED make dev' },
  { id: 'SHARDED', label: 'C · SHARDED', command: 'make dev-sharded' },
  { id: 'SHARDED_REPLICATED', label: 'D · SHARDED + REPLICATION', command: 'MODE=SHARDED_REPLICATED make dev-sharded' },
  { id: 'SHARDED_CACHED', label: 'E · SHARDED + CACHE', command: 'MODE=SHARDED_CACHED CACHE_ENABLED=true make dev-sharded' },
  { id: 'FULL_DISTRIBUTED', label: 'F · FULL DISTRIBUTED', command: 'make dev-full' },
];

export function Dashboard() {
  const [health, setHealth] = useState<Json | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [userId, setUserId] = useState<number | null>(null);
  const [postId, setPostId] = useState<number | null>(null);
  const [output, setOutput] = useState<string | null>(null);

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  const loadHealth = useCallback(async () => {
    try {
      const res = await endpoints.health();
      setHealth(res.data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void loadHealth();
    const timer = window.setInterval(() => void loadHealth(), 5000);
    return () => window.clearInterval(timer);
  }, [loadHealth]);

  function record(label: string, method: string, path: string, res: { data?: Json; requestId: string | null; cache: string | null; shard: string | null; latencyMs: number }, eventType: string | null, derived: string[] = []) {
    setTrace({
      label,
      method,
      path,
      requestId: res.requestId,
      cache: (res.cache as Trace['cache']) ?? null,
      shard: res.shard,
      eventType,
      derived,
      latencyMs: res.latencyMs,
      status: 'ok',
    });
    refresh();
  }

  async function run(name: string, fn: () => Promise<void>) {
    setBusy(name);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(`${name}: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(null);
    }
  }

  // ---------------------------------------------------------------- actions
  const createUser = () =>
    run('CREATE USER', async () => {
      const name = `demo_${Math.floor(Math.random() * 1_000_000)}`;
      const res = await endpoints.createUser({
        username: name,
        email: `${name}@example.com`,
        password: 'demo-password',
        display_name: name,
      });
      setUserId(Number(res.data.user_id));
      record('CREATE USER', 'POST', '/api/v1/users', res, 'USER_CREATED', ['search index']);
      setOutput(JSON.stringify(res.data, null, 2));
    });

  const createPost = () =>
    run('CREATE POST', async () => {
      if (userId === null) throw new Error('create a user first');
      const res = await endpoints.createPost({
        user_id: userId,
        content: `demo post ${new Date().toISOString()} #metascale`,
        visibility: 'public',
      });
      setPostId(Number(res.data.post_id));
      record('CREATE POST', 'POST', '/api/v1/posts', res, 'POST_CREATED', [
        'denormalized read model',
        'feed fan-out',
        'search index',
      ]);
      setOutput(JSON.stringify(res.data, null, 2));
    });

  const likePost = () =>
    run('LIKE POST', async () => {
      if (postId === null) throw new Error('create a post first');
      const liker =
        userId ??
        Number(
          ((await endpoints.users(1)) as unknown as { data: Json[] }).data[0]?.user_id ?? 0,
        );
      const res = await endpoints.likePost(postId, liker);
      record('LIKE POST', 'POST', `/api/v1/posts/${postId}/like`, res, 'LIKE_CREATED', [
        'denormalized counters',
      ]);
      setOutput(JSON.stringify(res.data, null, 2));
    });

  const commentPost = () =>
    run('COMMENT', async () => {
      if (postId === null) throw new Error('create a post first');
      if (userId === null) throw new Error('create a user first');
      const res = await endpoints.commentPost(postId, userId, 'comment from the control center');
      record('COMMENT', 'POST', `/api/v1/posts/${postId}/comments`, res, 'COMMENT_CREATED', [
        'denormalized counters',
      ]);
      setOutput(JSON.stringify(res.data, null, 2));
    });

  const followUser = () =>
    run('FOLLOW USER', async () => {
      if (userId === null) throw new Error('create a user first');
      const users = (await endpoints.users(5)).data as Json[];
      const target = users.find((u) => Number(u.user_id) !== userId);
      if (!target) throw new Error('need at least two users to follow');
      const res = await endpoints.followUser(userId, Number(target.user_id));
      record('FOLLOW USER', 'POST', `/api/v1/users/${userId}/follow`, res, 'FOLLOW_CREATED', [
        'feed fan-out',
      ]);
      setOutput(JSON.stringify(res.data, null, 2));
    });

  const getPost = () =>
    run('GET POST', async () => {
      if (postId === null) throw new Error('create a post first');
      const route = await endpoints.routeKey(postId);
      const res = await endpoints.getPost(postId);
      record('GET POST', 'GET', `/api/v1/posts/${postId}`, { ...res, shard: String(route.data.shard_id ?? route.data.shard ?? null) }, null, []);
      setOutput(JSON.stringify(res.data, null, 2));
    });

  const generateFeed = () =>
    run('GENERATE FEED', async () => {
      if (userId === null) throw new Error('create a user first');
      const res = await endpoints.feed(userId, 10);
      record('GENERATE FEED', 'GET', `/api/v1/users/${userId}/feed`, res, null, []);
      setOutput(JSON.stringify(res.data, null, 2).slice(0, 800));
    });

  const search = () =>
    run('SEARCH', async () => {
      const res = await endpoints.search('metascale', 5);
      record('SEARCH', 'GET', '/api/v1/search?q=metascale', res, null, []);
      setOutput(JSON.stringify(res.data, null, 2).slice(0, 800));
    });

  const uploadMedia = () =>
    run('UPLOAD MEDIA', async () => {
      const res = await endpoints.mediaStats();
      record('MEDIA BACKEND', 'GET', '/api/v1/media/_stats/backend', res, null, []);
      setOutput(JSON.stringify(res.data, null, 2));
    });

  const runQueryBenchmark = () =>
    run('RUN QUERY BENCHMARK', async () => {
      const res = await endpoints.benchmarkCompare(30);
      record('BENCHMARK', 'POST', '/api/v1/benchmarks/compare', res, null, []);
      setOutput(JSON.stringify(res.data, null, 2).slice(0, 1500));
    });

  const simulateShardFailure = () =>
    run('SIMULATE SHARD FAILURE', async () => {
      const overview = await endpoints.dbOverview();
      const shardId = String((overview.data.shards as Json[])[0]?.shard_id ?? 'shard-0');
      const res = await endpoints.shardSimulateDown(shardId);
      record('SIMULATE FAILURE', 'POST', `/api/v1/shards/${shardId}/simulate-down`, res, null, []);
      setOutput(
        `${JSON.stringify(res.data, null, 2)}\n\nNOTE: this only marks the shard down in the ` +
          `router/health checker. The PostgreSQL instance keeps running — no real machine or ` +
          `container was stopped.`,
      );
    });

  const runRebalance = () =>
    run('RUN REBALANCE', async () => {
      const res = await endpoints.rebalance();
      record('REBALANCE', 'POST', '/api/v1/shards/rebalance', res, null, []);
      setOutput(JSON.stringify(res.data, null, 2).slice(0, 1200));
    });

  const runArchitectureComparison = () =>
    run('RUN ARCHITECTURE COMPARISON', async () => {
      const [compare, catalogue] = await Promise.all([
        endpoints.benchmarkCompare(30),
        endpoints.benchmarkArchitectures(),
      ]);
      record('ARCHITECTURE COMPARISON', 'POST', '/api/v1/benchmarks/compare', compare, null, []);
      const lines = (compare.data.results as Json[])
        .map(
          (row) =>
            `${String(row.operation).padEnd(20)} p50=${row.available === false ? 'n/a' : String(row.p50_ms)} ms`,
        )
        .join('\n');
      setOutput(
        `${lines}\n\nInterpretation:\n${(compare.data.interpretation as string[])
          .map((line) => `- ${line}`)
          .join('\n')}\n\nFor the full A-F sweep (one process per architecture) run: ` +
          `make benchmark-unified\n\n${JSON.stringify(catalogue.data.architectures, null, 2).slice(0, 1200)}`,
      );
    });

  const components = (health?.components ?? {}) as Record<string, Json>;

  return (
    <div className="page">
      <section className="status-bar">
        <div className="status-items">
          {Object.entries(components).map(([name, component]) => (
            <div key={name} className="status-item">
              <StatusDot status={String(component.status)} />
              <span className="status-name">{name}</span>
              <span className="status-value">{String(component.status)}</span>
              {component.required ? <span className="badge">required</span> : null}
            </div>
          ))}
        </div>
        <div className="status-mode">
          <span className={`badge badge-mode`}>{String(health?.mode ?? '—')}</span>
          <span className="badge">{String(health?.status ?? 'unknown')}</span>
        </div>
      </section>

      <p className="disclaimer">{DISCLAIMER}</p>

      {error && <ErrorBox message={error} />}

      <Card
        title="Architecture mode"
        subtitle="The mode is fixed when the API process starts — this panel shows the current one and how to relaunch in another"
      >
        <div className="mode-list">
          {MODES.map((mode) => (
            <div
              key={mode.id}
              className={`mode ${health?.mode === mode.id ? 'mode-active' : ''}`}
              title={mode.command}
            >
              <b>{mode.label}</b>
              <code>{mode.command}</code>
            </div>
          ))}
        </div>
      </Card>

      <Card
        title="Control panel"
        subtitle="Every button performs a real request; the flow below shows the path it took"
      >
        <div className="buttons">
          <button className="btn" disabled={busy !== null} onClick={() => void createUser()}>
            CREATE USER
          </button>
          <button className="btn" disabled={busy !== null} onClick={() => void createPost()}>
            CREATE POST
          </button>
          <button className="btn" disabled={busy !== null} onClick={() => void likePost()}>
            LIKE POST
          </button>
          <button className="btn" disabled={busy !== null} onClick={() => void commentPost()}>
            COMMENT
          </button>
          <button className="btn" disabled={busy !== null} onClick={() => void followUser()}>
            FOLLOW USER
          </button>
          <button className="btn" disabled={busy !== null} onClick={() => void getPost()}>
            GET POST
          </button>
          <button className="btn" disabled={busy !== null} onClick={() => void generateFeed()}>
            GENERATE FEED
          </button>
          <button className="btn" disabled={busy !== null} onClick={() => void search()}>
            SEARCH
          </button>
          <button className="btn" disabled={busy !== null} onClick={() => void uploadMedia()}>
            MEDIA INFO
          </button>
          <button
            className="btn btn-primary"
            disabled={busy !== null}
            onClick={() => void runQueryBenchmark()}
          >
            RUN QUERY BENCHMARK
          </button>
          <button
            className="btn btn-danger"
            disabled={busy !== null}
            onClick={() => void simulateShardFailure()}
          >
            SIMULATE SHARD FAILURE
          </button>
          <button className="btn" disabled={busy !== null} onClick={() => void runRebalance()}>
            RUN REBALANCE
          </button>
          <button
            className="btn btn-primary"
            disabled={busy !== null}
            onClick={() => void runArchitectureComparison()}
          >
            RUN ARCHITECTURE COMPARISON
          </button>
        </div>
        {busy && <p className="note">running: {busy}…</p>}
        <p className="muted small">
          current user: <b>{userId ?? '—'}</b> · current post: <b>{postId ?? '—'}</b>
        </p>
      </Card>

      <RequestFlow trace={trace} />

      {output && (
        <Card title="Last response" subtitle="raw JSON from the API">
          <pre className="sql">{output}</pre>
        </Card>
      )}

      <div className="grid-2">
        <Card title="Live database changes" subtitle="one record per canonical write">
          <DbChanges refreshKey={refreshKey} />
        </Card>
        <Card title="Live events" subtitle="domain events published after commit">
          <EventsFeed refreshKey={refreshKey} />
        </Card>
      </div>

      <ShardMap refreshKey={refreshKey} />
      <CachePanel postId={postId} refreshKey={refreshKey} />
      <DenormPanel refreshKey={refreshKey} />
      <ReplicationPanel refreshKey={refreshKey} />

      <div className="grid-2">
        <FeedDemo userId={userId} postId={postId} />
        <SearchDemo refreshKey={refreshKey} />
      </div>
      <MediaDemo refreshKey={refreshKey} />

      <Card
        title="Database explorer"
        subtitle="real rows, tagged with the shard they live on"
      >
        <DbExplorer refreshKey={refreshKey} />
      </Card>

      <Card title="Observability snapshot" subtitle="measured in the request path">
        <p className="muted small">
          requests: {kv((health as unknown as Json)?.requests ?? '—')} · see the /observability page
          for metrics, latency histograms and Prometheus output.
        </p>
      </Card>
    </div>
  );
}
