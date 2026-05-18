/**
 * Typed fetch wrappers over the Vite proxy.
 *
 * The helpers stay deliberately thin — they handle URL composition,
 * error mapping, and JSON parsing. Caching/polling lives in TanStack
 * Query inside components, not here.
 */

import type { AskResponse, Finding, FlowRow, MetricRow } from '../types';

const ROOT = '/api';

class HttpError extends Error {
  status: number;
  body: string;
  constructor(status: number, body: string) {
    super(`HTTP ${status}: ${body.slice(0, 120)}`);
    this.status = status;
    this.body = body;
  }
}

async function getJson<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const url = new URL(`${ROOT}${path}`, window.location.origin);
  for (const [k, v] of Object.entries(params ?? {})) {
    url.searchParams.set(k, String(v));
  }
  const r = await fetch(url.pathname + url.search, { headers: { Accept: 'application/json' } });
  if (!r.ok) throw new HttpError(r.status, await r.text());
  return (await r.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${ROOT}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new HttpError(r.status, await r.text());
  return (await r.json()) as T;
}

/** /buffer/flows — flow rows from the ingestor's rolling buffer. */
export async function getFlows({ sinceS }: { sinceS: number }): Promise<FlowRow[]> {
  const resp = await getJson<{ count: number; rows: FlowRow[] }>('/buffer/flows', {
    since: `-${sinceS}s`,
  });
  return resp.rows;
}

/** /buffer/metrics — metric samples, optionally filtered by pod / metric name. */
export async function getMetrics({
  sinceS,
  pod,
  metric,
}: {
  sinceS: number;
  pod?: string;
  metric?: string;
}): Promise<MetricRow[]> {
  const params: Record<string, string | number> = { since: `-${sinceS}s` };
  if (pod) params.pod = pod;
  if (metric) params.name = metric;
  const resp = await getJson<{ count: number; rows: MetricRow[] }>('/buffer/metrics', params);
  return resp.rows;
}

/** /findings/recent — recent Findings from the coordinator's cache. */
export async function getRecentFindings({
  sinceS,
  pod,
}: {
  sinceS: number;
  pod?: string;
}): Promise<Finding[]> {
  const params: Record<string, string | number> = { since_s: sinceS };
  if (pod) params.pod = pod;
  return await getJson<Finding[]>('/findings/recent', params);
}

/** /ask — coordinator question/answer. */
export async function askCoordinator({ question }: { question: string }): Promise<AskResponse> {
  return await postJson<AskResponse>('/ask', { question });
}
