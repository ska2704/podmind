"""Tool-layer tests. respx mocks the ingestor; we hand-feed Findings
into the cache directly."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from podmind_contracts import BaselineSummary, Finding

from app.findings_cache import FindingsCache
from app.tools import (
    dispatch,
    get_pod_metrics,
    get_pod_neighbors,
    get_recent_anomalies,
)


METRIC = "rate(container_cpu_usage_seconds_total[30s])"
INGESTOR = "http://ingestor.test:8000"


def _metric_row(*, pod: str, ns: str, ts: datetime, value: float) -> dict:
    return {
        "ts": ts.isoformat().replace("+00:00", "Z"),
        "name": METRIC,
        "value": value,
        "pod": pod,
        "namespace": ns,
        "container": None,
        "labels": {},
    }


def _flow_row(*, src: str | None, dst: str | None, sport=44321, dport=80) -> dict:
    return {
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "verdict": "FORWARDED",
        "src_pod": src,
        "src_namespace": "sh-edge" if src else None,
        "dst_pod": dst,
        "dst_namespace": "sh-edge" if dst else None,
        "l4_protocol": "TCP",
        "src_port": sport,
        "dst_port": dport,
        "bytes": None,
        "observation_point": "TO_ENDPOINT",
    }


def _finding(*, pod: str, ns: str = "sh-edge", score: float = 0.85, age_s: int = 5) -> Finding:
    return Finding(
        id=f"cpu-agent:{ns}/{pod}:{age_s}",
        ts=datetime.now(UTC) - timedelta(seconds=age_s),
        agent_id="cpu-agent",
        pod=pod,
        namespace=ns,
        metric_name="cpu_rate",
        current_value=0.2,
        anomaly_score=score,
        severity="critical",
        baseline_window_summary=BaselineSummary(mean=0.001, stddev=0.0005, sample_count=200),
    )


# ---- get_pod_metrics ------------------------------------------------


@respx.mock
async def test_get_pod_metrics_summarises_samples():
    now = datetime.now(UTC)
    rows = [
        _metric_row(pod="hvac-controller-abc", ns="sh-edge", ts=now - timedelta(seconds=30 - i), value=0.1 + 0.01 * i)
        for i in range(10)
    ]
    respx.get(f"{INGESTOR}/buffer/metrics").mock(
        return_value=httpx.Response(200, json={"count": 10, "rows": rows})
    )
    async with httpx.AsyncClient() as client:
        result = await get_pod_metrics(
            client=client,
            ingestor_url=INGESTOR,
            metric_query=METRIC,
            pod="hvac-controller",
            since_s=60,
        )

    assert result["pod_match"] == "hvac-controller-abc"
    assert result["namespace"] == "sh-edge"
    assert result["sample_count"] == 10
    assert result["summary"]["min"] == pytest.approx(0.10)
    assert result["summary"]["max"] == pytest.approx(0.19)
    assert result["summary"]["current"] == pytest.approx(0.19)
    assert 0.14 < result["summary"]["mean"] < 0.16
    # Sample truncation: at most 30
    assert len(result["samples"]) == 10


@respx.mock
async def test_get_pod_metrics_handles_missing_pod():
    respx.get(f"{INGESTOR}/buffer/metrics").mock(
        return_value=httpx.Response(200, json={"count": 0, "rows": []})
    )
    async with httpx.AsyncClient() as client:
        result = await get_pod_metrics(
            client=client,
            ingestor_url=INGESTOR,
            metric_query=METRIC,
            pod="nonexistent",
            since_s=60,
        )
    assert result["sample_count"] == 0
    assert result["summary"] is None
    assert "nonexistent" in result["note"]


@respx.mock
async def test_get_pod_metrics_rejects_since_zero():
    """The tool descriptions say 'do not pass 0' but we defend in code too."""
    respx.get(f"{INGESTOR}/buffer/metrics").mock(
        return_value=httpx.Response(200, json={"count": 0, "rows": []})
    )
    async with httpx.AsyncClient() as client:
        result = await get_pod_metrics(
            client=client,
            ingestor_url=INGESTOR,
            metric_query=METRIC,
            pod="anything",
            since_s=0,
        )
    # silently coerce up to default
    assert result["since_s"] == 120


@respx.mock
async def test_get_pod_metrics_propagates_ingestor_error():
    respx.get(f"{INGESTOR}/buffer/metrics").mock(
        return_value=httpx.Response(500, text="boom")
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await get_pod_metrics(
                client=client,
                ingestor_url=INGESTOR,
                metric_query=METRIC,
                pod="x",
                since_s=60,
            )


# ---- get_recent_anomalies ------------------------------------------


async def test_get_recent_anomalies_filters_by_pod_substring():
    cache = FindingsCache(size=10)
    cache.add(_finding(pod="hvac-controller-aaa"))
    cache.add(_finding(pod="hvac-controller-bbb"))
    cache.add(_finding(pod="gateway-xyz"))

    result = await get_recent_anomalies(cache=cache, pod="hvac-controller", since_s=60)
    assert result["count"] == 2
    pods = {f["pod"] for f in result["findings"]}
    assert pods == {"hvac-controller-aaa", "hvac-controller-bbb"}


async def test_get_recent_anomalies_returns_all_when_pod_unspecified():
    cache = FindingsCache(size=10)
    cache.add(_finding(pod="a-aaa"))
    cache.add(_finding(pod="b-bbb"))
    result = await get_recent_anomalies(cache=cache, since_s=60)
    assert result["count"] == 2


async def test_get_recent_anomalies_drops_old():
    cache = FindingsCache(size=10)
    cache.add(_finding(pod="hvac-controller-old", age_s=900))
    cache.add(_finding(pod="hvac-controller-new", age_s=10))
    result = await get_recent_anomalies(cache=cache, pod="hvac-controller", since_s=60)
    assert result["count"] == 1
    assert result["findings"][0]["pod"] == "hvac-controller-new"


# ---- get_pod_neighbors --------------------------------------------


@respx.mock
async def test_get_pod_neighbors_aggregates_direct_flows():
    """Direct pod-IP flows (no service VIP, both sides populated)."""
    rows = [
        _flow_row(src="gateway-1", dst="hvac-controller-aaa"),
        _flow_row(src="gateway-1", dst="hvac-controller-aaa"),
        _flow_row(src="hvac-controller-aaa", dst="room-z"),
        _flow_row(src="hvac-controller-aaa", dst="energy-meter-z"),
        _flow_row(src="hvac-controller-aaa", dst="energy-meter-z"),
    ]
    respx.get(f"{INGESTOR}/buffer/flows").mock(
        return_value=httpx.Response(200, json={"count": len(rows), "rows": rows})
    )
    async with httpx.AsyncClient() as client:
        result = await get_pod_neighbors(
            client=client,
            ingestor_url=INGESTOR,
            pod="hvac-controller",
            since_s=120,
        )

    talks_to = {n["pod"]: n["flow_count"] for n in result["talks_to"]}
    talked_to_by = {n["pod"]: n["flow_count"] for n in result["talked_to_by"]}
    assert talks_to == {"room-z": 1, "energy-meter-z": 2}
    assert talked_to_by == {"gateway-1": 2}


@respx.mock
async def test_get_pod_neighbors_pairs_socketlb_halves():
    """Pair half-flows from a service-VIP call.

    Scenario: gateway-1 calls hvac-controller-aaa via the service VIP.
    socketLB splits this into:
      • gateway's view (TO_STACK):    src=gateway-1, dst=None, src_port=47786, dst_port=80
      • hvac's view (TO_ENDPOINT):    src=None,      dst=hvac, src_port=47786, dst_port=80
    Neither half on its own names both ends, so the legacy
    both-sides-populated check returns nothing. The 5-tuple pairing
    must recover 'gateway-1' as a caller of hvac-controller.
    """
    rows = [
        # half 1: gateway's outbound stack view
        {
            **_flow_row(src="gateway-1", dst=None, sport=47786, dport=80),
            "observation_point": "TO_STACK",
        },
        # half 2: hvac's inbound endpoint view
        {
            **_flow_row(src=None, dst="hvac-controller-aaa", sport=47786, dport=80),
            "observation_point": "TO_ENDPOINT",
        },
    ]
    respx.get(f"{INGESTOR}/buffer/flows").mock(
        return_value=httpx.Response(200, json={"count": 2, "rows": rows})
    )
    async with httpx.AsyncClient() as client:
        result = await get_pod_neighbors(
            client=client,
            ingestor_url=INGESTOR,
            pod="hvac-controller",
            since_s=120,
        )
    talked_to_by = {n["pod"]: n["flow_count"] for n in result["talked_to_by"]}
    assert talked_to_by == {"gateway-1": 1}, result


# ---- dispatch --------------------------------------------------------


async def test_dispatch_unknown_tool_returns_error():
    cache = FindingsCache(size=10)
    async with httpx.AsyncClient() as client:
        result = await dispatch(
            "bogus_tool", {},
            client=client,
            cache=cache,
            ingestor_url=INGESTOR,
            metric_query=METRIC,
        )
    assert "unknown tool" in result["error"]


@respx.mock
async def test_dispatch_routes_to_correct_tool():
    cache = FindingsCache(size=10)
    cache.add(_finding(pod="hvac-controller-abc"))
    async with httpx.AsyncClient() as client:
        r = await dispatch(
            "get_recent_anomalies", {"pod": "hvac-controller", "since_s": 60},
            client=client,
            cache=cache,
            ingestor_url=INGESTOR,
            metric_query=METRIC,
        )
    assert r["count"] == 1
