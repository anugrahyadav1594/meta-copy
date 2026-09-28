/**
 * Replication panel.
 *
 * IMPORTANT: with no SHARD_n_REPLICA_URL configured the system is
 * primary-only, so the failover button is explicitly labelled
 * "FAILOVER SIMULATION" — it demonstrates the routing logic, it does not
 * promote a real standby.
 */

import { useCallback, useEffect, useState } from 'react';
import { endpoints, type Json } from '../api';
import { Card, Empty, ErrorBox, Simulated, StatusDot } from './common';

export function ReplicationPanel({ refreshKey }: { refreshKey: number }) {
  const [status, setStatus] = useState<Json | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await endpoints.replicationStatus();
      setStatus(res.data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function simulateFailover(shardId: string) {
    setBusy(true);
    try {
      const res = await endpoints.replicationFailover(shardId);
      setResult(JSON.stringify(res.data).slice(0, 400));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const shards = (status?.shards ?? []) as Json[];

  return (
    <Card
      title="Replication (Member 5)"
      subtitle="read routing inside a shard: primary vs replica"
      badge={<Simulated what="failover demo" />}
    >
      {error && <ErrorBox message={error} />}
      {!status ? (
        <Empty>Replication provider is not active in this deployment.</Empty>
      ) : (
        <>
          <p className="muted small">
            replication active: <b>{String(status.replication_active)}</b> · reads routed to:{' '}
            <b>{String(status.read_routing ?? 'primary')}</b>
            {status.replication_active
              ? ''
              : ' — no SHARD_n_REPLICA_URL is configured, so every read goes to the primary'}
          </p>
          <table className="table compact">
            <thead>
              <tr>
                <th>shard</th>
                <th>primary</th>
                <th>replica</th>
                <th>read routing</th>
                <th>action</th>
              </tr>
            </thead>
            <tbody>
              {shards.map((shard) => (
                <tr key={String(shard.shard_id)}>
                  <td className="mono">{String(shard.shard_id)}</td>
                  <td>
                    <StatusDot status={shard.primary?.reachable ? 'up' : 'down'} /> primary
                  </td>
                  <td className="muted">
                    {shard.replica_configured ? (
                      <>
                        <StatusDot status={shard.replica?.reachable ? 'up' : 'down'} /> replica
                      </>
                    ) : (
                      'not configured'
                    )}
                  </td>
                  <td className="mono">{String(shard.read_routing)}</td>
                  <td>
                    <button
                      className="btn tiny"
                      disabled={busy}
                      onClick={() => void simulateFailover(String(shard.shard_id))}
                    >
                      FAILOVER SIMULATION
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {result && <pre className="sql">{result}</pre>}
          <p className="muted small">
            A real promotion needs a real standby (streaming replication). This demo only walks the
            provider's routing decision so the concept can be shown without containers.
          </p>
        </>
      )}
    </Card>
  );
}
