/**
 * Shard topology: real per-shard row counts, hot-shard highlighting and a
 * clearly-labelled simulated failure control (it only flips the router's view
 * of the shard — the PostgreSQL instance is untouched).
 */

import { useCallback, useEffect, useState } from 'react';
import { endpoints, type Json } from '../api';
import { Card, Empty, ErrorBox, Simulated, StatusDot } from './common';

export function ShardMap({ refreshKey }: { refreshKey: number }) {
  const [overview, setOverview] = useState<Json | null>(null);
  const [stats, setStats] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyShard, setBusyShard] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [ov, st] = await Promise.all([endpoints.dbOverview(), endpoints.shardStats()]);
      setOverview(ov.data);
      setStats(st.data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function simulateDown(shardId: string) {
    setBusyShard(shardId);
    try {
      const res = await endpoints.shardSimulateDown(shardId);
      setNote(
        `shard ${shardId} marked ${res.data.status ?? 'down'} in the router — the PostgreSQL ` +
          `instance itself was NOT stopped (local dev server has no container to kill)`,
      );
      await load();
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyShard(null);
    }
  }

  async function bringUp(shardId: string) {
    setBusyShard(shardId);
    try {
      await endpoints.shardHealthCheck(shardId);
      setNote(`shard ${shardId} re-probed and marked up again`);
      await load();
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyShard(null);
    }
  }

  const shards: Json[] = overview?.shards ?? [];
  const tables: Record<string, Json> = overview?.tables ?? {};
  const perShardTotals: Record<string, number> = {};
  for (const table of Object.values(tables)) {
    for (const [shard, count] of Object.entries(table.per_shard ?? {})) {
      perShardTotals[shard] = (perShardTotals[shard] ?? 0) + (count as number);
    }
  }
  const counts = Object.values(perShardTotals);
  const max = counts.length ? Math.max(...counts) : 0;
  const min = counts.length ? Math.min(...counts) : 0;
  const average = counts.length ? counts.reduce((a, b) => a + b, 0) / counts.length : 0;
  const hotThreshold = average * 1.25;

  return (
    <Card
      title="Shard topology"
      subtitle="Row counts are real COUNT(*) results, scattered over every shard"
      badge={<Simulated what="failure injection" />}
    >
      {error && <ErrorBox message={error} />}
      {shards.length === 0 ? (
        <Empty>Sharding is not active in this deployment (MODE=SHARDED_* or FULL_DISTRIBUTED).</Empty>
      ) : (
        <>
          <div className="shard-grid">
            {shards.map((shard) => {
              const rows = perShardTotals[shard.shard_id as string] ?? 0;
              const hot = max > 0 && rows >= hotThreshold && rows > min;
              const down = shard.status !== 'up';
              return (
                <div
                  key={shard.shard_id as string}
                  className={`shard ${hot ? 'shard-hot' : ''} ${down ? 'shard-down' : ''}`}
                >
                  <div className="shard-head">
                    <StatusDot status={down ? 'down' : 'up'} />
                    <b>{shard.shard_id as string}</b>
                    {hot && <span className="badge badge-hot">HOT</span>}
                  </div>
                  <div className="shard-rows">{rows.toLocaleString()} rows</div>
                  <div className="shard-bar">
                    <div
                      className="shard-bar-fill"
                      style={{ width: `${max ? (rows / max) * 100 : 0}%` }}
                    />
                  </div>
                  <div className="shard-actions">
                    <button
                      className="btn tiny"
                      disabled={busyShard === shard.shard_id}
                      onClick={() => void simulateDown(shard.shard_id as string)}
                    >
                      simulate failure
                    </button>
                    <button
                      className="btn tiny"
                      disabled={busyShard === shard.shard_id}
                      onClick={() => void bringUp(shard.shard_id as string)}
                    >
                      re-probe
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
          <p className="muted small">
            Hot shard = ≥1.25× the average row count (computed here from the counts above, not
            hard-coded). Requests per shard:{' '}
            {stats ? JSON.stringify(stats.shards ?? stats).slice(0, 160) : '—'}
          </p>
          {note && <p className="note">{note}</p>}
        </>
      )}
    </Card>
  );
}
