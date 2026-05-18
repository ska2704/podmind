"""End-to-end test of /ask with a mocked LLM layer.

We bypass the lifespan entirely and populate `main.state` by hand —
the tests aren't trying to exercise the subscriber, just the request/
response plumbing on top of llm_ask.
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


async def test_ask_returns_answer_and_tools(client, monkeypatch):
    async def fake_ask(**kwargs):
        return {
            "answer": "hvac-controller is running hot.",
            "tools_called": [
                {"name": "get_recent_anomalies", "arguments": {"pod": "hvac-controller"}},
                {"name": "get_pod_metrics", "arguments": {"pod": "hvac-controller", "since_s": 120}},
            ],
        }

    monkeypatch.setattr(main_module, "llm_ask", fake_ask)
    r = await client.post("/ask", json={"question": "why is hvac hot?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "hvac-controller is running hot."
    assert len(body["tools_called"]) == 2
    assert body["tools_called"][0]["name"] == "get_recent_anomalies"


async def test_ask_returns_502_on_upstream_http_error(client, monkeypatch):
    async def explodes(**kwargs):
        raise httpx.ConnectError("ollama gone")

    monkeypatch.setattr(main_module, "llm_ask", explodes)
    r = await client.post("/ask", json={"question": "anything?"})
    assert r.status_code == 502
    assert "upstream" in r.json()["detail"].lower()


async def test_ask_returns_503_when_ollama_unreachable(client, monkeypatch):
    async def _no(_):
        return False

    monkeypatch.setattr(main_module, "_ping_ollama", _no)
    main_module.state.ollama_reachable = False

    r = await client.post("/ask", json={"question": "anything?"})
    assert r.status_code == 503
