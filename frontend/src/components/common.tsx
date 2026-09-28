/** Small shared UI pieces (status dots, labels for simulated subsystems). */

import type { ReactNode } from 'react';

export type Status = 'healthy' | 'degraded' | 'unavailable' | 'up' | 'down' | 'unknown' | 'ready';

export function StatusDot({ status }: { status: Status | string }) {
  const normalized = String(status).toLowerCase();
  const level =
    normalized === 'healthy' || normalized === 'up' || normalized === 'ready'
      ? 'ok'
      : normalized === 'degraded'
        ? 'warn'
        : normalized === 'unavailable' || normalized === 'down' || normalized === 'not_ready'
          ? 'bad'
          : 'muted';
  return <span className={`dot dot-${level}`} title={String(status)} />;
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  badge,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <section className="card">
      <header className="card-head">
        <div>
          <h3>
            {title} {badge}
          </h3>
          {subtitle && <p className="muted small">{subtitle}</p>}
        </div>
        {actions && <div className="card-actions">{actions}</div>}
      </header>
      <div className="card-body">{children}</div>
    </section>
  );
}

/**
 * Any subsystem that is not actually running gets this badge. Used so the UI
 * never presents a simulation as a real distributed feature.
 */
export function Simulated({ what }: { what: string }) {
  return (
    <span className="badge badge-simulated" title={`${what} is simulated in this deployment`}>
      SIMULATED: {what}
    </span>
  );
}

export function Inspired({ what }: { what: string }) {
  return (
    <span
      className="badge badge-inspired"
      title="educational implementation inspired by publicly discussed concepts"
    >
      INSPIRED BY: {what}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="muted small">{children}</p>;
}

export function ErrorBox({ message }: { message: string }) {
  return <div className="error-box">Error: {message}</div>;
}

export function kv(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
