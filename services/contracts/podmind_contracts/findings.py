from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

Severity = Literal["info", "warn", "critical"]


class BaselineSummary(BaseModel):
    """Stats over the window the detector fit on, before scoring the
    most recent sample. The coordinator passes these to the LLM as
    context — keep them small and numeric.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mean: float
    stddev: float
    sample_count: int


class Finding(BaseModel):
    """An agent's claim about a pod, published to Redis pub/sub.

    Each agent publishes to its own channel (e.g. `findings.cpu`); the
    coordinator subscribes to `findings.*` and fans them out from there.

    `id` is the dedupe key — pick a ULID or `{agent_id}:{pod}:{ts.isoformat()}`.
    Same id from the same agent must mean the same logical event.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    ts: datetime

    agent_id: str
    pod: str
    namespace: str

    metric_name: str
    current_value: float
    anomaly_score: float

    severity: Severity
    baseline_window_summary: BaselineSummary
