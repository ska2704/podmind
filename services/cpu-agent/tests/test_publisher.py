"""Publisher emits a JSON-serialized Finding to the configured channel."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from podmind_contracts import BaselineSummary, Finding

from app.publisher import Publisher


class FakeRedis:
    """In-memory Redis stand-in matching the only method we need."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.calls.append((channel, message))
        return 1


@pytest.fixture
def finding() -> Finding:
    return Finding(
        id="cpu-agent:sh-edge/hvac-1:2026-05-17T12:00:00+00:00",
        ts=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        agent_id="cpu-agent",
        pod="hvac-1",
        namespace="sh-edge",
        metric_name="cpu_rate",
        current_value=3.7,
        anomaly_score=0.81,
        severity="critical",
        baseline_window_summary=BaselineSummary(mean=0.1, stddev=0.02, sample_count=60),
    )


async def test_publish_sends_json_to_channel(finding: Finding):
    redis = FakeRedis()
    publisher = Publisher(client=redis, channel="findings.cpu")

    delivered = await publisher.publish(finding)

    assert delivered == 1
    assert len(redis.calls) == 1
    channel, payload = redis.calls[0]
    assert channel == "findings.cpu"

    body: dict[str, Any] = json.loads(payload)
    assert body["pod"] == "hvac-1"
    assert body["namespace"] == "sh-edge"
    assert body["agent_id"] == "cpu-agent"
    assert body["metric_name"] == "cpu_rate"
    assert body["anomaly_score"] == 0.81
    assert body["severity"] == "critical"
    assert body["baseline_window_summary"] == {
        "mean": 0.1,
        "stddev": 0.02,
        "sample_count": 60,
    }


async def test_publish_returns_subscriber_count(finding: Finding):
    class ZeroSubsRedis(FakeRedis):
        async def publish(self, channel: str, message: str) -> int:
            await super().publish(channel, message)
            return 0

    publisher = Publisher(client=ZeroSubsRedis(), channel="findings.cpu")
    delivered = await publisher.publish(finding)
    # 0 is a legitimate "nobody listening yet" — not an error.
    assert delivered == 0
