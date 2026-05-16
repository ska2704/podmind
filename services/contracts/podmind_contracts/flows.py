from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

# Hubble's verdict enum, narrowed to the values we care about.
# Anything we don't recognise (rare, future versions) is mapped to UNKNOWN
# at the ingestor edge.
Verdict = Literal["FORWARDED", "DROPPED", "ERROR", "AUDIT", "REDIRECTED", "UNKNOWN"]

# Cilium's trace_observation_point enum, mirrored from upstream v1.19.1.
# With socketLB enabled, service-VIP traffic appears in Hubble as two
# halves with single-sided identity (TO_STACK carries the sender, TO_ENDPOINT
# carries the receiver); consumers pair halves by 5-tuple + this field.
ObservationPoint = Literal[
    "UNKNOWN_POINT",
    "TO_PROXY",
    "TO_HOST",
    "TO_STACK",
    "TO_OVERLAY",
    "FROM_ENDPOINT",
    "FROM_PROXY",
    "FROM_HOST",
    "FROM_STACK",
    "FROM_OVERLAY",
    "FROM_NETWORK",
    "TO_NETWORK",
    "FROM_CRYPTO",
    "TO_CRYPTO",
    "TO_ENDPOINT",
]


class HubbleFlow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: datetime
    verdict: Verdict

    src_pod: str | None = None
    src_namespace: str | None = None
    dst_pod: str | None = None
    dst_namespace: str | None = None

    l4_protocol: str | None = None
    src_port: int | None = None
    dst_port: int | None = None

    # Hubble doesn't always carry byte counts (depends on collector config),
    # so this is allowed to be missing.
    bytes: int | None = None

    observation_point: ObservationPoint | None = None
