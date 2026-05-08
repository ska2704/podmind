"""Hubble Relay flow streamer.

Opens a server-streaming GetFlows RPC against the Relay, maps each flow
into a HubbleFlow, batches them up, writes to the buffer. Reconnects
with exponential backoff on transport errors.
"""

import asyncio
import logging
from datetime import UTC, datetime

import grpc
from podmind_contracts import HubbleFlow

from ._proto import observer_pb2, observer_pb2_grpc
from .buffer import Buffer

log = logging.getLogger(__name__)


_VERDICT = {
    observer_pb2.VERDICT_UNKNOWN: "UNKNOWN",
    observer_pb2.FORWARDED: "FORWARDED",
    observer_pb2.DROPPED: "DROPPED",
    observer_pb2.ERROR: "ERROR",
    observer_pb2.AUDIT: "AUDIT",
    observer_pb2.REDIRECTED: "REDIRECTED",
}


def _ts_from_pb(pb_ts) -> datetime:
    if pb_ts is None or (pb_ts.seconds == 0 and pb_ts.nanos == 0):
        return datetime.now(UTC)
    return datetime.fromtimestamp(pb_ts.seconds + pb_ts.nanos / 1e9, tz=UTC)


def flow_to_record(flow) -> HubbleFlow:
    src = flow.source if flow.HasField("source") else None
    dst = flow.destination if flow.HasField("destination") else None

    proto = None
    src_port: int | None = None
    dst_port: int | None = None
    if flow.HasField("l4"):
        l4 = flow.l4
        if l4.HasField("tcp"):
            proto = "TCP"
            src_port = l4.tcp.source_port
            dst_port = l4.tcp.destination_port
        elif l4.HasField("udp"):
            proto = "UDP"
            src_port = l4.udp.source_port
            dst_port = l4.udp.destination_port

    return HubbleFlow(
        ts=_ts_from_pb(flow.time),
        verdict=_VERDICT.get(flow.verdict, "UNKNOWN"),
        src_pod=src.pod_name if src else None,
        src_namespace=src.namespace if src else None,
        dst_pod=dst.pod_name if dst else None,
        dst_namespace=dst.namespace if dst else None,
        l4_protocol=proto,
        src_port=src_port,
        dst_port=dst_port,
        bytes=None,
    )


async def stream_forever(
    buffer: Buffer,
    relay_addr: str,
    *,
    batch_size: int = 32,
    batch_timeout_s: float = 1.0,
) -> None:
    """Tail the GetFlows stream forever. Writes batches to `buffer`."""
    backoff = 1.0
    while True:
        try:
            async with grpc.aio.insecure_channel(relay_addr) as channel:
                stub = observer_pb2_grpc.ObserverStub(channel)
                request = observer_pb2.GetFlowsRequest(follow=True)

                pending: list[HubbleFlow] = []
                last_flush = asyncio.get_event_loop().time()

                async for resp in stub.GetFlows(request):
                    if not resp.HasField("flow"):
                        continue
                    pending.append(flow_to_record(resp.flow))

                    now = asyncio.get_event_loop().time()
                    if len(pending) >= batch_size or now - last_flush >= batch_timeout_s:
                        await buffer.insert_flows(pending)
                        pending = []
                        last_flush = now

                if pending:
                    await buffer.insert_flows(pending)

                # Stream ended cleanly. Reset backoff before reconnecting.
                backoff = 1.0
        except asyncio.CancelledError:
            raise
        except grpc.aio.AioRpcError as exc:
            log.warning("hubble stream rpc error: %s; backoff %.1fs", exc.code(), backoff)
        except Exception:
            log.exception("hubble stream crashed")

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30.0)
