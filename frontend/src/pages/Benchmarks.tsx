/**
 * Benchmarks page.
 *
 * No ranking, no score. Each row explains what the measurement means and where
 * the number is unmeasured (n/a) rather than filling it in with a guess.
 */

import { useCallback, useEffect, useState } from 'react';
import { endpoints, type Json } from '../api';
import { Card, Empty, ErrorBox, kv } from '../components/common';

export function BenchmarksPage() {
  const [operations, setOperations] = useState<Json[]>([]);
  const [operation, setOperation] = useState('read_post');
  const [iterations, setIterations] = useState(50);
  const [compare, setCompare] = useState<Json | null>(null);
  const [single, setSingle] = useState<Json | null>(null);
  const [catalogue, setCatalogue] = useState<Json[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [ops, arch] = await Promise.all([
        endpoints.benchmarkOperations(),
        endpoints.benchmarkArchitectures(),
      ]);
      setOperations((ops.data.operations ?? []) as Json[]);
      setCatalogue((arch.data.architectures ?? []) as Json[]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function runSingle() {
    setBusy(true);
    try {
      const res = await endpoints.benchmarkRun(operation, iterations);
      setSingle(res.data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function runCompare() {
    setBusy(true);
    try {
      const res = await endpoints.benchmarkCompare(iterations);
      setCompare(res.data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <h2>Benchmarks</h2>
      <p className="muted small">
        All numbers on this page are measured live against the running system with real data. There
        is deliberately <b>no ranking and no score</b>: each architecture trades consistency,
        storage, latency and complexity differently, so the tables below explain trade-offs instead
        of declaring a winner. Where nothing was measured the cell says <b>n/a</b>.
      </p>
      {error && <ErrorBox message={error} />}

      <Card
        title="Run one operation"
        subtitle="pick an operation and a sample count; percentiles come from real observations"
        actions={
          <button className="btn btn-primary" disabled={busy} onClick={() => void runSingle()}>
            run
          </button>
        }
      >
        <div className="explorer-controls">
          <label>
            operation{' '}
            <select value={operation} onChange={(e) => setOperation(e.target.value)}>
              {operations.map((op) => (
                <option key={String(op.name)} value={String(op.name)} disabled={!op.available}>
                  {String(op.name)}
                  {op.available ? '' : ' (unavailable)'}
                </option>
              ))}
            </select>
          </label>
          <label>
            iterations{' '}
            <input
              className="input small-input"
              type="number"
              min={1}
              max={2000}
              value={iterations}
              onChange={(e) => setIterations(Number(e.target.value))}
            />
          </label>
        </div>
        {single ? (
          <table className="table compact">
            <tbody>
              <tr>
                <th>path</th>
                <td className="small">{String(single.path ?? '—')}</td>
              </tr>
              {single.cold ? (
                <>
                  <tr>
                    <th>cold p50 (cache miss)</th>
                    <td className="mono">{kv((single.cold as Json).p50_ms)} ms</td>
                  </tr>
                  <tr>
                    <th>warm p50 (cache hit)</th>
                    <td className="mono">{kv((single.warm as Json).p50_ms)} ms</td>
                  </tr>
                  <tr>
                    <th>hits / misses</th>
                    <td className="mono">
                      {kv(single.cache_hits)} / {kv(single.cache_misses)}
                    </td>
                  </tr>
                </>
              ) : null}
              <tr>
                <th>min / p50 / p95 / p99 / max</th>
                <td className="mono">
                  {kv(single.min_ms)} / {kv(single.p50_ms)} / {kv(single.p95_ms)} /{' '}
                  {kv(single.p99_ms)} / {kv(single.max_ms)} ms
                </td>
              </tr>
              <tr>
                <th>samples</th>
                <td className="mono">{kv(single.count)}</td>
              </tr>
              {single.documents !== undefined ? (
                <tr>
                  <th>documents in index</th>
                  <td className="mono">{kv(single.documents)}</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        ) : (
          <Empty>Run an operation to see measured percentiles.</Empty>
        )}
      </Card>

      <Card
        title="Compare paths in this deployment"
        subtitle="cache vs no cache · targeted vs scatter-gather · normalized joins vs denormalized projection"
        actions={
          <button className="btn btn-primary" disabled={busy} onClick={() => void runCompare()}>
            run comparison
          </button>
        }
      >
        {compare ? (
          <>
            <table className="table">
              <thead>
                <tr>
                  <th>operation</th>
                  <th>p50 (ms)</th>
                  <th>p95 (ms)</th>
                  <th>samples</th>
                  <th>cache hits</th>
                  <th>cache misses</th>
                </tr>
              </thead>
              <tbody>
                {(compare.results as Json[]).map((row) => {
                  const unavailable = row.available === false;
                  return (
                    <tr key={String(row.operation)}>
                      <td className="mono">{String(row.operation)}</td>
                      <td className="mono">{unavailable ? 'n/a' : kv(row.p50_ms)}</td>
                      <td className="mono">{unavailable ? 'n/a' : kv(row.p95_ms)}</td>
                      <td className="mono">{unavailable ? 'n/a' : kv(row.count)}</td>
                      <td className="mono">{unavailable ? 'n/a' : kv(row.cache_hits ?? 0)}</td>
                      <td className="mono">{unavailable ? 'n/a' : kv(row.cache_misses ?? 0)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <h4>How to read this</h4>
            <ul className="bullets">
              {(compare.interpretation as string[]).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <p className="muted small">
              measured at {String(compare.measured_at)} · mode {String(compare.mode)} ·{' '}
              {kv(compare.iterations)} iterations per operation
            </p>
          </>
        ) : (
          <Empty>Press “run comparison” to measure every available path.</Empty>
        )}
      </Card>

      <Card
        title="Architecture catalogue (A–F)"
        subtitle="capabilities and trade-offs only — results belong to a measurement, not a description"
      >
        <table className="table">
          <thead>
            <tr>
              <th>id</th>
              <th>architecture</th>
              <th>adds</th>
              <th>costs</th>
              <th>measured here</th>
            </tr>
          </thead>
          <tbody>
            {catalogue.map((arch) => (
              <tr key={String(arch.id)}>
                <td className="mono">{String(arch.id)}</td>
                <td>
                  <b>{String(arch.name)}</b>
                  <div className="muted small">{String(arch.description)}</div>
                </td>
                <td className="small">{(arch.adds as string[])?.join('; ')}</td>
                <td className="small">{(arch.costs as string[])?.join('; ')}</td>
                <td className="small muted">
                  {String(arch.id) === String(compare?.mode ?? '') ? 'n/a — see full sweep' : 'run make benchmark-unified'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">
          The full A–F sweep starts one process per architecture and writes JSON + CSV into
          <code> benchmarks/results/</code> (<code>make benchmark-unified</code>). This page never
          hard-codes those results.
        </p>
      </Card>
    </div>
  );
}
