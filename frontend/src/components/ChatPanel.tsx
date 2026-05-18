/**
 * Right-column chat panel. Pure presentation — the parent owns the
 * message list, current input value, and the send handler. Pending
 * assistant turns are rendered as a typing-indicator bubble.
 */
import { useEffect, useRef, useState } from 'react';
import type { ChatMessage } from '../types';

interface Props {
  messages: ChatMessage[];
  onSend(question: string): void;
  isSending: boolean;
}

export function ChatPanel({ messages, onSend, isSending }: Props) {
  const [draft, setDraft] = useState('');
  const scrollerRef = useRef<HTMLDivElement | null>(null);

  // Autoscroll to bottom on new messages.
  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, isSending]);

  function submit() {
    const q = draft.trim();
    if (!q || isSending) return;
    onSend(q);
    setDraft('');
  }

  return (
    <div className="card h-full flex flex-col overflow-hidden">
      <div className="px-4 py-3 border-b border-border-subtle flex items-baseline gap-2">
        <div className="text-[10px] font-mono uppercase tracking-widest text-ink-400">Ask</div>
        <div className="font-mono text-[11px] text-ink-500">/ask · coordinator</div>
      </div>

      <div ref={scrollerRef} className="flex-1 min-h-0 overflow-y-auto px-4 py-4 space-y-4">
        {messages.map((m) => (
          <Bubble key={m.id} m={m} />
        ))}
        {isSending && <PendingBubble />}
      </div>

      <div className="border-t border-border-subtle p-3">
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder="Ask about a pod or service..."
            rows={1}
            className="flex-1 resize-none rounded-md border border-border-subtle bg-page px-3 py-2 text-[13px] text-ink-900 placeholder:text-ink-400 focus:outline-none focus:border-ink-400 transition-colors"
          />
          <button
            type="button"
            onClick={submit}
            disabled={isSending || !draft.trim()}
            className="rounded-md bg-ink-900 px-3 py-2 text-[13px] font-medium text-white disabled:bg-ink-300 disabled:cursor-not-allowed transition-colors"
          >
            Ask
          </button>
        </div>
      </div>
    </div>
  );
}

function Bubble({ m }: { m: ChatMessage }) {
  const isUser = m.role === 'user';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-[88%] flex flex-col ${isUser ? 'items-end' : 'items-start'}`}>
        <div
          className={
            isUser
              ? 'rounded-2xl px-3.5 py-2.5 bg-[#F1F2F4] text-ink-900 text-[13px] leading-relaxed'
              : 'rounded-2xl px-3.5 py-2.5 bg-card border border-border-subtle text-ink-900 text-[13px] leading-relaxed'
          }
        >
          {m.content}
        </div>
        <div className="mt-1 flex items-center gap-2">
          <span className="font-mono text-[10px] text-ink-400">{shortTime(m.ts)}</span>
          {m.tools_called && m.tools_called.length > 0 && (
            <span className="font-mono text-[10px] text-ink-400">
              · {m.tools_called.length} tools
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function PendingBubble() {
  return (
    <div className="flex justify-start">
      <div className="max-w-[88%] flex flex-col items-start">
        <div className="rounded-2xl px-3.5 py-2.5 bg-card border border-border-subtle flex gap-1 items-center">
          <Dot delay="0ms" />
          <Dot delay="160ms" />
          <Dot delay="320ms" />
        </div>
        <div className="mt-1 flex items-center gap-2">
          <span className="font-mono text-[10px] text-ink-400">thinking…</span>
        </div>
      </div>
    </div>
  );
}

function Dot({ delay }: { delay: string }) {
  return (
    <span
      className="inline-block h-1.5 w-1.5 rounded-full bg-ink-400"
      style={{
        animation: 'pm-pulse 1.1s ease-in-out infinite',
        animationDelay: delay,
      }}
    />
  );
}

function shortTime(ts: string): string {
  const d = new Date(ts);
  // HH:MM:SS in local time. Kept short — timestamps shouldn't dominate
  // the visual hierarchy of a bubble.
  return d.toTimeString().slice(0, 8);
}
