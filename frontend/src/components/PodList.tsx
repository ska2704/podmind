/**
 * Sidebar pod list, grouped by namespace.
 *
 * Selection state lives in the parent (App owns the dashboard's
 * currently-focused pod). Hover and selected backgrounds are tuned
 * so the camera doesn't smear them together — selected is a clear
 * blue tint with a left accent rail; hover is a subtle warm grey.
 */
import type { PodRow, Severity } from '../types';

interface Props {
  pods: PodRow[];
  selectedShort: string | null;
  onSelect(short: string): void;
}

export function PodList({ pods, selectedShort, onSelect }: Props) {
  // Group by namespace, preserving the order pods arrive in.
  const groups = new Map<string, PodRow[]>();
  for (const p of pods) {
    const arr = groups.get(p.namespace) ?? [];
    arr.push(p);
    groups.set(p.namespace, arr);
  }
  const anomalyCount = pods.filter((p) => p.anomaly !== null).length;
  // Highest-severity drives the aggregate indicator's colour: red if
  // any critical, amber if only warns, nothing if healthy. Keeps the
  // header in lockstep with the rule "absence of colour = health".
  const aggregateSeverity: Severity | null = pods.some((p) => p.anomaly === 'critical')
    ? 'critical'
    : pods.some((p) => p.anomaly === 'warn')
      ? 'warn'
      : null;

  return (
    <div className="card h-full flex flex-col overflow-hidden">
      <Header total={pods.length} anomalous={anomalyCount} severity={aggregateSeverity} />
      <div className="flex-1 min-h-0 overflow-y-auto">
        {[...groups.entries()].map(([ns, items]) => (
          <section key={ns} className="pb-1">
            <div className="px-4 pt-3 pb-1 text-[10px] font-mono uppercase tracking-widest text-ink-400">
              {ns}
            </div>
            <ul>
              {items.map((p) => (
                <li key={p.short}>
                  <PodRowItem
                    pod={p}
                    selected={selectedShort === p.short}
                    onSelect={() => onSelect(p.short)}
                  />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}

function Header({
  total,
  anomalous,
  severity,
}: {
  total: number;
  anomalous: number;
  severity: Severity | null;
}) {
  const color = severity === 'critical' ? '#E5484D' : severity === 'warn' ? '#F59E0B' : null;
  return (
    <div className="px-4 py-3 border-b border-border-subtle flex items-baseline gap-2">
      <div className="text-[10px] font-mono uppercase tracking-widest text-ink-400">Pods</div>
      <div className="font-mono text-[12px] text-ink-700">{total}</div>
      {anomalous > 0 && color && (
        <div className="ml-auto flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
          <span className="font-mono text-[11px]" style={{ color }}>
            {anomalous} anomalous
          </span>
        </div>
      )}
    </div>
  );
}

function PodRowItem({
  pod,
  selected,
  onSelect,
}: {
  pod: PodRow;
  selected: boolean;
  onSelect(): void;
}) {
  const base = 'group relative w-full text-left px-4 flex items-center';
  const selectedClasses = selected
    ? 'bg-[#EEF4FF]'
    : 'hover:bg-[#F4F4F4]';
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`${base} ${selectedClasses}`}
      style={{ height: 48 }}
    >
      {/* Left accent rail for the selected row */}
      {selected && (
        <span
          aria-hidden
          className="absolute left-0 top-2 bottom-2 w-[3px] rounded-r-sm"
          style={{ background: '#3B82F6' /* blue-500 */ }}
        />
      )}
      <span
        className={`text-[13px] font-medium truncate ${
          selected ? 'text-ink-900' : 'text-ink-700'
        }`}
      >
        {pod.short}
      </span>
      <span className="ml-auto flex items-center gap-2">
        {pod.anomaly && <AnomalyBadge severity={pod.anomaly} count={pod.anomaly_count} />}
      </span>
    </button>
  );
}

function AnomalyBadge({ severity, count }: { severity: Severity; count: number }) {
  // Warn = amber, critical = red. The critical glow ring is the only
  // emphasis we apply on top — colour already communicates severity.
  const critical = severity === 'critical';
  const bg = critical ? '#E5484D' : '#F59E0B';
  return (
    <span
      className={`pill text-white ${critical ? 'shadow-glow-red' : ''}`}
      style={{
        background: bg,
        height: 18,
        paddingInline: 6,
      }}
      title={`${severity} (${count} in last 60s)`}
    >
      {count}
    </span>
  );
}
