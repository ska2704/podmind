"""Prometheus poller. Instant queries on a fixed cadence."""

import asyncio
import logging
import math
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import httpx
from podmind_contracts import MetricRecord

from .buffer import Buffer

log = logging.getLogger(__name__)


# Curated metric list. Each entry is a PromQL string, evaluated as an
# instant query. Heuristics:
#   - rate() everything that's a counter
#   - keep the per-tick cost bounded; one HTTP round-trip per entry
#   - cover what the agents in the brief actually need
DEFAULT_METRICS: tuple[str, ...] = (
    "rate(container_cpu_usage_seconds_total[30s])",
    "container_memory_working_set_bytes",
    "rate(container_cpu_cfs_throttled_seconds_total[30s])",
    "rate(container_fs_writes_bytes_total[30s])",
    "rate(container_fs_reads_bytes_total[30s])",
    "rate(container_network_transmit_bytes_total[30s])",
    "rate(container_network_receive_bytes_total[30s])",
    "rate(container_network_transmit_packets_dropped_total[30s])",
    "rate(node_netstat_Tcp_RetransSegs[30s])",
)


def parse_response(query: str, payload: dict[str, Any]) -> Iterable[MetricRecord]:
    """Yield MetricRecord rows from a Prom /api/v1/query response."""
    if payload.get("status") != "success":
        log.warning("prom query %r returned status %s", query, payload.get("status"))
        return

    data = payload.get("data") or {}
    if data.get("resultType") not in ("vector", "matrix"):
        return

    for entry in data.get("result", []):
        labels = dict(entry.get("metric", {}))
        # When the query is a bare metric, Prom returns __name__. When it's
        # an expression like rate(...), there's no __name__ — fall back to
        # the query string itself so the row is still identifiable.
        name = labels.pop("__name__", None) or query

        sample = entry.get("value")
        if not sample:
            continue
        try:
            ts = datetime.fromtimestamp(float(sample[0]), tz=UTC)
            value = float(sample[1])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            # Prom returns NaN / +Inf / -Inf as parseable floats; skip them
            # rather than poisoning downstream aggregations.
            continue

        pod = labels.pop("pod", None)
        namespace = labels.pop("namespace", None)
        container = labels.pop("container", None)

        yield MetricRecord(
            ts=ts,
            name=name,
            value=value,
            pod=pod,
            namespace=namespace,
            container=container,
            labels=labels,
        )


async def poll_once(
    client: httpx.AsyncClient,
    prom_url: str,
    metrics: Iterable[str],
) -> list[MetricRecord]:
    async def one(query: str) -> list[MetricRecord]:
        try:
            r = await client.get(
                f"{prom_url}/api/v1/query",
                params={"query": query},
                timeout=2.0,
            )
            r.raise_for_status()
            return list(parse_response(query, r.json()))
        except Exception as exc:
            log.warning("prom query %r failed: %s", query, exc)
            return []

    batches = await asyncio.gather(*(one(q) for q in metrics))
    return [r for batch in batches for r in batch]


async def poll_forever(
    buffer: Buffer,
    prom_url: str,
    interval_s: float,
    metrics: Iterable[str] | None = None,
) -> None:
    """Run the poll loop until cancelled. Each tick targets `interval_s`,
    drifting later if Prometheus is slow rather than queueing requests."""
    metrics = tuple(metrics or DEFAULT_METRICS)
    async with httpx.AsyncClient() as client:
        while True:
            t0 = asyncio.get_event_loop().time()
            try:
                records = await poll_once(client, prom_url, metrics)
                if records:
                    await buffer.insert_metrics(records)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("prom poll iteration failed")

            elapsed = asyncio.get_event_loop().time() - t0
            await asyncio.sleep(max(0.0, interval_s - elapsed))
