"""Tests for the deterministic-orchestration path."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from podmind_contracts import BaselineSummary, Finding

from app.config import Config
from app.findings_cache import FindingsCache
from app.orchestrator import (
    _short_name,
    collect_known_pods,
    deterministic_ask,
    extract_pod_short,
)


INGESTOR = "http://ingestor.test:8000"
OLLAMA = "http://ollama.test:11434"
METRIC = "rate(container_cpu_usage_seconds_total[30s])"


def _config(**overrides) -> Config:
    defaults = dict(
        ingestor_url=INGESTOR,
        redis_url="redis://test:6379/0",
        ollama_url=OLLAMA,
        model_name="qwen2.5:1.5b-instruct-q4_K_M",
        findings_channel="findings.cpu",
        findings_cache_size=200,
        max_tool_rounds=5,
        default_metric_query=METRIC,
    )
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def _finding(*, pod: str, ns: str = "sh-edge") -> Finding:
    return Finding(
        id=f"x:{pod}",
        ts=datetime.now(UTC) - timedelta(seconds=5),
        agent_id="cpu-agent",
        pod=pod,
        namespace=ns,
        metric_name="cpu_rate",
        current_value=0.2,
        anomaly_score=0.85,
        severity="critical",
        baseline_window_summary=BaselineSummary(mean=0.001, stddev=0.0005, sample_count=200),
    )


def test_short_name_strips_deployment_suffix():
    assert _short_name("hvac-controller-cff74f845-czxng") == "hvac-controller"
    assert _short_name("gateway-68ddb457d-754vj") == "gateway"
    # ReplicaSet hash + pod hash exactly at the trailing pattern:
    assert _short_name("guest-sim-5bbd9b8956-blw2l") == "guest-sim"


def test_short_name_leaves_non_matching_alone():
    # No suffix pattern → return as-is. Covers static-name pods.
    assert _short_name("redis") == "redis"
    assert _short_name("etcd-0") == "etcd-0"


def test_extract_pod_short_picks_longest_substring_match():
    shorts = {
        "auth": "auth-abc-x",
        "auth-server": "auth-server-def-y",
        "gateway": "gateway-zzz-q",
    }
    # Both "auth" and "auth-server" are substrings; prefer the longer.
    assert extract_pod_short("what is happening with auth-server?", shorts) == "auth-server"


def test_extract_pod_short_case_insensitive():
    shorts = {"hvac-controller": "hvac-controller-abc-x"}
    assert extract_pod_short("Tell me about HVAC-Controller", shorts) == "hvac-controller"


def test_extract_pod_short_returns_none_when_no_match():
    shorts = {"hvac-controller": "hvac-controller-abc-x"}
    assert extract_pod_short("what is broken in the cluster?", shorts) is None


@respx.mock
async def test_collect_known_pods_merges_cache_and_ingestor():
    cache = FindingsCache(size=10)
    cache.add(_finding(pod="hvac-controller-cff74f845-czxng"))
    rows = [
        {
            "ts": datetime.now(UTC).isoformat(),
            "name": METRIC, "value": 0.1,
            "pod": "gateway-68ddb457d-754vj",
            "namespace": "sh-core", "container": None, "labels": {},
        },
        {
            "ts": datetime.now(UTC).isoformat(),
            "name": METRIC, "value": 0.1,
            "pod": "auth-686d58bff8-zdllw",
            "namespace": "sh-core", "container": None, "labels": {},
        },
    ]
    respx.get(f"{INGESTOR}/buffer/metrics").mock(
        return_value=httpx.Response(200, json={"count": 2, "rows": rows})
    )
    async with httpx.AsyncClient() as client:
        shorts = await collect_known_pods(
            client=client,
            ingestor_url=INGESTOR,
            metric_query=METRIC,
            cache=cache,
        )
    assert "hvac-controller" in shorts
    assert shorts["hvac-controller"] == "hvac-controller-cff74f845-czxng"
    assert "gateway" in shorts
    assert "auth" in shorts


@respx.mock
async def test_collect_known_pods_tolerates_ingestor_error():
    """If the ingestor is briefly unavailable, fall back to whatever the
    cache contains rather than failing extraction."""
    cache = FindingsCache(size=10)
    cache.add(_finding(pod="hvac-controller-cff74f845-czxng"))
    respx.get(f"{INGESTOR}/buffer/metrics").mock(
        return_value=httpx.Response(500, text="boom")
    )
    async with httpx.AsyncClient() as client:
        shorts = await collect_known_pods(
            client=client,
            ingestor_url=INGESTOR,
            metric_query=METRIC,
            cache=cache,
        )
    assert "hvac-controller" in shorts


@respx.mock
async def test_deterministic_ask_runs_all_three_tools_and_summarises():
    cache = FindingsCache(size=10)
    cache.add(_finding(pod="hvac-controller-aaa-bbbbb"))

    # Ingestor mock — used by get_pod_metrics + get_pod_neighbors
    metric_rows = [
        {
            "ts": datetime.now(UTC).isoformat(),
            "name": METRIC, "value": 0.2,
            "pod": "hvac-controller-aaa-bbbbb",
            "namespace": "sh-edge", "container": None, "labels": {},
        }
    ] * 5
    respx.get(f"{INGESTOR}/buffer/metrics").mock(
        return_value=httpx.Response(200, json={"count": 5, "rows": metric_rows})
    )
    flow_rows = [
        {
            "ts": datetime.now(UTC).isoformat(),
            "verdict": "FORWARDED",
            "src_pod": "gateway-1", "src_namespace": "sh-core",
            "dst_pod": "hvac-controller-aaa-bbbbb", "dst_namespace": "sh-edge",
            "l4_protocol": "TCP", "src_port": 4321, "dst_port": 80, "bytes": None,
            "observation_point": "TO_ENDPOINT",
        }
    ]
    respx.get(f"{INGESTOR}/buffer/flows").mock(
        return_value=httpx.Response(200, json={"count": 1, "rows": flow_rows})
    )

    # Ollama mock — should be called exactly once with NO tools field
    respx.post(f"{OLLAMA}/api/chat").mock(
        return_value=httpx.Response(200, json={
            "model": "qwen2.5:1.5b",
            "message": {
                "role": "assistant",
                "content": "hvac-controller has critical anomalies and talks to gateway-1.",
                "tool_calls": [],
            },
            "eval_count": 1,
            "total_duration": 100_000_000,
        })
    )

    async with httpx.AsyncClient() as client:
        result = await deterministic_ask(
            config=_config(),
            client=client,
            cache=cache,
            question="what is happening with hvac-controller?",
            pod_short="hvac-controller",
        )

    # Always reports all three tools, in canonical order.
    names = [tc["name"] for tc in result["tools_called"]]
    assert names == ["get_recent_anomalies", "get_pod_metrics", "get_pod_neighbors"]
    assert result["answer"] == "hvac-controller has critical anomalies and talks to gateway-1."

    # Confirm only ONE /api/chat call was made — single-shot summary, no tool loop.
    chat_routes = [call for call in respx.calls if "/api/chat" in str(call.request.url)]
    assert len(chat_routes) == 1
    # And that call did not pass tools — the deterministic path is non-autonomous.
    import json as _json
    body = _json.loads(chat_routes[0].request.content)
    assert "tools" not in body
