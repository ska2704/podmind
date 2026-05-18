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
