const BASE = '/api/v1';

function headers(token: string, idempotencyKey?: string): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) h['Authorization'] = `Bearer ${token}`;
  if (idempotencyKey) h['Idempotency-Key'] = idempotencyKey;
  return h;
}

function getToken(): string {
  return localStorage.getItem('qd_token') || '';
}

/** A fresh key per user action: retries of the same action dedupe server-side, while a
 * genuinely repeated order stays a new order. */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

async function request<T>(path: string, opts?: RequestInit & { idempotencyKey?: string }): Promise<T> {
  const { idempotencyKey, ...init } = opts || {};
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { ...headers(getToken(), idempotencyKey), ...init?.headers },
  });
  if (res.status === 401) {
    localStorage.removeItem('qd_token');
    window.location.reload();
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Health & runtime
  health: () => request<{ ok: boolean; initialized: boolean; paused: boolean; epoch: number; mode: string }>('/health'),
  runtime: () => request<{ initialized: boolean; paused: boolean; epoch: number; version: number; config: any; has_secrets: boolean }>('/runtime'),

  // Setup
  setup: (body: { capital: Record<string, string>; config: any; secrets: Record<string, string> }) =>
    request<{ ok: boolean }>('/setup', { method: 'POST', body: JSON.stringify(body) }),
  updateConfig: (config: any, expected_version: number) =>
    request<{ ok: boolean; version: number }>('/config', { method: 'PUT', body: JSON.stringify({ config, expected_version }) }),
  updateCredentials: (secrets: Record<string, string>) =>
    request<{ ok: boolean }>('/credentials', { method: 'PUT', body: JSON.stringify({ secrets }) }),
  clearCredentials: () => request<{ ok: boolean }>('/credentials', { method: 'DELETE' }),

  // Run control
  pause: () => request<{ ok: boolean; epoch: number }>('/pause', { method: 'POST' }),
  resume: () => request<{ ok: boolean; epoch: number }>('/resume', { method: 'POST' }),

  // Market data
  snapshots: (params?: { market?: string; symbol?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    if (params?.market) q.set('market', params.market);
    if (params?.symbol) q.set('symbol', params.symbol);
    q.set('limit', String(params?.limit ?? 50));
    q.set('offset', String(params?.offset ?? 0));
    return request<{ total: number; items: any[] }>(`/snapshots?${q}`);
  },
  latestSnapshots: () => request<{ items: any[] }>('/snapshots/latest'),
  fetchSnapshot: (market: string, symbol: string) =>
    request<{ id: number; market: string; symbol: string; price: string }>(`/snapshots/fetch?market=${market}&symbol=${symbol}`, { method: 'POST' }),
  backfill: (market: string, symbol: string, days = 400) =>
    request<{ market: string; symbol: string; fetched: number; stored: number }>('/backfill', {
      method: 'POST', body: JSON.stringify({ market, symbol, days }), idempotencyKey: newIdempotencyKey(),
    }),
  marketStatus: () => request<{ items: any[]; markets: Record<string, any> }>('/market-status'),

  // Accounts & positions
  accounts: () => request<{ items: any[]; portfolio_value: string }>('/accounts'),
  positions: (market?: string) => {
    const q = market ? `?market=${market}` : '';
    return request<{ items: any[] }>(`/positions${q}`);
  },

  // Orders
  orders: (params?: { market?: string; symbol?: string; status?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    if (params?.market) q.set('market', params.market);
    if (params?.symbol) q.set('symbol', params.symbol);
    if (params?.status) q.set('status', params.status);
    q.set('limit', String(params?.limit ?? 100));
    q.set('offset', String(params?.offset ?? 0));
    return request<{ total: number; items: any[] }>(`/orders?${q}`);
  },
  order: (id: number) => request<any>(`/orders/${id}`),
  createOrder: (body: { market: string; symbol: string; side: string; quantity: string; kind?: string; limit_price?: string },
                idempotencyKey = newIdempotencyKey()) =>
    request<any>('/orders', { method: 'POST', body: JSON.stringify(body), idempotencyKey }),
  cancelOrder: (id: number) => request<any>(`/orders/${id}/cancel`, { method: 'POST' }),

  // Fills
  fills: (params?: { market?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    if (params?.market) q.set('market', params.market);
    q.set('limit', String(params?.limit ?? 100));
    q.set('offset', String(params?.offset ?? 0));
    return request<{ total: number; items: any[] }>(`/fills?${q}`);
  },

  // Ledger
  ledger: (params?: { market?: string; kind?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    if (params?.market) q.set('market', params.market);
    if (params?.kind) q.set('kind', params.kind);
    q.set('limit', String(params?.limit ?? 100));
    q.set('offset', String(params?.offset ?? 0));
    return request<{ total: number; items: any[] }>(`/ledger?${q}`);
  },

  // Reconcile
  reconcile: () => request<{ items: any[] }>('/reconcile'),

  // Strategies
  strategies: (params?: { market?: string; status?: string }) => {
    const q = new URLSearchParams();
    if (params?.market) q.set('market', params.market);
    if (params?.status) q.set('status', params.status);
    return request<{ items: any[] }>(`/strategies?${q}`);
  },
  strategy: (id: number) => request<any>(`/strategies/${id}`),
  evaluateStrategy: (id: number) =>
    request<{ ok: boolean; queued: boolean; job_id: number; strategy_status: string }>(
      `/strategies/${id}/evaluate`, { method: 'POST' }),
  rollbackStrategy: (id: number) =>
    request<{ ok: boolean; strategy_id: number; status: string; restored_id: number | null }>(
      `/strategies/${id}/rollback`, { method: 'POST' }),

  // Decisions
  decisions: (params?: { market?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    if (params?.market) q.set('market', params.market);
    q.set('limit', String(params?.limit ?? 100));
    q.set('offset', String(params?.offset ?? 0));
    return request<{ total: number; items: any[] }>(`/decisions?${q}`);
  },

  // AI budget, risk state and job queue
  aiBudget: () => request<{
    utc_day: string; used: { calls: number; tokens: number; cost: string };
    limits: { max_calls_day: number; max_tokens_day: number; max_cost_day: string };
    allowed: boolean; blocked_reason: string;
  }>('/ai/budget'),
  risk: (limit = 20) => request<any>(`/risk?limit=${limit}`),
  jobs: (status?: string, limit = 50) => {
    const q = new URLSearchParams();
    if (status) q.set('status', status);
    q.set('limit', String(limit));
    return request<{ items: any[]; counts: Record<string, number> }>(`/jobs?${q}`);
  },

  // Events
  events: (params?: { kind?: string; trace_id?: string; since_id?: number; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.kind) q.set('kind', params.kind);
    if (params?.trace_id) q.set('trace_id', params.trace_id);
    if (params?.since_id) q.set('since_id', String(params.since_id));
    q.set('limit', String(params?.limit ?? 200));
    return request<{ items: any[] }>(`/events?${q}`);
  },

  // Trace
  trace: (traceId: string) => request<any>(`/trace/${traceId}`),

  // Export
  exportLedger: (market?: string) => {
    const q = market ? `?market=${market}` : '';
    return fetch(`${BASE}/export/ledger${q}`, { headers: headers(getToken()) }).then(r => r.blob());
  },
  exportFills: (market?: string) => {
    const q = market ? `?market=${market}` : '';
    return fetch(`${BASE}/export/fills${q}`, { headers: headers(getToken()) }).then(r => r.blob());
  },
};

/** SSE subscription with catch-up. The browser replays Last-Event-ID on its own reconnects,
 * but a fresh subscription needs an explicit cursor or it would replay all stored events. */
export function subscribeEvents(
  onEvent: (e: { id?: number; kind: string; payload: any; trace_id?: string; created_at?: string }) => void,
  sinceId = 0,
): () => void {
  const token = getToken();
  const es = new EventSource(`${BASE}/stream?token=${encodeURIComponent(token)}&since_id=${sinceId}`);
  es.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch { /* a malformed frame must not kill the stream */ }
  };
  es.onerror = () => {
    // EventSource reconnects on its own and sends Last-Event-ID, which the server honours.
  };
  return () => es.close();
}

/** Highest event id currently stored, used as the cursor for a fresh subscription. */
export async function latestEventId(): Promise<number> {
  try {
    const res = await api.events({ limit: 1 });
    return res.items.length ? Number(res.items[res.items.length - 1].id) : 0;
  } catch {
    return 0;
  }
}
