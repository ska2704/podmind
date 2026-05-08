from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["info", "warning", "critical"]
AgentName = Literal["cpu", "memory", "storage", "network"]


class Finding(BaseModel):
    """An agent's claim about a pod. Published to Redis pub/sub.

    `evidence` is a free-form bag of numbers and strings — keep it small,
    the coordinator passes it to the LLM as context. The agents own the
    schema of what they put in here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    ts: datetime

    agent: AgentName
    pod: str
    namespace: str

    kind: str
    severity: Severity
    summary: str

    evidence: dict[str, float | int | str] = Field(default_factory=dict)
