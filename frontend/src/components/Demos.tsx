/**
 * Feed / search / media demos.
 *
 * Each one calls the real endpoint and shows the real response. Async indexing
 * in the search demo is labelled as such: the index is updated by the event
 * bus, so a brand-new post may not be searchable until the event is processed.
 */

import { useCallback, useEffect, useState } from 'react';
import { endpoints, type Json } from '../api';
import { Card, Empty, ErrorBox, Inspired, kv } from './common';

export function FeedDemo({ userId, postId }: { userId: number | null; postId: number | null }) {
  const [strategy, setStrategy] = useState<Json | null>(null);
  const [feed, setFeed] = useState<Json[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function generate() {
    if (userId === null) {
      setError('create a user first');
      return;
    }
    setBusy(true);
    try {
      const [strategies, res] = await Promise.all([
        endpoints.feedStrategies(),
        endpoints.feed(userId, 10),
      ]);
      setStrategy(strategies.data);
      setFeed((res.data.items ?? res.data ?? []) as Json[]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Feed (Member 7)"
      subtitle="follow graph → candidate posts → rank"
      actions={
        <button className="btn" disabled={busy} onClick={() => void generate()}>
          generate feed
        </button>
      }
    >
      {error && <ErrorBox message={error} />}
      <p className="muted small">
        strategy: <b>{String(strategy?.strategy ?? 'pull')}</b> · ranking:{' '}
        {String(strategy?.ranking ?? '—')} · candidate limit:{' '}
        {String(strategy?.candidate_limit ?? '—')} · push table derived:{' '}
        {String(strategy?.push_table_is_derived ?? '—')}
      </p>
      {feed.length === 0 ? (
        <Empty>No feed items — the user needs to follow somebody who has posts.</Empty>
      ) : (
        <table className="table compact">
          <thead>
            <tr>
              <th>post</th>
              <th>author</th>
              <th>content</th>
            </tr>
          </thead>
          <tbody>
            {feed.slice(0, 8).map((item) => (
              <tr key={String(item.post_id)}>
                <td className="mono small">{String(item.post_id)}</td>
                <td className="small">
                  {String(item.author_username ?? item.username ?? item.user_id ?? '—')}
                </td>
                <td className="small">{String(item.content ?? '').slice(0, 60)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {postId !== null && (
        <p className="muted small">currently selected post: {String(postId)}</p>
      )}
    </Card>
  );
}

export function SearchDemo({ refreshKey }: { refreshKey: number }) {
  const [query, setQuery] = useState('meta');
  const [results, setResults] = useState<Json | null>(null);
  const [stats, setStats] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadStats = useCallback(async () => {
    try {
      const res = await endpoints.searchStats();
      setStats(res.data);
    } catch {
      /* index not ready yet */
    }
  }, []);

  useEffect(() => {
    void loadStats();
  }, [loadStats, refreshKey]);

  async function search() {
    setBusy(true);
    try {
      const res = await endpoints.search(query, 10);
      setResults(res.data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function reindex() {
    setBusy(true);
    try {
      const res = await endpoints.searchReindex();
      setStats(res.data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const posts = (results?.posts ?? []) as Json[];

  return (
    <Card
      title="Search (Member 10)"
      subtitle="derived inverted index; asynchronous indexing"
      actions={
        <>
          <button className="btn" disabled={busy} onClick={() => void search()}>
            search
          </button>
          <button className="btn" disabled={busy} onClick={() => void reindex()}>
            reindex from PostgreSQL
          </button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      <input
        className="input"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="search term"
      />
      <p className="muted small">
        Indexing is <b>asynchronous</b>: a new post becomes searchable once its POST_CREATED event
        has been processed. No results? Press “reindex from PostgreSQL”.
      </p>
      {stats && (
        <p className="muted small">
          documents indexed: {kv(stats.documents)} · provider {kv(stats.backend)} · queries{' '}
          {kv(stats.queries)}
        </p>
      )}
      {posts.length === 0 ? (
        <Empty>No matches (the index may be empty — reindex first).</Empty>
      ) : (
        <table className="table compact">
          <thead>
            <tr>
              <th>post</th>
              <th>score</th>
              <th>content</th>
            </tr>
          </thead>
          <tbody>
            {posts.slice(0, 8).map((post) => (
              <tr key={String(post.post_id ?? post.id)}>
                <td className="mono small">{String(post.post_id ?? post.id)}</td>
                <td className="mono small">{kv(post.score)}</td>
                <td className="small">{String(post.content ?? '').slice(0, 60)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

export function MediaDemo({ refreshKey }: { refreshKey: number }) {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<Json | null>(null);
  const [backend, setBackend] = useState<Json | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadBackend = useCallback(async () => {
    try {
      const res = await endpoints.mediaStats();
      setBackend(res.data);
    } catch {
      /* media backend not initialised */
    }
  }, []);

  useEffect(() => {
    void loadBackend();
  }, [loadBackend, refreshKey]);

  async function upload() {
    if (!file) {
      setError('choose a file first');
      return;
    }
    setBusy(true);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('owner_id', '1');
      form.append('mime_type', file.type || 'application/octet-stream');
      const res = await endpoints.mediaUpload(form);
      setResult(res.data);
      const stats = await endpoints.mediaStats();
      setBackend(stats.data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Media (Member 8)"
      subtitle="metadata in PostgreSQL, bytes in an object store"
      badge={<Inspired what="Haystack-style blob storage" />}
      actions={
        <button className="btn" disabled={busy} onClick={() => void upload()}>
          upload
        </button>
      }
    >
      {error && <ErrorBox message={error} />}
      <input
        className="input"
        type="file"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
      />
      <p className="muted small">
        HAYSTACK-INSPIRED EDUCATIONAL IMPLEMENTATION: metadata rows live in PostgreSQL and the
        bytes go to the configured blob store (MinIO in compose, local filesystem as the fallback
        adapter). De-duplication is by SHA-256 checksum.
      </p>
      {backend && (
        <p className="muted small">
          backend: <b>{String(backend.backend)}</b> · uploads {kv(backend.uploads)} ·
          de-duplicated {kv(backend.deduplicated_uploads)} · bytes stored{' '}
          {kv(backend.bytes_stored)}
        </p>
      )}
      {result && <pre className="sql">{JSON.stringify(result, null, 2).slice(0, 500)}</pre>}
    </Card>
  );
}
