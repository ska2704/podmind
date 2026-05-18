"""The three tools exposed to the LLM. Each is a small async function
returning a JSON-serializable dict. The tool schemas at the bottom of
this module are passed to Ollama via /api/chat.

Tool descriptions are written DEFENSIVELY against the model's common
failure modes:
- `since_s=0` is documented as "returns nothing useful" because 1.5B
  models otherwise pass 0 for "right now."
- `pod` parameters note that substring match works against the random
  Deployment-suffix problem (gateway-68ddb457d-754vj etc.) so the
  model doesn't have to invent UUIDs.
"""

from __future__ import annotations

import logging
import statistics
from typing import Any

import httpx

from .findings_cache import FindingsCache

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Implementations


async def get_pod_metrics(
    *,
    client: httpx.AsyncClient,
    ingestor_url: str,
    metric_query: str,
    pod: str,
    since_s: int = 120,
) -> dict[str, Any]:
    """Fetch recent samples of the configured CPU rate metric for a
    pod, return a small summary."""
    if since_s <= 0:
        since_s = 120
    r = await client.get(
        f"{ingestor_url}/buffer/metrics",
        params={"since": f"-{since_s}s", "name": metric_query},
        timeout=5.0,
    )
    r.raise_for_status()
    rows = r.json().get("rows", [])

    # Substring match — the deployment-suffix problem
    matches = [row for row in rows if pod and pod in (row.get("pod") or "")]
    if not matches:
        return {
            "pod_query": pod,
            "metric": "cpu_rate",
            "since_s": since_s,
            "sample_count": 0,
            "samples": [],
            "summary": None,
            "note": f"No samples for any pod containing {pod!r} in the last {since_s}s.",
        }

    matches.sort(key=lambda r: r.get("ts") or "")
    values = [float(row["value"]) for row in matches if row.get("value") is not None]
    samples_compact = [
        {"ts": row["ts"], "value": float(row["value"])}
        for row in matches[-30:]  # cap to the last 30 to keep prompt small
    ]
    summary = {
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "current": values[-1],
    }
    return {
        "pod_query": pod,
        "pod_match": matches[-1].get("pod"),
        "namespace": matches[-1].get("namespace"),
        "metric": "cpu_rate",
        "since_s": since_s,
        "sample_count": len(values),
        "samples": samples_compact,
        "summary": summary,
    }


async def get_recent_anomalies(
    *,
    cache: FindingsCache,
    pod: str | None = None,
    since_s: int = 300,
) -> dict[str, Any]:
    if since_s <= 0:
        since_s = 300
    findings = cache.get_recent(pod_substring=pod, since_s=since_s)
    return {
        "pod_query": pod,
        "since_s": since_s,
        "count": len(findings),
        "findings": [f.model_dump(mode="json") for f in findings],
    }


async def get_pod_neighbors(
    *,
    client: httpx.AsyncClient,
    ingestor_url: str,
    pod: str,
    since_s: int = 120,
) -> dict[str, Any]:
    """Who talks to this pod and who does it talk to?

    Hubble emits each connection as two half-flows when traffic goes
    through a service VIP under socketLB (see podmind-brief.md): one
    half has the sender identified (TO_STACK), the other has the
    receiver (TO_ENDPOINT). Neither half on its own names both ends.
    We pair halves by the (src_port, dst_port) 5-tuple to recover the
    real neighbour identity.

    Substring match on the pod name so a query for "hvac-controller"
    finds "hvac-controller-cff74f845-m6qvk".
    """
    if since_s <= 0:
        since_s = 120
    r = await client.get(
        f"{ingestor_url}/buffer/flows",
        params={"since": f"-{since_s}s"},
        timeout=5.0,
    )
    r.raise_for_status()
    rows = r.json().get("rows", [])

    def _matches(p: str | None) -> bool:
        return bool(p) and pod in p

    # Direct (both-sides populated) — happens for direct pod-IP traffic.
    talks_to: dict[str, int] = {}
    talked_to_by: dict[str, int] = {}
    for row in rows:
        src = row.get("src_pod") or ""
        dst = row.get("dst_pod") or ""
        if _matches(src) and dst and dst != src:
            talks_to[dst] = talks_to.get(dst, 0) + 1
        if _matches(dst) and src and src != dst:
            talked_to_by[src] = talked_to_by.get(src, 0) + 1

    # socketLB half-flow pairing.
    # Inbound to our pod: TO_ENDPOINT rows where dst=our pod. The
    # caller appears in TO_STACK rows with a matching (src_port,
    # dst_port) — that is the OTHER pod's view of the same packet
    # before the host SNAT obscured its identity.
    inbound_keys = {
        (row.get("src_port"), row.get("dst_port"))
        for row in rows
        if row.get("observation_point") == "TO_ENDPOINT" and _matches(row.get("dst_pod"))
    }
    for row in rows:
        if row.get("observation_point") != "TO_STACK":
            continue
        key = (row.get("src_port"), row.get("dst_port"))
        if key not in inbound_keys:
            continue
        src = row.get("src_pod")
        if not src or _matches(src):
            continue
        talked_to_by[src] = talked_to_by.get(src, 0) + 1

    # Outbound from our pod: TO_STACK rows where src=our pod. The
    # receiver appears in TO_ENDPOINT rows with matching ports.
    outbound_keys = {
        (row.get("src_port"), row.get("dst_port"))
        for row in rows
        if row.get("observation_point") == "TO_STACK" and _matches(row.get("src_pod"))
    }
    for row in rows:
        if row.get("observation_point") != "TO_ENDPOINT":
            continue
        key = (row.get("src_port"), row.get("dst_port"))
        if key not in outbound_keys:
            continue
        dst = row.get("dst_pod")
        if not dst or _matches(dst):
            continue
        talks_to[dst] = talks_to.get(dst, 0) + 1

    def _top(d: dict[str, int], n: int = 5) -> list[dict[str, Any]]:
        return [
            {"pod": p, "flow_count": c}
            for p, c in sorted(d.items(), key=lambda kv: -kv[1])[:n]
        ]

    return {
        "pod_query": pod,
        "since_s": since_s,
        "talks_to": _top(talks_to),
        "talked_to_by": _top(talked_to_by),
        "note": (
            "Neighbours are recovered by pairing socketLB half-flows on "
            "(src_port, dst_port). flow_count is the number of matched "
            "half-flow pairs, not unique connections."
        ),
    }


