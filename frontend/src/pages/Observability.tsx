/**
 * Observability page: real counters, real latency histograms and the raw
 * Prometheus exposition the scrape target returns.
 */

import { useCallback, useEffect, useState } from 'react';
import { endpoints, type Json } from '../api';
import { Card, Empty, ErrorBox, StatusDot, kv } from '../components/common';

export function ObservabilityPage() {
  const [status, setStatus] = useState<Json | null>(null);
  const [metrics, setMetrics] = useState<Json | null>(null);
  const [latency, setLatency] = useState<Json | null>(null);
  const [prometheus, setPrometheus] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [st, mt, lt] = await Promise.all([
        endpoints.observability(),
        endpoints.metrics(),
        endpoints.latency(),
      ]);
      setStatus(st.data);
      setMetrics(mt.data);
      setLatency(lt.data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    void fetch('/metrics')
      .then((r) => r.text())
      .then((text) => setPrometheus(text.split('\n').slice(0, 40).join('\n')))
      .catch(() => setPrometheus(''));
  }, []);

  const components = (status?.components ?? {}) as Record<string, Json>;
  const histograms = (latency?.histograms ?? metrics?.histograms ?? {}) as Record<string, Json>;
  const counters = (metrics?.counters ?? {}) as Record<string, number>;
  const gauges = (metrics?.gauges ?? {}) as Record<string, number>;

  return (
    <div className="page">
      <h2>Observability (Member 11)</h2>
      <p className="muted small">
        Every value here is recorded in the request path — there are no synthetic series. An empty
        histogram means that path was never exercised since the process started.
      </p>
      {error && <ErrorBox message={error} />}

      <Card title="Components" subtitle="probed on every refresh">
        <table className="table compact">
          <thead>
            <tr>
              <th>component</th>
              <th>status</th>
              <th>required</th>
              <th>detail</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(components).map(([name, component]) => (
              <tr key={name}>
                <td className="mono">{name}</td>
                <td>
                  <StatusDot status={String(component.status)} /> {String(component.status)}
                </td>
                <td>{component.required ? 'required' : 'optional'}</td>
                <td className="small">{String(component.detail ?? '')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <div className="grid-2">
        <Card title="Latency histograms (ms)" subtitle="P50/P95/P99 from real samples">
          {Object.keys(histograms).length === 0 ? (
            <Empty>No samples yet — issue some requests first.</Empty>
          ) : (
            <table className="table compact">
              <thead>
                <tr>
                  <th>metric</th>
                  <th>count</th>
                  <th>p50</th>
                  <th>p95</th>
                  <th>p99</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(histograms).map(([name, values]) => (
                  <tr key={name}>
                    <td className="mono small">{name}</td>
                    <td className="mono">{kv(values.count)}</td>
                    <td className="mono">{kv(values.p50_ms)}</td>
                    <td className="mono">{kv(values.p95_ms)}</td>
                    <td className="mono">{kv(values.p99_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Counters and gauges" subtitle="as returned by /api/v1/metrics">
          <table className="table compact">
            <thead>
              <tr>
                <th>name</th>
                <th>value</th>
              </tr>
            </thead>
            <tbody>
              {[...Object.entries(counters), ...Object.entries(gauges)].map(([name, value]) => (
                <tr key={name}>
                  <td className="mono small">{name}</td>
                  <td className="mono">{kv(value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      <Card title="Subsystem metrics" subtitle="pushed in by the module that owns them">
        <div className="grid-2">
          {['cache', 'sharding', 'events', 'search', 'read_model'].map((key) => (
            <div key={key}>
              <h4>{key}</h4>
              <pre className="sql">
                {JSON.stringify((metrics ?? {})[key] ?? {}, null, 2) === '{}'
                  ? 'not enabled / no data'
                  : JSON.stringify((metrics ?? {})[key], null, 2)}
              </pre>
            </div>
          ))}
        </div>
      </Card>

      <Card title="Prometheus exposition (first 40 lines of /metrics)">
        <pre className="sql">{prometheus || 'unavailable'}</pre>
      </Card>
    </div>
  );
}
