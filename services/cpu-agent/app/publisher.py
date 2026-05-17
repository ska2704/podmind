"""Redis pub/sub publisher for Findings.

Tiny wrapper around redis.asyncio so the agent can `await
publisher.publish(finding)` without each caller knowing the channel
name or the serialization format.
"""

from __future__ import annotations

import logging
from typing import Protocol

from podmind_contracts import Finding

log = logging.getLogger(__name__)


class _RedisLike(Protocol):
    async def publish(self, channel: str, message: str) -> int: ...


class Publisher:
    def __init__(self, client: _RedisLike, channel: str):
        self.client = client
        self.channel = channel

    async def publish(self, finding: Finding) -> int:
        """Publish one Finding. Returns the number of subscribers it
        was delivered to (Redis semantics — 0 is normal when no
        consumer is listening yet)."""
        payload = finding.model_dump_json()
        delivered = await self.client.publish(self.channel, payload)
        log.info(
            "finding published: pod=%s score=%.3f severity=%s -> %s (subs=%d)",
            finding.pod,
            finding.anomaly_score,
            finding.severity,
            self.channel,
            delivered,
        )
        return delivered
