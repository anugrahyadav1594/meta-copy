/**
 * Cache panel: demonstrates a real MISS followed by a real HIT.
 *
 * The buttons read the same post twice through the API; the X-Cache response
 * header (HIT/MISS/BYPASS) decides what the panel shows, so the UI can never
 * claim a hit that did not happen.
 */

import { useCallback, useEffect, useState } from 'react';
import { endpoints, type Json } from '../api';
import { Card, Empty, ErrorBox, kv } from './common';

interface ReadLog {
  n: number;
  cache: string;
  latency: number;
}

export function CachePanel({
  postId,
  refreshKey,
}: {
  postId: number | null;
  refreshKey: number;
}) {
  const [metrics, setMetrics] = useState<Json | null>(null);
  const [log, setLog] = useState<ReadLog[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await endpoints.cacheMetrics();
      setMetrics(res.data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function readPost(times: number) {
    if (postId === null) {
      setError('create or pick a post first (use CREATE POST)');
      return;
    }
    setBusy(true);
    const entries: ReadLog[] = [];
    try {
      for (let i = 0; i < times; i += 1) {
        const res = await endpoints.getPost(postId);
        entries.push({
          n: i + 1,
          cache: (res.cache ?? 'BYPASS').toString(),
          latency: res.latencyMs,
        });
      }
      setLog(entries);
      await load();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function invalidate() {
    if (postId === null) return;
    setBusy(true);
    try {
      await endpoints.cacheInvalidatePost(postId);
      setLog([]);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Cache (Member 6)"
      subtitle="cache-aside above the repository; Redis by default, in-memory fallback in dev"
      actions={
        <>
          <button className="btn" disabled={busy || postId === null} onClick={() => void readPost(3)}>
            read post ×3
          </button>
          <button className="btn" disabled={busy || postId === null} onClick={() => void invalidate()}>
            invalidate key
          </button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      {!metrics ? (
        <Empty>Cache metrics unavailable — caching is disabled or Redis is unreachable.</Empty>
      ) : (
        <div className="metrics-grid">
          <div>
            <span className="metric-label">hits</span>
            <span className="metric-value">{kv(metrics.cache_hits)}</span>
          </div>
          <div>
            <span className="metric-label">misses</span>
            <span className="metric-value">{kv(metrics.cache_misses)}</span>
          </div>
          <div>
            <span className="metric-label">hit ratio</span>
            <span className="metric-value">{kv(metrics.hit_ratio)}%</span>
          </div>
          <div>
            <span className="metric-label">db queries avoided</span>
            <span className="metric-value">{kv(metrics.db_queries_avoided)}</span>
          </div>
          <div>
            <span className="metric-label">backend</span>
            <span className="metric-value">{kv(metrics.backend)}</span>
          </div>
          <div>
            <span className="metric-label">invalidations</span>
            <span className="metric-value">{kv(metrics.cache_invalidations)}</span>
          </div>
        </div>
      )}
      {log.length > 0 && (
        <table className="table compact">
          <thead>
            <tr>
              <th>read</th>
              <th>cache</th>
              <th>latency (ms)</th>
            </tr>
          </thead>
          <tbody>
            {log.map((entry) => (
              <tr key={entry.n}>
                <td>#{entry.n}</td>
                <td>
                  <span className={`op op-${entry.cache === 'HIT' ? 'hit' : 'miss'}`}>
                    {entry.cache}
                  </span>
                </td>
                <td className="mono">{entry.latency.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted small">
        The first read of a key always misses and fills the cache; later reads hit until the TTL
        expires or a write invalidates the key. Counters come from the real cache provider.
      </p>
    </Card>
  );
}
