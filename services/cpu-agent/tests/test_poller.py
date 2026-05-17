"""Poller wires the ingestor HTTP client → WindowStore → detector → publisher.

We mock the ingestor with respx so the tests don't need a live service.
We use the same FakeRedis from test_publisher for the publisher leg.
"""

import random
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from podmind_contracts import Finding

from app.config import Config
from app.poller import Poller
from app.publisher import Publisher


class FakeRedis:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.calls.append((channel, message))
        return 1


def _make_config(**overrides) -> Config:
    defaults = dict(
        ingestor_url="http://ingestor.test",
        redis_url="redis://test:6379/0",
        poll_interval_s=5.0,
        window_s=300,
        min_samples=30,
        refit_interval_s=30.0,
        anomaly_threshold=0.5,
        cpu_metric_query="rate(container_cpu_usage_seconds_total[30s])",
        findings_channel="findings.cpu",
        agent_id="cpu-agent",
    )
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def _row(*, pod: str, namespace: str, ts: datetime, value: float) -> dict:
    return {
        "ts": ts.isoformat().replace("+00:00", "Z"),
        "name": "rate(container_cpu_usage_seconds_total[30s])",
        "value": value,
        "pod": pod,
        "namespace": namespace,
        "container": None,
        "labels": {},
    }


def _flat_baseline(pod: str, namespace: str, n: int, base_ts: datetime) -> list[dict]:
    rng = random.Random(0)
    return [
        _row(
            pod=pod,
            namespace=namespace,
            ts=base_ts + timedelta(seconds=i),
            value=0.1 + rng.gauss(0, 0.01),
        )
        for i in range(n)
    ]


@pytest.fixture
def poller() -> Poller:
    cfg = _make_config()
    return Poller(config=cfg, publisher=Publisher(FakeRedis(), cfg.findings_channel))


@respx.mock
async def test_samples_route_to_correct_per_pod_windows(poller: Poller):
    base = datetime.now(UTC) - timedelta(seconds=10)
    respx.get("http://ingestor.test/buffer/metrics").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 2,
                "rows": [
                    _row(pod="hvac-1", namespace="sh-edge", ts=base, value=0.1),
                    _row(pod="gateway-1", namespace="sh-core", ts=base, value=0.05),
                ],
            },
        )
    )

    async with httpx.AsyncClient() as client:
        await poller.tick_once(client)

    hvac = poller.windows.get("sh-edge", "hvac-1")
    gw = poller.windows.get("sh-core", "gateway-1")
    assert hvac is not None and len(hvac) == 1
    assert gw is not None and len(gw) == 1
    assert hvac.values() == [0.1]
    assert gw.values() == [0.05]


@respx.mock
async def test_no_finding_when_below_min_samples(poller: Poller):
    base = datetime.now(UTC) - timedelta(seconds=10)
    respx.get("http://ingestor.test/buffer/metrics").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 5,
                "rows": _flat_baseline("hvac-1", "sh-edge", 5, base),
            },
        )
    )

    async with httpx.AsyncClient() as client:
        published = await poller.tick_once(client)

    assert published == []
    assert isinstance(poller.publisher.client, FakeRedis)
    assert poller.publisher.client.calls == []


@respx.mock
async def test_publishes_finding_on_spike(poller: Poller):
    base = datetime.now(UTC) - timedelta(seconds=120)
    baseline_rows = _flat_baseline("hvac-1", "sh-edge", 60, base)
    spike = _row(
        pod="hvac-1",
        namespace="sh-edge",
        ts=base + timedelta(seconds=61),
        value=5.0,
    )
    respx.get("http://ingestor.test/buffer/metrics").mock(
        return_value=httpx.Response(
            200,
            json={"count": 61, "rows": baseline_rows + [spike]},
        )
    )

    async with httpx.AsyncClient() as client:
        published = await poller.tick_once(client)

    assert len(published) == 1
    f: Finding = published[0]
    assert f.pod == "hvac-1"
    assert f.namespace == "sh-edge"
    assert f.metric_name == "cpu_rate"
    assert f.current_value == 5.0
    assert f.anomaly_score > 0.5
    assert f.severity in ("warn", "critical")
    assert f.baseline_window_summary.sample_count == 60

    # Publisher actually sent it
    fake = poller.publisher.client
    assert isinstance(fake, FakeRedis)
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "findings.cpu"


@respx.mock
async def test_malformed_rows_are_skipped(poller: Poller):
    base = datetime.now(UTC) - timedelta(seconds=10)
    respx.get("http://ingestor.test/buffer/metrics").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 4,
                "rows": [
                    _row(pod="hvac-1", namespace="sh-edge", ts=base, value=0.1),
                    {"pod": None, "namespace": "sh-edge", "ts": base.isoformat(), "value": 0.1},
                    {"pod": "x", "namespace": "y", "ts": "garbage", "value": 0.1},
                    {"pod": "x", "namespace": "y", "ts": base.isoformat(), "value": None},
                ],
            },
        )
    )
    async with httpx.AsyncClient() as client:
        await poller.tick_once(client)

    # Only the well-formed row should land in a window.
    assert len(poller.windows) == 1
    assert poller.windows.get("sh-edge", "hvac-1") is not None
