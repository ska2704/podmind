"""Coordinator tool-call schemas.

The coordinator (Qwen2.5-3B via Ollama) calls four tools in a loop. These
models are the request/response payloads it produces and consumes.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .findings import Finding

_strict = ConfigDict(frozen=True, extra="forbid")


# get_pod_metrics ------------------------------------------------------------


class GetPodMetricsRequest(BaseModel):
    model_config = _strict

    pod: str
    namespace: str
    metric_names: list[str]
    since_seconds: int = 300


class PodMetricSeries(BaseModel):
    model_config = _strict

    name: str
    samples: list[tuple[datetime, float]]


class GetPodMetricsResponse(BaseModel):
    model_config = _strict

    series: list[PodMetricSeries] = Field(default_factory=list)


# get_causal_parents ---------------------------------------------------------


class GetCausalParentsRequest(BaseModel):
    model_config = _strict

    pod: str
    namespace: str
    target_metric: str


class CausalEdge(BaseModel):
    model_config = _strict

    parent_pod: str
    parent_namespace: str
    parent_metric: str
    lag_seconds: int
    confidence: float


class GetCausalParentsResponse(BaseModel):
    model_config = _strict

    edges: list[CausalEdge] = Field(default_factory=list)


# get_recent_anomalies -------------------------------------------------------


class GetRecentAnomaliesRequest(BaseModel):
    model_config = _strict

    pod: str | None = None
    namespace: str | None = None
    since_seconds: int = 300


class GetRecentAnomaliesResponse(BaseModel):
    model_config = _strict

    findings: list[Finding] = Field(default_factory=list)


# get_dependency_neighbors ---------------------------------------------------


Direction = Literal["upstream", "downstream", "both"]


class GetDependencyNeighborsRequest(BaseModel):
    model_config = _strict

    pod: str
    namespace: str
    direction: Direction = "both"


class Neighbor(BaseModel):
    model_config = _strict

    pod: str
    namespace: str
    direction: Literal["upstream", "downstream"]


class GetDependencyNeighborsResponse(BaseModel):
    model_config = _strict

    neighbors: list[Neighbor] = Field(default_factory=list)
