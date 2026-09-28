/**
 * Thin typed client for the MetaScale API.
 *
 * Everything goes through a RELATIVE path: in development the Vite dev server
 * proxies /api to the FastAPI process, in production the API serves the built
 * bundle. The browser never needs a hard-coded host.
 *
 * Every call also returns the request id and the measured round-trip time so
 * the UI can show the real path a request took (cache hit/miss, shard, events)
 * instead of a decorative animation.
 */

export type Json = Record<string, any>;

export interface ApiResult<T> {
  data: T;
  requestId: string | null;
  cache: string | null;
  shard: string | null;
  latencyMs: number;
}

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
  params?: Record<string, string | number | undefined>,
): Promise<ApiResult<T>> {
  const url = new URL(path, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
  }
  const started = performance.now();
  const response = await fetch(url.toString(), {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const latencyMs = performance.now() - started;
  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }
  if (!response.ok) {
    const detail =
      parsed && typeof parsed === 'object' && 'detail' in (parsed as Json)
        ? String((parsed as Json).detail)
        : response.statusText;
    throw new ApiError(response.status, detail, parsed);
  }
  return {
    data: (parsed ?? null) as T,
    requestId: response.headers.get('X-Request-ID'),
    cache: response.headers.get('X-Cache'),
    shard: response.headers.get('X-Shard-Id'),
    latencyMs,
  };
}

export const api = {
  get: <T>(path: string, params?: Record<string, string | number | undefined>) =>
    request<T>('GET', path, undefined, params),
  post: <T>(path: string, body?: unknown, params?: Record<string, string | number | undefined>) =>
    request<T>('POST', path, body ?? {}, params),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body ?? {}),
  del: <T>(path: string, params?: Record<string, string | number | undefined>) =>
    request<T>('DELETE', path, undefined, params),
};

// ---------------------------------------------------------------- endpoints
export const endpoints = {
  health: () => api.get<Json>('/api/v1/health'),
  observability: () => api.get<Json>('/api/v1/observability/status'),
  metrics: () => api.get<Json>('/api/v1/metrics'),
  latency: () => api.get<Json>('/api/v1/observability/latency'),

  dbOverview: () => api.get<Json>('/api/v1/database/overview'),
  dbTables: () => api.get<Json>('/api/v1/database/tables'),
  dbRows: (table: string, limit = 25, shard?: string) =>
    api.get<Json>(`/api/v1/database/tables/${table}/rows`, { limit, shard }),
  dbChanges: (limit = 30, table?: string, operation?: string) =>
    api.get<Json>('/api/v1/database/changes', { limit, table, operation }),
  dbChangeStats: () => api.get<Json>('/api/v1/database/changes/stats'),
  dbClearChanges: () => api.del<null>('/api/v1/database/changes'),

  eventsOverview: () => api.get<Json>('/api/v1/events'),
  eventsRecent: (limit = 25) => api.get<Json>('/api/v1/events/recent', { limit }),
  eventTypes: () => api.get<Json>('/api/v1/events/types'),

  shards: () => api.get<Json>('/api/v1/shards'),
  shardStats: () => api.get<Json>('/api/v1/shards/stats'),
  shardDistribution: () => api.get<Json>('/api/v1/shards/distribution'),
  shardSimulateDown: (shardId: string) =>
    api.post<Json>(`/api/v1/shards/${shardId}/simulate-down`, {}),
  shardHealthCheck: (shardId: string) =>
    api.post<Json>(`/api/v1/shards/${shardId}/health-check`, {}),
  routeKey: (key: number | string) => api.get<Json>(`/api/v1/shards/route/${key}`),
  routeUser: (userId: number | string) => api.get<Json>(`/api/v1/shards/route/user/${userId}`),
  rebalance: () => api.post<Json>('/api/v1/shards/rebalance', {}),
  hotUser: (userId: number | string) => api.get<Json>(`/api/v1/shards/demo/hot-user/${userId}`),

  cacheMetrics: () => api.get<Json>('/api/v1/cache/metrics'),
  cacheHealth: () => api.get<Json>('/api/v1/cache/health'),
  cacheInvalidatePost: (postId: number | string) =>
    api.del<Json>(`/api/v1/cache/posts/${postId}`),

  replicationStatus: () => api.get<Json>('/api/v1/replication/status'),
  replicationDescribe: () => api.get<Json>('/api/v1/replication/describe'),
  replicationFailover: (shardId: string) =>
    api.post<Json>(`/api/v1/replication/demo/simulate-failover/${shardId}`, {}),

  readModelStats: () => api.get<Json>('/api/v1/read-model/stats'),
  readModelPosts: (limit = 5) => api.get<Json>('/api/v1/read-model/posts', { limit }),
  readModelRebuild: () => api.post<Json>('/api/v1/read-model/rebuild', {}),

  feedStrategies: () => api.get<Json>('/api/v1/feed/strategies'),
  feed: (userId: number | string, limit = 10) =>
    api.get<Json>(`/api/v1/users/${userId}/feed`, { limit }),
  feedStats: () => api.get<Json>('/api/v1/feed/stats'),
  feedRebuild: () => api.post<Json>('/api/v1/feed/rebuild', {}),

  search: (q: string, limit = 10) => api.get<Json>('/api/v1/search', { q, limit }),
  searchReindex: () => api.post<Json>('/api/v1/search/reindex', {}),
  searchStats: () => api.get<Json>('/api/v1/search/stats'),

  mediaStats: () => api.get<Json>('/api/v1/media/_stats/backend'),
  mediaUpload: (form: FormData) =>
    fetch('/api/v1/media/upload', { method: 'POST', body: form }).then(async (r) => ({
      data: (await r.json()) as Json,
      requestId: r.headers.get('X-Request-ID'),
      cache: r.headers.get('X-Cache'),
      shard: r.headers.get('X-Shard-Id'),
      latencyMs: 0,
    })),

  users: (limit = 10) => api.get<Json[]>('/api/v1/users', { limit }),
  createUser: (payload: { username: string; email: string; password: string; display_name?: string }) =>
    api.post<Json>('/api/v1/users', payload),
  createPost: (payload: { user_id: number; content: string; visibility?: string }) =>
    api.post<Json>('/api/v1/posts', payload),
  getPost: (postId: number | string) => api.get<Json>(`/api/v1/posts/${postId}`),
  likePost: (postId: number | string, userId: number) =>
    api.post<Json>(`/api/v1/posts/${postId}/like`, { user_id: userId }),
  commentPost: (postId: number | string, userId: number, content: string) =>
    api.post<Json>(`/api/v1/posts/${postId}/comments`, { user_id: userId, content }),
  followUser: (userId: number | string, followingId: number) =>
    api.post<Json>(`/api/v1/users/${userId}/follow`, { following_id: followingId }),

  benchmarkOperations: () => api.get<Json>('/api/v1/benchmarks/operations'),
  benchmarkRun: (operation: string, iterations = 50) =>
    api.post<Json>('/api/v1/benchmarks/run', {}, { operation, iterations }),
  benchmarkCompare: (iterations = 50, operations?: string) =>
    api.post<Json>('/api/v1/benchmarks/compare', {}, { iterations, operations }),
  benchmarkArchitectures: () => api.get<Json>('/api/v1/benchmarks/architectures'),
};
