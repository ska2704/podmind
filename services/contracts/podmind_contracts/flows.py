from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

# Hubble's verdict enum, narrowed to the values we care about.
# Anything we don't recognise (rare, future versions) is mapped to UNKNOWN
# at the ingestor edge.
Verdict = Literal["FORWARDED", "DROPPED", "ERROR", "AUDIT", "REDIRECTED", "UNKNOWN"]


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
