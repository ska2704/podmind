"""Round-trip JSON tests for every contract.

If the buffer can't deserialise what the ingestor wrote, nothing else in
PodMind works. Keep these dumb and exhaustive.
"""

from datetime import UTC, datetime

import pytest
from podmind_contracts import (
    CausalEdge,
    Finding,
    GetCausalParentsRequest,
    GetCausalParentsResponse,
    GetDependencyNeighborsRequest,
    GetDependencyNeighborsResponse,
    GetPodMetricsRequest,
    GetPodMetricsResponse,
    GetRecentAnomaliesRequest,
    GetRecentAnomaliesResponse,
    HubbleFlow,
    MetricRecord,
    Neighbor,
    PodMetricSeries,
)
from pydantic import ValidationError


def _roundtrip(model):
    """Serialise to JSON and reconstruct. Equality must hold."""
    raw = model.model_dump_json()
    cls = type(model)
    rebuilt = cls.model_validate_json(raw)
    assert rebuilt == model


def test_metric_record_roundtrip():
    m = MetricRecord(
        ts=datetime(2026, 5, 6, 12, 0, tzinfo=UTC),
        name="container_cpu_usage_seconds_total",
        value=0.42,
        pod="gateway-7c9",
        namespace="sh-core",
        container="gateway",
        labels={"image": "nginx:alpine"},
    )
    _roundtrip(m)


def test_metric_record_rejects_extras():
    with pytest.raises(ValidationError):
        MetricRecord(
            ts=datetime.now(UTC),
            name="x",
            value=1.0,
            unknown_field="oops",
        )


def test_metric_record_is_frozen():
    m = MetricRecord(ts=datetime.now(UTC), name="x", value=1.0)
    with pytest.raises(ValidationError):
        m.value = 2.0  # type: ignore[misc]


def test_hubble_flow_roundtrip():
    f = HubbleFlow(
        ts=datetime(2026, 5, 6, 12, 0, tzinfo=UTC),
        verdict="FORWARDED",
        src_pod="gateway-7c9",
        src_namespace="sh-core",
        dst_pod="booking-abc",
        dst_namespace="sh-core",
        l4_protocol="TCP",
        src_port=44321,
        dst_port=8000,
        bytes=812,
        observation_point="TO_STACK",
    )
    _roundtrip(f)


def test_hubble_flow_unknown_verdict_rejected():
    with pytest.raises(ValidationError):
        HubbleFlow(
            ts=datetime.now(UTC),
            verdict="MAYBE",  # type: ignore[arg-type]
        )


def test_hubble_flow_unknown_observation_point_rejected():
    with pytest.raises(ValidationError):
        HubbleFlow(
            ts=datetime.now(UTC),
            verdict="FORWARDED",
            observation_point="SOMEWHERE",  # type: ignore[arg-type]
        )


def test_finding_roundtrip():
    f = Finding(
        id="01HXXX",
        ts=datetime(2026, 5, 6, 12, 0, tzinfo=UTC),
        agent="cpu",
        pod="hvac-controller-aaa",
        namespace="sh-edge",
        kind="anomaly",
        severity="warning",
        summary="CPU throttling spike, 4x baseline",
        evidence={"throttle_ratio": 0.41, "baseline": 0.10, "window_s": 60},
    )
    _roundtrip(f)


def test_finding_rejects_unknown_agent():
    with pytest.raises(ValidationError):
        Finding(
            id="x",
            ts=datetime.now(UTC),
            agent="psychic",  # type: ignore[arg-type]
            pod="p",
            namespace="n",
            kind="anomaly",
            severity="info",
            summary="...",
        )


def test_get_pod_metrics_roundtrip():
    req = GetPodMetricsRequest(
        pod="gateway-7c9",
        namespace="sh-core",
        metric_names=["container_cpu_usage_seconds_total"],
        since_seconds=120,
    )
    _roundtrip(req)

    series = PodMetricSeries(
        name="container_cpu_usage_seconds_total",
        samples=[
            (datetime(2026, 5, 6, 12, 0, 0, tzinfo=UTC), 0.1),
            (datetime(2026, 5, 6, 12, 0, 1, tzinfo=UTC), 0.12),
        ],
    )
    resp = GetPodMetricsResponse(series=[series])
    _roundtrip(resp)


def test_get_causal_parents_roundtrip():
    req = GetCausalParentsRequest(
        pod="hvac-controller-aaa",
        namespace="sh-edge",
        target_metric="container_cpu_usage_seconds_total",
    )
    _roundtrip(req)

    resp = GetCausalParentsResponse(
        edges=[
            CausalEdge(
                parent_pod="sensor-ingest-bbb",
                parent_namespace="sh-edge",
                parent_metric="sqlite_write_latency_seconds",
                lag_seconds=2,
                confidence=0.71,
            )
        ]
    )
    _roundtrip(resp)


def test_get_recent_anomalies_roundtrip():
    req = GetRecentAnomaliesRequest(pod="gateway-7c9", namespace="sh-core")
    _roundtrip(req)

    resp = GetRecentAnomaliesResponse(findings=[])
    _roundtrip(resp)


def test_get_dependency_neighbors_roundtrip():
    req = GetDependencyNeighborsRequest(
        pod="room-aaa",
        namespace="sh-core",
        direction="downstream",
    )
    _roundtrip(req)

    resp = GetDependencyNeighborsResponse(
        neighbors=[
            Neighbor(pod="sensor-ingest-bbb", namespace="sh-edge", direction="downstream"),
            Neighbor(pod="hvac-controller-ccc", namespace="sh-edge", direction="downstream"),
        ]
    )
    _roundtrip(resp)
