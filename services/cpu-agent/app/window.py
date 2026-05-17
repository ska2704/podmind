"""Per-pod rolling sample windows.

One `PodWindow` per (namespace, pod) pair, holding (ts, value) tuples
up to `window_s` seconds wide. A `WindowStore` owns the dict of windows
and handles eviction of pods that haven't been seen recently.

No global window — pods are independent. CPU baselines vary wildly
across pod types and a global model would either spam findings on
busy services or miss spikes on quiet ones.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class PodWindow:
    """Rolling (ts, value) deque for a single pod, bounded by window_s.

    Old samples drop off the front each time we add a new one or call
    `prune`. `last_seen` is updated on every `add`; the store uses it
    to evict pods that have gone quiet.
    """

    namespace: str
    pod: str
    window_s: int
    samples: deque[tuple[datetime, float]] = field(default_factory=deque)
    last_seen: datetime | None = None

    def add(self, ts: datetime, value: float) -> None:
        self.samples.append((ts, value))
        self.last_seen = ts
        self.prune(now=ts)

    def prune(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.window_s)
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()

    def values(self) -> list[float]:
        return [v for _, v in self.samples]

    def __len__(self) -> int:
        return len(self.samples)


class WindowStore:
    """Dict of PodWindow keyed by (namespace, pod) with eviction.

    Pods unseen for `2 * window_s` are dropped — long enough that a
    transient scrape gap doesn't wipe state, short enough that pods
    that have actually gone away don't accumulate forever.
    """

    def __init__(self, window_s: int):
        self.window_s = window_s
        self._windows: dict[tuple[str, str], PodWindow] = {}

    def add_sample(self, namespace: str, pod: str, ts: datetime, value: float) -> PodWindow:
        key = (namespace, pod)
        w = self._windows.get(key)
        if w is None:
            w = PodWindow(namespace=namespace, pod=pod, window_s=self.window_s)
            self._windows[key] = w
        w.add(ts, value)
        return w

    def get(self, namespace: str, pod: str) -> PodWindow | None:
        return self._windows.get((namespace, pod))

    def evict_stale(self, now: datetime) -> int:
        """Drop pods unseen for more than 2 * window_s. Returns count."""
        cutoff = now - timedelta(seconds=2 * self.window_s)
        stale = [k for k, w in self._windows.items() if w.last_seen is None or w.last_seen < cutoff]
        for k in stale:
            del self._windows[k]
        return len(stale)

    def items(self):
        return self._windows.items()

    def __len__(self) -> int:
        return len(self._windows)
