"""HTTP-level smoke for the buffer endpoints.

We construct a Buffer ourselves and bind it into the app module before
the lifespan runs the real pollers. Then we use httpx's ASGITransport
so we don't actually start uvicorn or the background tasks.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app import main as app_module
from app.buffer import Buffer
from podmind_contracts import HubbleFlow, MetricRecord


@pytest.fixture
async def client(tmp_path, monkeypatch):
    # swap in a fresh on-disk buffer; skip the real pollers entirely
    test_buf = Buffer(tmp_path / "test.sqlite")
    await test_buf.init()
    monkeypatch.setattr(app_module, "buffer", test_buf)

    # rebuild the route table reference to the new buffer (closures in the
    # app module read app_module.buffer at request time, so this is fine).
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c, test_buf

    await test_buf.close()


async def test_healthz(client):
    c, _ = client
    r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_readyz(client):
    c, _ = client
    r = await c.get("/readyz")
    assert r.status_code == 200


async def test_get_metrics_returns_inserted_rows(client):
    c, buf = client
    now = datetime.now(UTC)
    await buf.insert_metrics(
        [
            MetricRecord(ts=now, name="cpu", value=0.1, pod="gateway-1", namespace="sh-core"),
            MetricRecord(ts=now, name="mem", value=42, pod="gateway-1", namespace="sh-core"),
        ]
    )

    r = await c.get("/buffer/metrics", params={"since": "-30s"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert {row["name"] for row in body["rows"]} == {"cpu", "mem"}


async def test_get_metrics_with_filter(client):
    c, buf = client
    now = datetime.now(UTC)
    await buf.insert_metrics(
        [
            MetricRecord(ts=now, name="cpu", value=0.1, pod="a"),
            MetricRecord(ts=now, name="cpu", value=0.2, pod="b"),
        ]
    )
    r = await c.get("/buffer/metrics", params={"since": "-30s", "pod": "a"})
    assert r.json()["count"] == 1


async def test_get_flows_round_trip(client):
    c, buf = client
    now = datetime.now(UTC)
    await buf.insert_flows(
        [
            HubbleFlow(
                ts=now,
                verdict="FORWARDED",
                src_pod="gateway-1",
                dst_pod="booking-1",
            )
        ]
    )
    r = await c.get("/buffer/flows", params={"since": "-30s"})
    assert r.status_code == 200
    assert r.json()["count"] == 1


async def test_bad_since_returns_400(client):
    c, _ = client
    r = await c.get("/buffer/metrics", params={"since": "yesterday"})
    assert r.status_code == 400


async def test_iso_since_works(client):
    c, buf = client
    now = datetime.now(UTC)
    await buf.insert_metrics([MetricRecord(ts=now, name="cpu", value=0.1)])

    iso = (now - timedelta(seconds=10)).isoformat()
    r = await c.get("/buffer/metrics", params={"since": iso})
    assert r.status_code == 200
    assert r.json()["count"] == 1
