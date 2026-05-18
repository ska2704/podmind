/**
 * Hard-coded data used during STAGE 1 (visual-design pass).
 *
 * The shapes match `./types` so Stage-2 swaps these for live data
 * with zero downstream changes.
 *
 * The demo cluster topology shown here mirrors the actual SmartHostel
 * deployment: gateway is the only ingress, it fans out to auth,
 * booking and room; sh-edge controllers (hvac, energy, lock) consume
 * sensor-ingest. guest-sim drives gateway from outside the graph.
 * hvac-controller is the anomalous pod for the stage-1 screenshot.
 */

import type { ChatMessage, FlowEdge, MetricSample, PodRow } from './types';

export const FIXTURE_PODS: PodRow[] = [
  // sh-core
  { short: 'gateway',         full: 'gateway-68ddb457d-754vj',         namespace: 'sh-core',  anomaly: null,       anomaly_count: 0 },
  { short: 'auth',            full: 'auth-686d58bff8-zdllw',           namespace: 'sh-core',  anomaly: null,       anomaly_count: 0 },
  { short: 'booking',         full: 'booking-578946ddc-vqnck',         namespace: 'sh-core',  anomaly: null,       anomaly_count: 0 },
  { short: 'room',            full: 'room-74c4766bc5-lrcmx',           namespace: 'sh-core',  anomaly: 'warn',     anomaly_count: 1 },
  // sh-edge
  { short: 'hvac-controller', full: 'hvac-controller-cff74f845-czxng', namespace: 'sh-edge',  anomaly: 'critical', anomaly_count: 4 },
  { short: 'energy-meter',    full: 'energy-meter-d58f485cd-dc6wz',    namespace: 'sh-edge',  anomaly: null,       anomaly_count: 0 },
  { short: 'lock-controller', full: 'lock-controller-58859f654-wxr5n', namespace: 'sh-edge',  anomaly: null,       anomaly_count: 0 },
  { short: 'sensor-ingest',   full: 'sensor-ingest-555687cc47-z7tgm',  namespace: 'sh-edge',  anomaly: null,       anomaly_count: 0 },
  // sh-ops
  { short: 'billing',         full: 'billing-54cd54b995-cnk9n',        namespace: 'sh-ops',   anomaly: null,       anomaly_count: 0 },
  { short: 'notifications',   full: 'notifications-59dff8777b-vbfm5',  namespace: 'sh-ops',   anomaly: null,       anomaly_count: 0 },
];

export const FIXTURE_EDGES: FlowEdge[] = [
  // gateway fans out to the sh-core services
  { source: 'gateway',       target: 'auth',            count: 42 },
  { source: 'gateway',       target: 'booking',         count: 31 },
  { source: 'gateway',       target: 'room',            count: 17 },
  // room reaches the edge controllers
  { source: 'room',          target: 'hvac-controller', count: 24 },
  { source: 'room',          target: 'lock-controller', count: 12 },
  // edge controllers consume sensor-ingest
  { source: 'sensor-ingest', target: 'hvac-controller', count: 19 },
  { source: 'sensor-ingest', target: 'energy-meter',    count: 18 },
  // billing/notifications listen on booking
  { source: 'booking',       target: 'billing',         count: 14 },
  { source: 'booking',       target: 'notifications',   count: 9 },
];

/**
 * Build a believable CPU-rate time series for hvac-controller: ~120
 * samples of flat baseline at 0.001-0.003, a clean step up to ~0.20
 * for the last ~30 samples (the yes-loop window).
 */
export function fixtureMetrics(): MetricSample[] {
  const out: MetricSample[] = [];
  const now = Date.now();
  // 120 samples, 1s apart.
  for (let i = 0; i < 120; i++) {
    const ts = new Date(now - (120 - i) * 1000).toISOString();
    let v: number;
    if (i < 85) {
      // Quiet baseline: 0.001..0.003 with a tiny wobble.
      v = 0.0015 + Math.sin(i / 7) * 0.0008 + (Math.random() - 0.5) * 0.0005;
    } else if (i < 92) {
      // Ramp.
      v = 0.001 + ((i - 85) / 7) * 0.20;
    } else {
      // Sustained stress: ~0.20 with small wobble.
      v = 0.20 + (Math.random() - 0.5) * 0.012;
    }
    out.push({ ts, value: Math.max(0, v) });
  }
  return out;
}

export const FIXTURE_MESSAGES: ChatMessage[] = [
  {
    id: 'm-001',
    role: 'user',
    ts: new Date(Date.now() - 30_000).toISOString(),
    content: 'what is happening with hvac-controller?',
  },
  {
    id: 'm-002',
    role: 'assistant',
    ts: new Date(Date.now() - 25_000).toISOString(),
    tools_called: ['get_recent_anomalies', 'get_pod_metrics', 'get_pod_neighbors'],
    content:
      'hvac-controller is experiencing a sustained CPU spike — current rate is 0.199 cores against a baseline of 0.0015 (anomaly score 0.85, critical). The spike has held for about 30 seconds. Upstream, room is the only pod talking to it; sensor-ingest is also publishing to it. Recommend pausing the room → hvac call path or scaling the controller out while the investigation continues.',
  },
];
