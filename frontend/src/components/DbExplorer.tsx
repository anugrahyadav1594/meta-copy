/**
 * Database explorer: real tables, real columns, real rows (scatter-gather in
 * shard mode). Rows are fetched per shard so the UI can show which shard each
 * row physically lives on.
 */

import { useCallback, useEffect, useState } from 'react';
import { endpoints, type Json } from '../api';
import { Empty, ErrorBox } from './common';

export function DbExplorer({ refreshKey }: { refreshKey: number }) {
  const [tables, setTables] = useState<Json[]>([]);
  const [table, setTable] = useState<string>('users');
  const [rows, setRows] = useState<Json[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [shard, setShard] = useState<string>('');
  const [shards, setShards] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadTables = useCallback(async () => {
    try {
      const res = await endpoints.dbOverview();
      setTables((res.data.tables ? Object.values(res.data.tables) : []) as Json[]);
      setShards(((res.data.shards ?? []) as Json[]).map((s) => String(s.shard_id)));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const loadRows = useCallback(async () => {
    setBusy(true);
    try {
      const res = await endpoints.dbRows(table, 25, shard || undefined);
      setRows((res.data.rows ?? []) as Json[]);
      setColumns((res.data.columns ?? []) as string[]);
      setError(null);
    } catch (err) {
      setRows([]);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [table, shard]);

  useEffect(() => {
    void loadTables();
  }, [loadTables, refreshKey]);

  useEffect(() => {
    void loadRows();
  }, [loadRows, refreshKey]);

  return (
    <div className="explorer">
      {error && <ErrorBox message={error} />}
      <div className="explorer-controls">
        <label>
          table{' '}
          <select value={table} onChange={(e) => setTable(e.target.value)}>
            {tables.map((t) => (
              <option key={String(t.table)} value={String(t.table)}>
                {String(t.table)} ({Number(t.rows).toLocaleString()} rows)
              </option>
            ))}
          </select>
        </label>
        <label>
          shard{' '}
          <select value={shard} onChange={(e) => setShard(e.target.value)}>
            <option value="">all shards (scatter-gather)</option>
            {shards.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <button className="btn tiny" disabled={busy} onClick={() => void loadRows()}>
          refresh
        </button>
      </div>

      {rows.length === 0 ? (
        <Empty>No rows returned (empty table or the shards are unreachable).</Empty>
      ) : (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                {shard === '' && <th>shard</th>}
                {columns.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index}>
                  {shard === '' && <td className="mono small">{String(row.shard)}</td>}
                  {columns.map((column) => (
                    <td key={column} className="mono small">
                      {row[column] === null || row[column] === undefined
                        ? 'NULL'
                        : String(row[column])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
