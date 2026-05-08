from datetime import UTC, datetime, timedelta

import pytest
from app.buffer import Buffer
from podmind_contracts import HubbleFlow, MetricRecord


@pytest.fixture
async def buf(tmp_path):
    b = Buffer(tmp_path / "b.sqlite")
    await b.init()
    try:
        yield b
    finally:
        await b.close()


def _now():
    return datetime.now(UTC)


async def test_metric_insert_and_query(buf):
    now = _now()
    records = [
        MetricRecord(
            ts=now - timedelta(seconds=2),
            name="cpu",
            value=0.1,
            pod="gateway-1",
            namespace="sh-core",
            labels={"image": "nginx:alpine"},
        ),
        MetricRecord(
            ts=now - timedelta(seconds=1),
            name="cpu",
            value=0.2,
            pod="gateway-1",
            namespace="sh-core",
        ),
    ]
    await buf.insert_metrics(records)

    rows = await buf.query_metrics(now - timedelta(seconds=10))
    assert len(rows) == 2
    assert [r.value for r in rows] == [0.1, 0.2]
    assert rows[0].labels == {"image": "nginx:alpine"}


async def test_metric_filter_by_pod_and_name(buf):
    now = _now()
    await buf.insert_metrics(
        [
            MetricRecord(ts=now, name="cpu", value=0.1, pod="a"),
            MetricRecord(ts=now, name="cpu", value=0.2, pod="b"),
            MetricRecord(ts=now, name="mem", value=42.0, pod="a"),
        ]
    )

    only_a = await buf.query_metrics(now - timedelta(seconds=10), pod="a")
    assert {r.name for r in only_a} == {"cpu", "mem"}

    only_cpu = await buf.query_metrics(now - timedelta(seconds=10), name="cpu")
    assert {r.pod for r in only_cpu} == {"a", "b"}


async def test_flow_insert_and_query(buf):
    now = _now()
    flows = [
        HubbleFlow(
            ts=now,
            verdict="FORWARDED",
            src_pod="gateway-1",
            src_namespace="sh-core",
            dst_pod="booking-1",
            dst_namespace="sh-core",
            l4_protocol="TCP",
            src_port=4444,
            dst_port=8000,
        ),
        HubbleFlow(
            ts=now,
            verdict="DROPPED",
            src_pod="gateway-1",
            dst_pod="auth-1",
        ),
    ]
    await buf.insert_flows(flows)

    rows = await buf.query_flows(now - timedelta(seconds=10), src="gateway-1")
    assert len(rows) == 2
    assert {r.verdict for r in rows} == {"FORWARDED", "DROPPED"}


async def test_sweep_drops_old_rows(buf):
    now = _now()
    await buf.insert_metrics(
        [
            MetricRecord(ts=now - timedelta(seconds=400), name="cpu", value=0.1, pod="old"),
            MetricRecord(ts=now - timedelta(seconds=10), name="cpu", value=0.2, pod="new"),
        ]
    )

    deleted = await buf.sweep(now, window_s=300)
    assert deleted == 1

    rows = await buf.query_metrics(now - timedelta(seconds=600))
    assert [r.pod for r in rows] == ["new"]


async def test_query_empty_buffer(buf):
    rows = await buf.query_metrics(_now() - timedelta(seconds=300))
    assert rows == []
