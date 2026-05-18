/**
 * Three-column dashboard wired to the live backend.
 *
 * Data flow:
 *   - getFlows           polled every 3s   → derives pods + dependency graph
 *   - getRecentFindings  polled every 2s   → drives anomaly badges + node colours
 *   - getMetrics         polled every 5s   → footer time-series for the selected pod
 *   - askCoordinator     on user submit    → assistant message in the chat
 *
 * Selection rules:
 *   - On first paint, defaultSelectedPod() picks the first critical
 *     pod, else first warn pod, else first alphabetical in sh-core.
 *   - User clicks override that until next page load.
 */

import { useMutation, useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { askCoordinator, getFlows, getMetrics, getRecentFindings } from './api/client';
import { ChatPanel } from './components/ChatPanel';
import { DependencyGraph } from './components/DependencyGraph';
import { PodList } from './components/PodList';
import { Bar as SkeletonBar, List as SkeletonList } from './components/Skeleton';
import { TimeSeriesChart } from './components/TimeSeriesChart';
import { defaultSelectedPod, derivePodGraph, shortName } from './lib/pods';
import type { ChatMessage } from './types';

const METRIC_NAME = 'rate(container_cpu_usage_seconds_total[30s])';
// Typewriter pacing: clamp total wall time between MIN and MAX. The
// model's answers run 200-1500 chars; at 30 cps the long ones would
// stream for 50s, which is interminable on camera. MAX_MS=6000 caps
// the pacing so it never feels slow, and MIN_MS=2500 stops short
// answers from rendering instantly.
const TYPEWRITER_MIN_MS = 2500;
const TYPEWRITER_MAX_MS = 6000;

export default function App() {
  // ----- queries -----
  const flowsQ = useQuery({
    queryKey: ['flows'],
    queryFn: () => getFlows({ sinceS: 120 }),
    refetchInterval: 3_000,
    placeholderData: (prev) => prev,
  });
  const findingsQ = useQuery({
    queryKey: ['findings'],
    queryFn: () => getRecentFindings({ sinceS: 60 }),
    refetchInterval: 2_000,
    placeholderData: (prev) => prev,
  });

  // ----- derived dashboard state -----
  const { pods, edges } = useMemo(
    () => derivePodGraph(flowsQ.data ?? [], findingsQ.data ?? []),
    [flowsQ.data, findingsQ.data],
  );

  // ----- selection -----
  // ?pod=NAME in the URL pre-selects a pod (used by the screenshot
  // capture in docs/design-day3-step2-c.png). A real user click sets
  // userPickedSelection, after which the default heuristic stops
  // overwriting their choice.
  const initialFromUrl =
    typeof window !== 'undefined'
      ? new URLSearchParams(window.location.search).get('pod')
      : null;
  const [selectedShort, setSelectedShort] = useState<string | null>(initialFromUrl);
  const [userPickedSelection, setUserPickedSelection] = useState(Boolean(initialFromUrl));
  useEffect(() => {
    if (userPickedSelection) return;
    const pick = defaultSelectedPod(pods);
    if (pick && pick !== selectedShort) setSelectedShort(pick);
  }, [pods, userPickedSelection, selectedShort]);

  // Stable identity so the graph's effect deps don't churn.
  const chooseSelected = useCallback((short: string | null) => {
    setUserPickedSelection(true);
    if (short) setSelectedShort(short);
  }, []);

  // ----- metrics for the selected pod -----
  // The ingestor's /buffer/metrics `pod` filter is an exact match on the
  // full pod name (e.g. "billing-54cd54b995-82c8c"), but the dashboard
  // talks in short names. Easiest is to NOT pass pod to the server and
  // narrow client-side by short-name match.
  const metricsQ = useQuery({
    queryKey: ['metrics-all-pods'],
    queryFn: () => getMetrics({ sinceS: 180, metric: METRIC_NAME }),
    refetchInterval: 5_000,
    placeholderData: (prev) => prev,
  });

  const series = useMemo(() => {
    if (!metricsQ.data || !selectedShort) return [];
    // Exact short-name match (not startsWith), so a query for "auth"
    // doesn't grab samples for hypothetical "auth-server".
    const rows = metricsQ.data
      .filter((r) => r.pod != null && shortName(r.pod) === selectedShort)
      .map((r) => ({ ts: r.ts, value: r.value }));
    rows.sort((a, b) => a.ts.localeCompare(b.ts));
    return rows.slice(-180);
  }, [metricsQ.data, selectedShort]);

  // ----- chat + typewriter -----
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const askMut = useMutation({ mutationFn: askCoordinator });

  // The typewriter renders `full.slice(0, shown)` for the in-flight
  // assistant message. shown increments on a setTimeout; the per-char
  // delay is computed so total wall time = max(MIN_MS, len/CPS*1000).
  const [typewriter, setTypewriter] = useState<{
    id: string;
    full: string;
    shown: number;
    msPerChar: number;
  } | null>(null);

  useEffect(() => {
    if (!typewriter) return;
    if (typewriter.shown >= typewriter.full.length) return;
    // Capture the timer ID in the closure — using a shared ref bites
    // here because the cleanup of effect-N runs AFTER the timer of
    // effect-N has already fired and effect-(N+1) has assigned a new
    // ID to the ref. Clearing the ref then clears the NEW timer.
    const id = window.setTimeout(() => {
      setTypewriter((t) =>
        t ? { ...t, shown: Math.min(t.shown + 1, t.full.length) } : null,
      );
    }, typewriter.msPerChar);
    return () => window.clearTimeout(id);
  }, [typewriter]);

  // Screenshot capture only. Injects a coordinator response into chat
  // state without going through /ask. Never wired into production user
  // flows; used by docs/design-day3-step2-d capture script and similar.
  //
  // Payload format: base64(JSON.stringify({ question, answer, tools_called? }))
  const prefillRef = useRef(false);
  useEffect(() => {
    if (prefillRef.current) return;
    if (typeof window === 'undefined') return;
    const raw = new URLSearchParams(window.location.search).get('demo_response');
    if (!raw) return;
    prefillRef.current = true;
    try {
      const decoded = JSON.parse(atob(raw)) as {
        question: string;
        answer: string;
        tools_called?: string[];
      };
      const tNow = new Date().toISOString();
      setMessages((prev) => [
        ...prev,
        { id: `u-prefill`, role: 'user', content: decoded.question, ts: tNow },
        {
          id: `a-prefill`,
          role: 'assistant',
          content: decoded.answer,
          ts: tNow,
          tools_called: decoded.tools_called ?? [
            'get_recent_anomalies',
            'get_pod_metrics',
            'get_pod_neighbors',
          ],
        },
      ]);
    } catch (err) {
      console.warn('failed to decode prefill', err);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleSend(question: string) {
    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: question,
      ts: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    askMut.mutate(
      { question },
      {
        onSuccess: (resp) => {
          const id = `a-${Date.now()}`;
          const total = Math.min(
            TYPEWRITER_MAX_MS,
            Math.max(TYPEWRITER_MIN_MS, resp.answer.length * 22),
          );
          const msPerChar = Math.max(2, total / Math.max(1, resp.answer.length));
          const assistant: ChatMessage = {
            id,
            role: 'assistant',
            content: resp.answer,
            ts: new Date().toISOString(),
            tools_called: resp.tools_called.map((tc) => tc.name),
          };
          setMessages((prev) => [...prev, assistant]);
          setTypewriter({ id, full: resp.answer, shown: 0, msPerChar });
        },
        onError: (err) => {
          const id = `a-${Date.now()}`;
          const text = `error: ${err instanceof Error ? err.message : String(err)}`;
          setMessages((prev) => [
            ...prev,
            { id, role: 'assistant', content: text, ts: new Date().toISOString() },
          ]);
          // No typewriter for errors — render the message at once.
          setTypewriter(null);
        },
      },
    );
  }

  // Patch the in-flight assistant message in place so the typewriter's
  // visible content lags the stored full content.
  const displayedMessages = useMemo(() => {
    if (!typewriter) return messages;
    return messages.map((m) =>
      m.id === typewriter.id
        ? { ...m, content: typewriter.full.slice(0, typewriter.shown) }
        : m,
    );
  }, [messages, typewriter]);

  const firstPaintLoading = flowsQ.isLoading && findingsQ.isLoading;
  const liveStatusOk = !flowsQ.isError && !findingsQ.isError;

  return (
    <div className="h-full flex flex-col bg-page text-ink-900">
      <Header live={liveStatusOk} />

      <main className="flex-1 min-h-0 p-6">
        <div
          className="h-full grid gap-6"
          style={{
            gridTemplateColumns: '280px 1fr 360px',
            gridTemplateRows: '1fr 200px',
          }}
        >
          <div className="min-h-0" style={{ gridRow: '1' }}>
            {firstPaintLoading ? (
              <SkeletonCard title="Pods">
                <SkeletonList rows={8} rowHeight={40} />
              </SkeletonCard>
            ) : (
              <PodList
                pods={pods}
                selectedShort={selectedShort}
                onSelect={chooseSelected}
              />
            )}
          </div>

          <div className="min-h-0" style={{ gridRow: '1' }}>
            {firstPaintLoading ? (
              <SkeletonCard title="Dependency Graph">
                <div className="h-full w-full flex items-center justify-center">
                  <SkeletonBar w={320} h={20} />
                </div>
              </SkeletonCard>
            ) : (
              <DependencyGraph
                pods={pods}
                edges={edges}
                selectedShort={selectedShort}
                onSelect={chooseSelected}
              />
            )}
          </div>

          <div className="min-h-0" style={{ gridRow: '1' }}>
            <ChatPanel
              messages={displayedMessages}
              onSend={handleSend}
              isSending={askMut.isPending}
            />
          </div>

          <div className="min-h-0" style={{ gridColumn: '1 / -1', gridRow: '2' }}>
            <TimeSeriesChart podShort={selectedShort} samples={series} />
          </div>
        </div>
      </main>
    </div>
  );
}

function Header({ live }: { live: boolean }) {
  return (
    <header className="px-6 py-4 border-b border-border-subtle bg-page flex items-baseline gap-3">
      <div className="text-[15px] font-medium tracking-tight">PodMind</div>
      <div className="text-[12px] font-mono text-ink-500">cluster: k3d-podmind</div>
      <div className="flex-1" />
      <LiveStatus live={live} />
    </header>
  );
}

function LiveStatus({ live }: { live: boolean }) {
  return (
    <div className="flex items-center gap-1.5">
      <span
        aria-hidden
        className="inline-block h-2 w-2 rounded-full"
        style={{ background: live ? '#10B981' : '#A3A3A3' }}
      />
      <span className="text-[13px] font-medium text-ink-700">
        {live ? 'Live' : 'Offline'}
      </span>
    </div>
  );
}

function SkeletonCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card h-full flex flex-col overflow-hidden">
      <div className="px-4 py-3 border-b border-border-subtle">
        <div className="text-[10px] font-mono uppercase tracking-widest text-ink-400">
          {title}
        </div>
      </div>
      <div className="flex-1 min-h-0 p-4">{children}</div>
    </div>
  );
}
