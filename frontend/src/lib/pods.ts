/**
 * Pod-name utilities + the data derivations the dashboard performs
 * client-side. Tested-by-eyeball in the screenshots — same logic the
 * coordinator's get_pod_neighbors tool uses on the server side.
 */

import type { Finding, FlowRow, PodRow, Severity } from '../types';

// Kubernetes ReplicaSet pod-name suffix: -<rs-hash 7-10 hex>-<pod-hash 5 base36>.
// We strip this so all replicas roll up under one short name.
const POD_SUFFIX_RE = /-[0-9a-f]{7,10}-[0-9a-z]{5}$/;

export function shortName(full: string): string {
  return full.replace(POD_SUFFIX_RE, '');
}

/**
 * Namespaces the dashboard shows. Anything else gets dropped.
 *
 * `podmind` is intentionally excluded from the rendered graph + pod
 * list even though we still consume findings/flows about it on the
 * data layer — the dashboard's framing is SmartHostel observability,
 * not self-monitoring. cpu-agent and redis appearing as anomalous due
 * to the IF noise floor would muddy the demo narrative.
 */
export const DASHBOARD_NAMESPACES = new Set(['sh-core', 'sh-edge', 'sh-ops']);

export interface PodGraph {
  pods: PodRow[];
  edges: { source: string; target: string; count: number }[];
}

/**
 * Build the pod list + dependency graph from current flow rows and
 * findings. Half-flow pairing recovers neighbour identity under
 * socketLB, identical to the server-side rule.
 */
