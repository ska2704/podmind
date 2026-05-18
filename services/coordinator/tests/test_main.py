"""End-to-end /ask tests.

We bypass the lifespan (so the Redis subscriber doesn't try to connect)
and populate `main.state` by hand. /ask is exercised on both dispatch
paths: deterministic (pod identifiable) and autonomous (pod not in the
question).
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from app import main as main_module
from app.findings_cache import FindingsCache


@pytest.fixture
async def client(monkeypatch):
    main_module.state.cache = FindingsCache(size=10)
    main_module.state.http = httpx.AsyncClient()
    main_module.state.ollama_reachable = True

    async def _reachable(_):
        return True

    monkeypatch.setattr(main_module, "_ping_ollama", _reachable)

    transport = ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await main_module.state.http.aclose()
    main_module.state.cache = None
    main_module.state.http = None


async def test_healthz(client):
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_readyz_ok(client):
    r = await client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert "ollama" in body


# --- /findings/recent -----------------------------------------------------


async def test_findings_recent_returns_all_when_no_filter(client):
    from datetime import UTC, datetime, timedelta
    from podmind_contracts import BaselineSummary, Finding

    def _f(pod: str) -> Finding:
        return Finding(
            id=f"x:{pod}",
            ts=datetime.now(UTC) - timedelta(seconds=2),
            agent_id="cpu-agent",
            pod=pod,
            namespace="sh-edge",
            metric_name="cpu_rate",
            current_value=0.2,
            anomaly_score=0.85,
            severity="critical",
            baseline_window_summary=BaselineSummary(mean=0.001, stddev=0.0005, sample_count=200),
        )

    main_module.state.cache.add(_f("hvac-controller-aaa"))
    main_module.state.cache.add(_f("room-bbb"))

    r = await client.get("/findings/recent?since_s=60")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert {row["pod"] for row in body} == {"hvac-controller-aaa", "room-bbb"}
    # Shape sanity: every row carries the fields the UI consumes.
    for row in body:
        assert {"id", "ts", "pod", "namespace", "anomaly_score", "severity"} <= row.keys()


async def test_findings_recent_filters_by_pod_substring(client):
    from datetime import UTC, datetime, timedelta
    from podmind_contracts import BaselineSummary, Finding

    def _f(pod: str) -> Finding:
        return Finding(
            id=f"x:{pod}",
            ts=datetime.now(UTC) - timedelta(seconds=2),
            agent_id="cpu-agent",
            pod=pod,
            namespace="sh-edge",
            metric_name="cpu_rate",
            current_value=0.2,
            anomaly_score=0.85,
            severity="critical",
            baseline_window_summary=BaselineSummary(mean=0.001, stddev=0.0005, sample_count=200),
        )

    main_module.state.cache.add(_f("hvac-controller-aaa"))
    main_module.state.cache.add(_f("hvac-controller-bbb"))
    main_module.state.cache.add(_f("gateway-zzz"))

    r = await client.get("/findings/recent?since_s=60&pod=hvac-controller")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert all("hvac-controller" in row["pod"] for row in body)


async def test_findings_recent_coerces_since_s_zero(client):
    """The same defensive behaviour as the tool layer: since_s<=0 falls
    back to the default rather than returning nothing."""
    r = await client.get("/findings/recent?since_s=0")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# --- deterministic path ---------------------------------------------------


async def test_ask_pod_identified_uses_deterministic_path(client, monkeypatch):
    """When the question names a known pod, /ask must use the
    deterministic 3-tool orchestration and NOT fall through to llm_ask.
    """
    # The orchestrator's pod extractor reads from cache + ingestor;
    # stub it to return a known short name.
    async def fake_collect(**kwargs):
        return {"hvac-controller": "hvac-controller-aaa-bbbbb"}

    captured = {}

    async def fake_deterministic(*, config, client, cache, question, pod_short):
        captured["pod_short"] = pod_short
        captured["question"] = question
        return {
            "answer": "hvac-controller is hot.",
            "tools_called": [
                {"name": "get_recent_anomalies", "arguments": {"pod": "hvac-controller", "since_s": 300}},
                {"name": "get_pod_metrics", "arguments": {"pod": "hvac-controller", "since_s": 180}},
                {"name": "get_pod_neighbors", "arguments": {"pod": "hvac-controller", "since_s": 180}},
            ],
        }

    async def fake_llm_ask(**kwargs):
        captured["llm_ask_called"] = True
        return {"answer": "should not be called", "tools_called": []}

    monkeypatch.setattr(main_module, "collect_known_pods", fake_collect)
    monkeypatch.setattr(main_module, "deterministic_ask", fake_deterministic)
    monkeypatch.setattr(main_module, "llm_ask", fake_llm_ask)

    r = await client.post("/ask", json={"question": "what is happening with hvac-controller?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "hvac-controller is hot."
    names = [tc["name"] for tc in body["tools_called"]]
    assert names == ["get_recent_anomalies", "get_pod_metrics", "get_pod_neighbors"]
    assert captured["pod_short"] == "hvac-controller"
    assert "llm_ask_called" not in captured


# --- autonomous fallback --------------------------------------------------


async def test_ask_no_pod_identified_falls_back_to_llm_loop(client, monkeypatch):
    """When the question doesn't name a known pod, fall back to the
    autonomous tool-calling loop."""

    async def fake_collect(**kwargs):
        return {"hvac-controller": "hvac-controller-aaa"}  # nothing matching the question below

    async def fake_deterministic(**kwargs):
        raise AssertionError("deterministic path should not run")

    async def fake_llm_ask(**kwargs):
        return {
            "answer": "Cluster is degraded.",
            "tools_called": [
                {"name": "get_recent_anomalies", "arguments": {}},
            ],
        }

    monkeypatch.setattr(main_module, "collect_known_pods", fake_collect)
    monkeypatch.setattr(main_module, "deterministic_ask", fake_deterministic)
    monkeypatch.setattr(main_module, "llm_ask", fake_llm_ask)

    r = await client.post("/ask", json={"question": "what is broken in the cluster?"})
    assert r.status_code == 200
    assert r.json()["answer"] == "Cluster is degraded."


# --- error paths ----------------------------------------------------------


async def test_ask_returns_502_on_upstream_http_error(client, monkeypatch):
    async def fake_collect(**kwargs):
        return {"hvac-controller": "hvac-controller-aaa"}

    async def explodes(**kwargs):
        raise httpx.ConnectError("ollama gone")

    monkeypatch.setattr(main_module, "collect_known_pods", fake_collect)
    monkeypatch.setattr(main_module, "deterministic_ask", explodes)

    r = await client.post("/ask", json={"question": "what is happening with hvac-controller?"})
    assert r.status_code == 502
    assert "upstream" in r.json()["detail"].lower()


async def test_ask_returns_503_when_ollama_unreachable(client, monkeypatch):
    async def _no(_):
        return False

    monkeypatch.setattr(main_module, "_ping_ollama", _no)
    main_module.state.ollama_reachable = False

    r = await client.post("/ask", json={"question": "anything?"})
    assert r.status_code == 503
