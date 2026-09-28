/**
 * Live database changes: the diff panel.
 *
 * Renders the records produced by the API's change stream (one per canonical
 * write) with before/after values. Nothing is invented client-side: if the
 * stream is empty the panel says so.
 */

import { useCallback, useEffect, useState } from 'react';
import { endpoints, type Json } from '../api';
import { Empty, ErrorBox } from './common';

interface Change {
  change_id: string;
  operation: string;
  table: string;
  primary_key: Record<string, unknown>;
  shard: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  timestamp: string;
  request_id: string | null;
  service: string;
  latency_ms: number;
}

function DiffRow({
  field,
  before,
  after,
}: {
  field: string;
  before: unknown;
  after: unknown;
}) {
  const changed = JSON.stringify(before) !== JSON.stringify(after);
  return (
    <tr className={changed ? 'diff-changed' : ''}>
      <td className="mono">{field}</td>
      <td className="mono diff-before">{before === undefined || before === null ? '—' : String(before)}</td>
      <td className="mono diff-after">{after === undefined || after === null ? '—' : String(after)}</td>
    </tr>
  );
}

export function DbChanges({ refreshKey, pollMs = 2000 }: { refreshKey: number; pollMs?: number }) {
  const [changes, setChanges] = useState<Change[]>([]);
  const [stats, setStats] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, stat] = await Promise.all([
        endpoints.dbChanges(40),
        endpoints.dbChangeStats(),
      ]);
      setChanges((list.data.changes ?? []) as Change[]);
      setStats(stat.data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
    if (pollMs <= 0) return;
    const timer = window.setInterval(() => void load(), pollMs);
    return () => window.clearInterval(timer);
  }, [load, pollMs, refreshKey]);

  const active = changes.find((c) => c.change_id === selected) ?? changes[0];

  return (
    <div className="changes">
      {error && <ErrorBox message={error} />}
      <div className="changes-stats">
        {stats ? (
          <>
            <span>
              recorded: <b>{stats.recorded_total ?? 0}</b>
            </span>
            <span>
              retained: <b>{stats.retained ?? 0}</b>/{stats.capacity ?? 0}
            </span>
            <span>
              inserts: <b>{stats.by_operation?.INSERT ?? 0}</b>
            </span>
            <span>
              updates: <b>{stats.by_operation?.UPDATE ?? 0}</b>
            </span>
            <span>
              deletes: <b>{stats.by_operation?.DELETE ?? 0}</b>
            </span>
          </>
        ) : (
          <Empty>change stream unavailable (CHANGES_ENABLED=false?)</Empty>
        )}
      </div>

      <div className="changes-split">
        <div className="changes-list">
          {changes.length === 0 ? (
            <Empty>
              No writes recorded yet. Run CREATE USER / CREATE POST / LIKE / COMMENT / FOLLOW and
              the change appears here with its shard, before/after values and request id.
            </Empty>
          ) : (
            <table className="table compact">
              <thead>
                <tr>
                  <th>#</th>
                  <th>op</th>
                  <th>table</th>
                  <th>key</th>
                  <th>shard</th>
                  <th>ms</th>
                </tr>
              </thead>
              <tbody>
                {changes.map((change) => (
                  <tr
                    key={change.change_id}
                    className={change.change_id === active?.change_id ? 'selected' : ''}
                    onClick={() => setSelected(change.change_id)}
                  >
                    <td className="mono">{change.change_id.replace('chg-', '')}</td>
                    <td>
                      <span className={`op op-${change.operation.toLowerCase()}`}>
                        {change.operation}
                      </span>
                    </td>
                    <td className="mono">{change.table}</td>
                    <td className="mono small">
                      {Object.values(change.primary_key ?? {}).join('/')}
                    </td>
                    <td className="mono small">{change.shard ?? '—'}</td>
                    <td className="mono small">{change.latency_ms?.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="changes-detail">
          {active ? (
            <>
              <div className="changes-meta">
                <div>
                  <b>{active.operation}</b> on <code>{active.table}</code> → shard{' '}
                  <b>{active.shard ?? 'n/a'}</b>
                </div>
                <div className="muted small">
                  request {active.request_id ?? '—'} · service {active.service} ·{' '}
                  {new Date(active.timestamp).toLocaleTimeString()}
                </div>
              </div>
              <table className="table compact">
                <thead>
                  <tr>
                    <th>column</th>
                    <th>before</th>
                    <th>after</th>
                  </tr>
                </thead>
                <tbody>
                  {Array.from(
                    new Set([
                      ...Object.keys(active.before ?? {}),
                      ...Object.keys(active.after ?? {}),
                    ]),
                  ).map((field) => (
                    <DiffRow
                      key={field}
                      field={field}
                      before={(active.before ?? {})[field]}
                      after={(active.after ?? {})[field]}
                    />
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <Empty>Select a change to inspect its before/after diff.</Empty>
          )}
        </div>
      </div>
    </div>
  );
}