export function derivePodGraph(flows: FlowRow[], findings: Finding[]): PodGraph {
  // ----- Aggregate severity per short-name -----
  // Highest severity in the window wins.
  const severityRank = (s: Severity): number =>
    s === 'critical' ? 3 : s === 'warn' ? 2 : 1;

  const severityByShort = new Map<string, Severity>();
  const countByShort = new Map<string, number>();
  const namespaceByShort = new Map<string, string>();
  const fullByShort = new Map<string, string>();

  for (const f of findings) {
    const short = shortName(f.pod);
    countByShort.set(short, (countByShort.get(short) ?? 0) + 1);
    namespaceByShort.set(short, f.namespace);
    fullByShort.set(short, f.pod);
    const prev = severityByShort.get(short);
    if (!prev || severityRank(f.severity) > severityRank(prev)) {
      severityByShort.set(short, f.severity);
    }
  }

  // ----- Walk flows, also collecting pod identities + edges -----
  // We collect unique (src_port, dst_port) bucketed by half-flow type
  // so we can pair TO_STACK + TO_ENDPOINT halves and recover the real
  // neighbour even though socketLB strips one side of each row.

  type Half = { srcShort?: string; dstShort?: string; sp: number; dp: number };
  const toStack: Half[] = [];
  const toEndpoint: Half[] = [];
  // Edges seen with BOTH sides populated (direct pod-IP traffic).
  const directEdges = new Map<string, number>();

  function bumpEdge(src: string, dst: string) {
    if (!src || !dst || src === dst) return;
    const key = `${src}|${dst}`;
    directEdges.set(key, (directEdges.get(key) ?? 0) + 1);
  }

  function recordPod(full: string | null, namespace: string | null) {
    if (!full) return;
    const short = shortName(full);
    if (!namespaceByShort.has(short) && namespace) namespaceByShort.set(short, namespace);
    if (!fullByShort.has(short)) fullByShort.set(short, full);
  }

  for (const row of flows) {
    recordPod(row.src_pod, row.src_namespace);
    recordPod(row.dst_pod, row.dst_namespace);

    const srcShort = row.src_pod ? shortName(row.src_pod) : undefined;
    const dstShort = row.dst_pod ? shortName(row.dst_pod) : undefined;
    if (srcShort && dstShort) {
      bumpEdge(srcShort, dstShort);
      continue;
    }
    // One-sided rows: bucket for pairing.
    if (row.src_port == null || row.dst_port == null) continue;
    const h: Half = { srcShort, dstShort, sp: row.src_port, dp: row.dst_port };
    if (row.observation_point === 'TO_STACK') toStack.push(h);
    else if (row.observation_point === 'TO_ENDPOINT') toEndpoint.push(h);
  }

  // Pair half-flows by (src_port, dst_port).
  const pairedEdges = new Map<string, number>();
  // Index TO_ENDPOINT halves by their port pair for O(N) join.
  const epIdx = new Map<string, Half[]>();
  for (const h of toEndpoint) {
    const k = `${h.sp}|${h.dp}`;
    const arr = epIdx.get(k) ?? [];
    arr.push(h);
    epIdx.set(k, arr);
  }
  for (const ts of toStack) {
    if (!ts.srcShort) continue;
    const matches = epIdx.get(`${ts.sp}|${ts.dp}`);
    if (!matches) continue;
    for (const ep of matches) {
      if (!ep.dstShort) continue;
      if (ep.dstShort === ts.srcShort) continue;
      const key = `${ts.srcShort}|${ep.dstShort}`;
      pairedEdges.set(key, (pairedEdges.get(key) ?? 0) + 1);
    }
  }

  // ----- Compose PodRow[] -----
  const allShorts = new Set<string>([
    ...severityByShort.keys(),
    ...fullByShort.keys(),
  ]);

  const pods: PodRow[] = [];
  for (const short of allShorts) {
    const ns = namespaceByShort.get(short) ?? '';
    if (!DASHBOARD_NAMESPACES.has(ns)) continue;
    pods.push({
      short,
      full: fullByShort.get(short) ?? short,
      namespace: ns,
      anomaly: severityByShort.get(short) ?? null,
      anomaly_count: countByShort.get(short) ?? 0,
    });
  }

  // Sort: critical first, then warn, then alphabetical inside each
  // severity bucket. Keeps the eye drawn to anomalies on first paint.
  pods.sort((a, b) => {
    const ra = severityRank(a.anomaly ?? ('info' as Severity));
    const rb = severityRank(b.anomaly ?? ('info' as Severity));
    if (ra !== rb) return rb - ra;
    return a.short.localeCompare(b.short);
  });

  // ----- Compose edges -----
  // Merge direct + paired counts.
  const combinedEdges = new Map<string, number>(directEdges);
  for (const [k, v] of pairedEdges) {
    combinedEdges.set(k, (combinedEdges.get(k) ?? 0) + v);
  }
  // Restrict edges to pods we kept.
  const validShorts = new Set(pods.map((p) => p.short));
  const edges: { source: string; target: string; count: number }[] = [];
  for (const [k, count] of combinedEdges) {
    const [source, target] = k.split('|');
    if (!validShorts.has(source) || !validShorts.has(target)) continue;
    edges.push({ source, target, count });
  }

  return { pods, edges };
}

/**
 * Pick the default selected pod for first paint:
 *   1. critical pod with the highest anomaly_count
 *      (tiebreak: alphabetical) — so the camera lands on the
 *      MOST-stressed pod, not whichever critical happens to
 *      come first by name.
 *   2. else warn pod with the highest anomaly_count (same tiebreak)
 *   3. else first pod alphabetically in sh-core
 *   4. else first pod alphabetically anywhere
 *
 * User clicks override this until next page load (see App.tsx).
 */
export function defaultSelectedPod(pods: PodRow[]): string | null {
  if (pods.length === 0) return null;

  // anomaly_count desc, then short asc.
  const bySeverity = (sev: Severity) =>
    pods
      .filter((p) => p.anomaly === sev)
      .sort((a, b) => b.anomaly_count - a.anomaly_count || a.short.localeCompare(b.short));

  const crit = bySeverity('critical');
  if (crit.length > 0) return crit[0].short;
  const warn = bySeverity('warn');
  if (warn.length > 0) return warn[0].short;

  const shCore = pods
    .filter((p) => p.namespace === 'sh-core')
    .sort((a, b) => a.short.localeCompare(b.short));
  if (shCore.length > 0) return shCore[0].short;
  return [...pods].sort((a, b) => a.short.localeCompare(b.short))[0].short;
}
