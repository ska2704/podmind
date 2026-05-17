"""Polls the ingestor's /buffer/metrics endpoint for the CPU metric,
dispatches samples into per-pod windows, scores the latest sample,
and publishes a Finding if the anomaly score crosses the threshold.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from podmind_contracts import Finding

from .config import Config
from .detector import PodDetector, severity_from_score
from .publisher import Publisher
from .window import WindowStore

log = logging.getLogger(__name__)


class Poller:
    """Owns the WindowStore + per-pod PodDetector map + ingestor client.

    Run forever via `run_forever`; for tests, drive single ticks with
    `tick_once` against an injected httpx.AsyncClient.
    """

    def __init__(self, config: Config, publisher: Publisher):
        self.config = config
        self.publisher = publisher
        self.windows = WindowStore(window_s=config.window_s)
        self.detectors: dict[tuple[str, str], PodDetector] = {}
        self._last_query_ts: datetime | None = None

    def _detector_for(self, namespace: str, pod: str) -> PodDetector:
        key = (namespace, pod)
        d = self.detectors.get(key)
        if d is None:
            d = PodDetector(
                min_samples=self.config.min_samples,
                refit_interval_s=self.config.refit_interval_s,
            )
            self.detectors[key] = d
        return d

    async def _fetch_samples(self, client: httpx.AsyncClient) -> list[dict]:
        """Pull the most recent CPU samples from the ingestor.

        Each tick asks for the window since the previous tick (minus a
        couple of seconds of jitter slack) so we don't double-count or
        miss rows when the poll interval doesn't divide evenly into
        Prom scrape intervals.
        """
        if self._last_query_ts is None:
            # Cold start — bootstrap with the full WINDOW_S so we can
            # start fitting quickly rather than waiting MIN_SAMPLES
            # ticks to accumulate from cold.
            since = f"-{self.config.window_s}s"
        else:
            elapsed = (datetime.now(UTC) - self._last_query_ts).total_seconds()
            since = f"-{int(elapsed) + 2}s"

        self._last_query_ts = datetime.now(UTC)
        r = await client.get(
            f"{self.config.ingestor_url}/buffer/metrics",
            params={"since": since, "name": self.config.cpu_metric_query},
            timeout=5.0,
        )
        r.raise_for_status()
        return r.json().get("rows", [])

    async def tick_once(self, client: httpx.AsyncClient) -> list[Finding]:
        """One poll → score → publish cycle. Returns the Findings that
        were published in this tick (for tests / debug)."""
        rows = await self._fetch_samples(client)
        log.debug("tick: %d rows", len(rows))

        # Each row is a MetricRecord JSON dump. Dispatch into windows.
        for row in rows:
            pod = row.get("pod")
            namespace = row.get("namespace")
            ts_raw = row.get("ts")
            value = row.get("value")
            if not pod or not namespace or ts_raw is None or value is None:
                continue
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                v = float(value)
            except (TypeError, ValueError):
                continue
            self.windows.add_sample(namespace, pod, ts, v)

        # Score the latest sample for every pod with a fresh-enough window.
        # Publishing one Finding per pod per tick at most — even if the
        # window is "all anomaly" we don't want to flood.
        published: list[Finding] = []
        now = datetime.now(UTC)
        for (namespace, pod), w in self.windows.items():
            if not w.samples:
                continue
            detector = self._detector_for(namespace, pod)
            result = detector.score(w.values())
            if result is None:
                continue
            if result.anomaly_score < self.config.anomaly_threshold:
                continue
            severity = severity_from_score(result.anomaly_score)
            if severity == "info":
                # Conservative: only publish at warn or above. info-grade
                # signals stay in the log.
                log.debug(
                    "info-only signal: pod=%s score=%.3f", pod, result.anomaly_score
                )
                continue

            finding = Finding(
                id=f"{self.config.agent_id}:{namespace}/{pod}:{w.samples[-1][0].isoformat()}",
                ts=w.samples[-1][0],
                agent_id=self.config.agent_id,
                pod=pod,
                namespace=namespace,
                metric_name="cpu_rate",
                current_value=result.current_value,
                anomaly_score=result.anomaly_score,
                severity=severity,  # type: ignore[arg-type]
                baseline_window_summary=result.baseline,
            )
            await self.publisher.publish(finding)
            published.append(finding)

        # Garbage-collect stale pods, both from the window store and the
        # detector map (they're keyed identically).
        evicted = self.windows.evict_stale(now)
        if evicted:
            for key in list(self.detectors.keys()):
                if self.windows.get(*key) is None:
                    del self.detectors[key]

        return published

    async def run_forever(self) -> None:
        async with httpx.AsyncClient() as client:
            while True:
                t0 = asyncio.get_event_loop().time()
                try:
                    await self.tick_once(client)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("cpu-agent tick failed")
                elapsed = asyncio.get_event_loop().time() - t0
                await asyncio.sleep(max(0.0, self.config.poll_interval_s - elapsed))
