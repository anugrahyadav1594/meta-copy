/**
 * Denormalization side-by-side: the normalized join query versus the
 * denormalized projection, both measured live by the benchmark endpoint.
 */

import { useCallback, useEffect, useState } from 'react';
import { endpoints, type Json } from '../api';
import { Card, Empty, ErrorBox, kv } from './common';

export function DenormPanel({ refreshKey }: { refreshKey: number }) {
  const [stats, setStats] = useState<Json | null>(null);
  const [rows, setRows] = useState<Json | null>(null);
  const [compare, setCompare] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [statRes, rowsRes] = await Promise.all([
        endpoints.readModelStats(),
        endpoints.readModelPosts(3),
      ]);
      setStats(statRes.data);
      setRows(rowsRes.data);
      setError(null);
    } catch (err) {
      setStats(null);
      setRows(null);
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function measure() {
    setBusy(true);
    try {
      const res = await endpoints.benchmarkCompare(30, 'normalized_read,denormalized_read');
      setCompare(res.data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function rebuild() {
    setBusy(true);
    try {
      const res = await endpoints.readModelRebuild();
      setStats(res.data);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const rowsList = (rows?.items ?? []) as Json[];

  return (
    <Card
      title="Denormalization (Member 3)"
      subtitle="projection built from domain events; always rebuildable from PostgreSQL"
      actions={
        <>
          <button className="btn" disabled={busy} onClick={() => void measure()}>
            measure both
          </button>
          <button className="btn" disabled={busy} onClick={() => void rebuild()}>
            rebuild projection
          </button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      {stats && (
        <div className="metrics-grid">
          <div>
            <span className="metric-label">events applied</span>
            <span className="metric-value">{kv(stats.events_applied)}</span>
          </div>
          <div>
            <span className="metric-label">errors</span>
            <span className="metric-value">{kv(stats.errors)}</span>
          </div>
          <div>
            <span className="metric-label">rows</span>
            <span className="metric-value">{kv(stats.rows ?? rows?.count)}</span>
          </div>
        </div>
      )}

      <div className="split">
        <div>
          <h4>Normalized read</h4>
          <pre className="sql">
            {`SELECT p.post_id, u.username, p.content,
       (SELECT count(*) FROM likes l WHERE l.post_id=p.post_id),
       (SELECT count(*) FROM comments c WHERE c.post_id=p.post_id)
FROM posts p JOIN users u ON u.user_id=p.user_id
ORDER BY p.created_at DESC LIMIT 20;`}
          </pre>
        </div>
        <div>
          <h4>Denormalized read</h4>
          <pre className="sql">
            {`SELECT post_id, author_username, content,
       like_count, comment_count
FROM denormalized_post_feed
ORDER BY created_at DESC LIMIT 20;`}
          </pre>
        </div>
      </div>

      {compare && (
        <table className="table compact">
          <thead>
            <tr>
              <th>query</th>
              <th>p50 (ms)</th>
              <th>p95 (ms)</th>
              <th>samples</th>
            </tr>
          </thead>
          <tbody>
            {(compare.results as Json[]).map((row) => (
              <tr key={row.operation as string}>
                <td className="mono">{row.operation as string}</td>
                <td className="mono">{kv(row.p50_ms)}</td>
                <td className="mono">{kv(row.p95_ms)}</td>
                <td className="mono">{kv(row.count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {rowsList.length === 0 ? (
        <Empty>
          The projection is empty. Create posts (or press “rebuild projection”) to populate it.
        </Empty>
      ) : (
        <table className="table compact">
          <thead>
            <tr>
              <th>post</th>
              <th>author</th>
              <th>likes</th>
              <th>comments</th>
            </tr>
          </thead>
          <tbody>
            {rowsList.map((row) => (
              <tr key={String(row.post_id)}>
                <td className="mono">{String(row.post_id)}</td>
                <td>{String(row.author_username ?? '—')}</td>
                <td className="mono">{kv(row.like_count)}</td>
                <td className="mono">{kv(row.comment_count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