# ---------------------------------------------------------------------
# Tool dispatcher used by the LLM loop


async def dispatch(
    name: str,
    args: dict[str, Any],
    *,
    client: httpx.AsyncClient,
    cache: FindingsCache,
    ingestor_url: str,
    metric_query: str,
) -> dict[str, Any]:
    if name == "get_pod_metrics":
        return await get_pod_metrics(
            client=client,
            ingestor_url=ingestor_url,
            metric_query=metric_query,
            pod=str(args.get("pod", "")),
            since_s=int(args.get("since_s", 120)),
        )
    if name == "get_recent_anomalies":
        return await get_recent_anomalies(
            cache=cache,
            pod=args.get("pod"),
            since_s=int(args.get("since_s", 300)),
        )
    if name == "get_pod_neighbors":
        return await get_pod_neighbors(
            client=client,
            ingestor_url=ingestor_url,
            pod=str(args.get("pod", "")),
            since_s=int(args.get("since_s", 120)),
        )
    return {"error": f"unknown tool {name!r}"}


# ---------------------------------------------------------------------
# Ollama-side tool schemas (OpenAI-style function descriptors)
#
# Defensive descriptions: explicit on since_s ranges, substring matching
# for pod names, and the fact that 0 returns nothing useful. Without
# this, qwen2.5:1.5b cheerfully passes since_s=0 for "right now."

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_recent_anomalies",
            "description": (
                "Get recent anomaly Findings published by the cluster's agents. "
                "Call this FIRST when a user asks about a pod or about cluster "
                "health, before drilling into specific metrics. Returns a list "
                "of Findings, each with pod, namespace, anomaly_score, "
                "current_value, and a baseline_window_summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pod": {
                        "type": "string",
                        "description": (
                            "Optional pod-name substring filter. Pod names have "
                            "random Deployment suffixes (e.g. "
                            "'hvac-controller-cff74f845-czxng'); pass just "
                            "'hvac-controller' to match all replicas. Omit to "
                            "see anomalies across the whole cluster."
                        ),
                    },
                    "since_s": {
                        "type": "integer",
                        "description": (
                            "Lookback window in seconds. Use 60-300 for recent "
                            "context. Do NOT pass 0 — that returns no data."
                        ),
                        "default": 300,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pod_metrics",
            "description": (
                "Fetch CPU rate samples for a pod from the metrics buffer, "
                "with a min/max/mean/current summary. Call after "
                "get_recent_anomalies to get specific numbers for the pod in "
                "question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pod": {
                        "type": "string",
                        "description": (
                            "Pod name; substring match is supported so "
                            "'hvac-controller' matches "
                            "'hvac-controller-cff74f845-czxng'."
                        ),
                    },
                    "since_s": {
                        "type": "integer",
                        "description": (
                            "Lookback window in seconds. Use 60-300 for recent "
                            "context. Do NOT pass 0 — that returns no data."
                        ),
                        "default": 120,
                    },
                },
                "required": ["pod"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pod_neighbors",
            "description": (
                "List which pods this pod is talking to and which pods are "
                "talking to it (the blast radius). Use this to understand "
                "what else might be affected if the pod is in trouble. "
                "Returned counts are flow rows, not unique connections."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pod": {
                        "type": "string",
                        "description": (
                            "Pod name substring; matches both src_pod and "
                            "dst_pod in the flow buffer."
                        ),
                    },
                    "since_s": {
                        "type": "integer",
                        "description": (
                            "Lookback window in seconds. Use 60-300 for recent "
                            "context. Do NOT pass 0 — that returns no data."
                        ),
                        "default": 120,
                    },
                },
                "required": ["pod"],
            },
        },
    },
]
