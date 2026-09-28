/**
 * Live event feed (domain events) — polled from /api/v1/events/recent.
 * Shows the unified event contract fields: id, type, entity, id, timestamp.
 */

import { useCallback, useEffect, useState } from 'react';
import { endpoints, type Json } from '../api';
import { Empty, ErrorBox } from './common';

export function EventsFeed({ refreshKey, pollMs = 2000 }: { refreshKey: number; pollMs?: number }) {
  const [events, setEvents] = useState<Json[]>([]);
  const [stats, setStats] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [recent, overview] = await Promise.all([
        endpoints.eventsRecent(20),
        endpoints.eventsOverview(),
      ]);
      setEvents((recent.data.events ?? []) as Json[]);
      setStats(overview.data);
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

  return (
    <div className="events">
      {error && <ErrorBox message={error} />}
      <div className="changes-stats">
        <span>
          transport: <b>{String(stats?.transport ?? '—')}</b>
        </span>
        <span>
          published: <b>{String(stats?.stats?.published ?? 0)}</b>
        </span>
        <span>
          delivered: <b>{String(stats?.stats?.delivered ?? 0)}</b>
        </span>
        <span>
          failures: <b>{String(stats?.stats?.failures ?? 0)}</b>
        </span>
      </div>
      {events.length === 0 ? (
        <Empty>
          No events yet — write something (post, like, comment, follow, media) and the event appears
          here.
        </Empty>
      ) : (
        <table className="table compact">
          <thead>
            <tr>
              <th>time</th>
              <th>type</th>
              <th>entity</th>
              <th>id</th>
              <th>payload</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={String(event.event_id)}>
                <td className="small">{new Date(String(event.timestamp)).toLocaleTimeString()}</td>
                <td>
                  <span className="op op-insert">{String(event.event_type)}</span>
                </td>
                <td className="mono small">{String(event.entity_type)}</td>
                <td className="mono small">{String(event.entity_id)}</td>
                <td className="mono small">{JSON.stringify(event.payload).slice(0, 90)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
