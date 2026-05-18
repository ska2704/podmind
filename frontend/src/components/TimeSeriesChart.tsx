/**
 * Footer time-series — CPU rate over time for the selected pod.
 *
 * Recharts isn't perfectly opinionated about styling out of the box;
 * the props below are tuned so the chart reads as "engineered" rather
 * than "playful": muted indigo line, faint area fill, mono tick labels,
 * grid in ink-300 at 40% opacity.
 */
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { MetricSample } from '../types';

interface Props {
  podShort: string | null;
  samples: MetricSample[];
}

const INDIGO = '#6366F1';
const INDIGO_FILL = '#6366F1';

export function TimeSeriesChart({ podShort, samples }: Props) {
  const data = samples.map((s) => ({
    ts: s.ts,
    t: shortTime(s.ts),
    value: s.value,
  }));

  const latest = data.length > 0 ? data[data.length - 1].value : null;
  const peak = data.length > 0 ? Math.max(...data.map((d) => d.value)) : null;

  return (
    <div className="card h-full flex flex-col overflow-hidden">
      <div className="px-4 py-3 border-b border-border-subtle flex items-baseline gap-3">
        <div className="text-[10px] font-mono uppercase tracking-widest text-ink-400">
          Time series
        </div>
        <div className="font-mono text-[12px] text-ink-700">
          {podShort ?? '—'}
        </div>
        <div className="font-mono text-[11px] text-ink-500">
          rate(container_cpu_usage_seconds_total[30s])
        </div>
        <div className="ml-auto flex items-center gap-4">
          <Stat label="current" value={latest} />
          <Stat label="peak" value={peak} />
          <Stat label="samples" value={data.length} integer />
        </div>
      </div>

      <div className="flex-1 min-h-0 px-2 py-2">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={data}
            margin={{ top: 8, right: 24, left: 12, bottom: 8 }}
          >
            <defs>
              <linearGradient id="ts-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={INDIGO_FILL} stopOpacity={0.22} />
                <stop offset="100%" stopColor={INDIGO_FILL} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#D4D4D4" strokeOpacity={0.4} vertical={false} />
            <XAxis
              dataKey="t"
              interval="preserveStartEnd"
              minTickGap={48}
              tickLine={false}
              axisLine={{ stroke: '#D4D4D4' }}
              tick={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 10, fill: '#7A7A7A' }}
            />
            <YAxis
              // 2 decimals is enough for what this chart communicates.
              // Width tightened to match the shorter ticks.
              tickFormatter={(v) => v.toFixed(2)}
              tickLine={false}
              axisLine={false}
              width={40}
              tick={{ fontFamily: '"JetBrains Mono", monospace', fontSize: 10, fill: '#7A7A7A' }}
            />
            <Tooltip content={<CustomTooltip />} />
            <Area
              type="monotone"
              dataKey="value"
              stroke="none"
              fill="url(#ts-fill)"
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="value"
              stroke={INDIGO}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  integer,
}: {
  label: string;
  value: number | null;
  integer?: boolean;
}) {
  // Mixed type: label is UI text (Inter, ink-500), value is data
  // (JetBrains Mono, ink-900). Reads "instrument-like" at a glance.
  const rendered = value == null ? '—' : integer ? value.toFixed(0) : value.toFixed(4);
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span className="text-[12px] text-ink-500">{label}</span>
      <span className="font-mono text-[12px] text-ink-900">{rendered}</span>
    </span>
  );
}

function CustomTooltip(props: {
  active?: boolean;
  payload?: { value: number; payload: { ts: string; t: string } }[];
}) {
  if (!props.active || !props.payload || props.payload.length === 0) return null;
  const p = props.payload[0];
  return (
    <div className="rounded-md bg-card border border-border-subtle shadow-card px-2.5 py-1.5">
      <div className="font-mono text-[10px] text-ink-400">{p.payload.t}</div>
      <div className="font-mono text-[12px] text-ink-900">{p.value.toFixed(4)}</div>
    </div>
  );
}

function shortTime(ts: string): string {
  const d = new Date(ts);
  return d.toTimeString().slice(0, 8);
}
