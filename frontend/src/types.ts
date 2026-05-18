/**
 * Shared types. Mirrors the backend contracts in
 * services/contracts/podmind_contracts so Stage-2 wiring is just
 * "plug the API responses in here".
 */

export type Severity = 'info' | 'warn' | 'critical';

export interface BaselineSummary {
  mean: number;
  stddev: number;
  sample_count: number;
}

export interface Finding {
  id: string;
  ts: string;
  agent_id: string;
  pod: string;
  namespace: string;
  metric_name: string;
  current_value: number;
  anomaly_score: number;
  severity: Severity;
  baseline_window_summary: BaselineSummary;
}

export interface MetricSample {
  ts: string;
  value: number;
}

/** A pod the dashboard knows about, identified by short (deployment) name. */
export interface PodRow {
  short: string;
  /** Latest observed full pod name (deployment-suffixed). */
  full: string;
  namespace: string;
  /**
   * Highest severity in the last 60s. `null` means healthy / no recent
   * findings, which is the demo's default state.
   */
  anomaly: Severity | null;
  /** Number of findings in the last 60s. 0 when healthy. */
  anomaly_count: number;
}

/** A directed edge in the dependency graph, aggregated over the lookback. */
export interface FlowEdge {
  /** Short pod name (deployment prefix). */
  source: string;
  /** Short pod name. */
  target: string;
  /** Number of flow rows in the lookback window. */
  count: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  ts: string;
  /** Tool names invoked by the coordinator for assistant messages. */
  tools_called?: string[];
  /** Optional thinking indicator (for the in-flight assistant turn). */
  pending?: boolean;
}

// --------------------------------------------------------------------
// Wire types that mirror backend responses.
//
// Only the fields the dashboard actually reads are listed — keep this
// narrow so when the backend grows fields we don't fight TypeScript.
// --------------------------------------------------------------------

/** Row from `/buffer/metrics`. */
export interface MetricRow {
  ts: string;
  name: string;
  value: number;
  pod: string | null;
  namespace: string | null;
}

/** Row from `/buffer/flows`. */
export interface FlowRow {
  ts: string;
  verdict: string;
  src_pod: string | null;
  src_namespace: string | null;
  dst_pod: string | null;
  dst_namespace: string | null;
  l4_protocol: string | null;
  src_port: number | null;
  dst_port: number | null;
  observation_point:
    | 'TO_STACK'
    | 'TO_ENDPOINT'
    | 'TO_PROXY'
    | 'TO_HOST'
    | 'TO_OVERLAY'
    | 'FROM_ENDPOINT'
    | 'FROM_PROXY'
    | 'FROM_HOST'
    | 'FROM_STACK'
    | 'FROM_OVERLAY'
    | 'FROM_NETWORK'
    | 'TO_NETWORK'
    | 'FROM_CRYPTO'
    | 'TO_CRYPTO'
    | 'UNKNOWN_POINT'
    | null;
}

/** Coordinator `/ask` response. */
export interface AskResponse {
  answer: string;
  tools_called: { name: string; arguments: Record<string, unknown> }[];
}
