/**
 * The reusable 7-step request-process component.
 *
 *   REQUEST -> SERVICE -> CACHE -> SHARD ROUTER -> DATABASE -> EVENT -> DERIVED
 *
 * It is driven by a real trace captured from the last action (request id,
 * X-Cache header, shard the router picked, latency), never by a canned
 * animation. Steps the request did not take (cache disabled, no event
 * published) are rendered as SKIPPED, not as successfully executed.
 */

import { useEffect, useState } from 'react';

export interface Trace {
  label: string;
  method: string;
  path: string;
  requestId: string | null;
  cache: 'HIT' | 'MISS' | 'BYPASS' | null;
  shard: string | null;
  eventType: string | null;
  derived: string[];
  latencyMs: number;
  status: 'ok' | 'error';
  detail?: string;
}

type StepState = 'done' | 'active' | 'skipped' | 'pending';

interface Step {
  key: string;
  title: string;
  subtitle: string;
  owner: string;
}

const STEPS: Step[] = [
  { key: 'request', title: 'REQUEST', subtitle: 'HTTP + request id', owner: 'API' },
  { key: 'service', title: 'SERVICE', subtitle: 'business rules, validation', owner: 'API' },
  { key: 'cache', title: 'CACHE', subtitle: 'cache-aside lookup', owner: 'Member 6' },
  { key: 'router', title: 'SHARD ROUTER', subtitle: 'consistent hashing', owner: 'Member 4' },
  { key: 'database', title: 'DATABASE', subtitle: 'PostgreSQL (source of truth)', owner: 'M1/M5' },
  { key: 'event', title: 'EVENT', subtitle: 'published after commit', owner: 'Events' },
  { key: 'derived', title: 'DERIVED', subtitle: 'projections updated', owner: 'M3/M7/M10' },
];

function stateOf(step: string, trace: Trace | null, activeIndex: number): StepState {
  if (!trace) return 'pending';
  const index = STEPS.findIndex((s) => s.key === step);
  if (index > activeIndex) return 'pending';
  if (index === activeIndex) return 'active';
  const skipped =
    (step === 'cache' && (trace.cache === 'BYPASS' || trace.cache === null)) ||
    (step === 'event' && trace.eventType === null) ||
    (step === 'derived' && trace.derived.length === 0);
  return skipped ? 'skipped' : 'done';
}

export function RequestFlow({ trace, autoPlay = true }: { trace: Trace | null; autoPlay?: boolean }) {
  const [activeIndex, setActiveIndex] = useState(trace ? 0 : -1);

  useEffect(() => {
    if (!trace || !autoPlay) {
      setActiveIndex(trace ? STEPS.length - 1 : -1);
      return;
    }
    setActiveIndex(0);
    let index = 0;
    const timer = window.setInterval(() => {
      index += 1;
      setActiveIndex(index);
      if (index >= STEPS.length - 1) window.clearInterval(timer);
    }, 180);
    return () => window.clearInterval(timer);
  }, [trace, autoPlay]);

  return (
    <div className="flow">
      <div className="flow-steps">
        {STEPS.map((step, index) => {
          const state = stateOf(step.key, trace, activeIndex);
          return (
            <div key={step.key} className={`flow-step flow-${state}`}>
              <div className="flow-index">{index + 1}</div>
              <div className="flow-title">{step.title}</div>
              <div className="flow-sub">{step.subtitle}</div>
              <div className="flow-owner">{step.owner}</div>
              <div className="flow-state">{state.toUpperCase()}</div>
            </div>
          );
        })}
      </div>
      {trace ? (
        <div className="flow-trace">
          <div>
            <strong>{trace.label}</strong>{' '}
            <code>
              {trace.method} {trace.path}
            </code>
          </div>
          <div className="flow-meta">
            <span>request id: {trace.requestId ?? '—'}</span>
            <span>
              cache:{' '}
              <b className={trace.cache === 'HIT' ? 'ok' : trace.cache === 'MISS' ? 'warn' : 'muted'}>
                {trace.cache ?? 'not used'}
              </b>
            </span>
            <span>shard: {trace.shard ?? 'routed by key'}</span>
            <span>event: {trace.eventType ?? 'none'}</span>
            <span>latency: {trace.latencyMs.toFixed(1)} ms</span>
          </div>
          {trace.derived.length > 0 && (
            <div className="flow-meta">
              <span>derived updated: {trace.derived.join(', ')}</span>
            </div>
          )}
          {trace.detail && <div className="flow-detail">{trace.detail}</div>}
        </div>
      ) : (
        <div className="flow-trace muted">
          Run an action from the control panel — the flow fills in with the path that request
          really took.
        </div>
      )}
    </div>
  );
}
