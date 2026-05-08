from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MetricRecord(BaseModel):
    """One Prometheus sample, flattened so the buffer can store and query it.

    The ingestor pulls instant queries on a 1s tick and writes one row per
    series per tick. Anything not covered by the four named columns lives
    in `labels`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: datetime
    name: str
    value: float

    pod: str | None = None
    namespace: str | None = None
    container: str | None = None

    labels: dict[str, str] = Field(default_factory=dict)
