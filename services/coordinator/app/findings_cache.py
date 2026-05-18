"""In-process ring buffer of recent Findings, fed by a Redis subscriber.

The subscriber runs as a long-lived asyncio task spawned in the FastAPI
lifespan. Each pub/sub message is parsed back into a `Finding` and
appended; old entries fall off when the deque is full.

Race-free reads: the deque itself is thread-safe for append/iterate
under CPython's GIL, and we never mutate items in place — readers
always get a consistent snapshot via `list(self._buffer)`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import UTC, datetime, timedelta

from podmind_contracts import Finding
from redis.asyncio import Redis

log = logging.getLogger(__name__)


class FindingsCache:
    def __init__(self, size: int):
        self._buffer: deque[Finding] = deque(maxlen=size)

    def __len__(self) -> int:
        return len(self._buffer)

    def add(self, finding: Finding) -> None:
        self._buffer.append(finding)

    def get_recent(
        self,
        *,
        pod_substring: str | None = None,
        since_s: int = 300,
    ) -> list[Finding]:
        """Snapshot of recent findings, newest last. Optionally filter
        by pod-name substring (matches the random deployment suffix
        problem) and by a max age."""
        cutoff = datetime.now(UTC) - timedelta(seconds=since_s)
        snap = list(self._buffer)
        out: list[Finding] = []
        for f in snap:
            if f.ts < cutoff:
                continue
            if pod_substring and pod_substring not in f.pod:
                continue
            out.append(f)
        return out


async def run_subscriber(
    redis: Redis,
    channel: str,
    cache: FindingsCache,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Subscribe to `channel` and append every parsed Finding to `cache`.

    Runs until cancelled or `stop_event` is set. Reconnects on transient
    Redis errors with a small backoff so we don't tight-loop if Redis
    is briefly unavailable.
    """
    backoff = 1.0
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        try:
            pubsub = redis.pubsub()
            await pubsub.subscribe(channel)
            log.info("findings_cache: subscribed to %s", channel)
            backoff = 1.0
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode()
                try:
                    finding = Finding.model_validate_json(data)
                except Exception:
                    log.warning("findings_cache: dropped malformed payload: %r", data[:200])
                    continue
                cache.add(finding)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("findings_cache: subscriber loop crashed, reconnecting")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
