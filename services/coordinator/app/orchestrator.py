"""Deterministic /ask orchestration for pod-specific questions.

The autonomous tool-calling loop in `llm.py` is creative but unreliable
on small models (qwen2.5:1.5b calls all three tools ~25% of the time
in our measurements). For the recorded demo we want guaranteed
3-tool coverage on questions that name a pod. This module:

1. Extracts a pod name from the question by matching the leading
   "short-name" portion of every pod we've seen recently (findings
   cache + ingestor metric rows) against the question text.

2. If a pod is identified, runs all three tools in parallel against
   it and asks the model for a single prose answer using the
   pre-computed JSON as context. No tool loop, no autonomy.

3. If no pod is identified, the caller falls back to the autonomous
   path (good for cluster-wide questions like "what is broken
   right now?").
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

from .config import Config
from .findings_cache import FindingsCache
from .tools import get_pod_metrics, get_pod_neighbors, get_recent_anomalies

log = logging.getLogger(__name__)

# Matches the trailing "-<replica-set-hash>-<pod-hash>" pattern that
# Kubernetes appends to Deployment-managed pod names. Replica-set
# hashes are typically 8-10 hex chars; pod-name hashes are 5 chars
# from base36-ish alphabet.
_POD_SUFFIX_RE = re.compile(r"-[0-9a-f]{7,10}-[0-9a-z]{5}$")


# System prompt for the deterministic path. Tighter than the autonomous
# system prompt because we don't need to argue with the model about
# tool calling — we've already pre-computed everything.
DETERMINISTIC_SYSTEM_PROMPT = (
    "You are PodMind, a Kubernetes observability assistant. The user has "
    "asked about a pod. Here is the data we have gathered. Respond in "
    "plain English prose (three to five sentences, not lists, not JSON). "
    "Reference specific values from the data — anomaly scores, CPU rates, "
    "neighbour pod names. Be concrete. If the data shows no recent "
    "anomalies, say so plainly."
)


def _short_name(pod_full: str) -> str:
    """gateway-68ddb457d-754vj -> gateway"""
    return _POD_SUFFIX_RE.sub("", pod_full)


async def collect_known_pods(
    *,
    client: httpx.AsyncClient,
    ingestor_url: str,
    metric_query: str,
    cache: FindingsCache,
    since_s: int = 600,
) -> dict[str, str]:
    """Build a map from short-name -> latest-observed full pod name,
    drawing from the findings cache and the ingestor's recent metrics."""
    shorts: dict[str, str] = {}

    for f in cache.get_recent(since_s=since_s):
        shorts[_short_name(f.pod)] = f.pod

    try:
        r = await client.get(
            f"{ingestor_url}/buffer/metrics",
            params={"since": f"-{since_s}s", "name": metric_query},
            timeout=5.0,
        )
        r.raise_for_status()
        for row in r.json().get("rows", []):
            full = row.get("pod")
            if full:
                shorts[_short_name(full)] = full
    except Exception as exc:
        # Don't fail extraction just because the ingestor is briefly
        # unavailable — cache may still give us something.
        log.warning("collect_known_pods: ingestor query failed: %s", exc)

    return shorts


def extract_pod_short(question: str, shorts: dict[str, str]) -> str | None:
    """Return the longest short-name that appears as a case-insensitive
    substring of the question, or None.

    Prefers longer matches so "auth-server" beats a generic "auth" if
    both are present in the cluster.
    """
    qlow = question.lower()
    hits = [s for s in shorts if s.lower() in qlow]
    if not hits:
        return None
    hits.sort(key=lambda s: -len(s))
    return hits[0]


async def deterministic_ask(
    *,
    config: Config,
    client: httpx.AsyncClient,
    cache: FindingsCache,
    question: str,
    pod_short: str,
) -> dict[str, Any]:
    """Run the three tools in parallel against `pod_short`, then make a
    single non-tool LLM call to summarise the results."""

    anomaly_args = {"pod": pod_short, "since_s": 300}
    metric_args = {"pod": pod_short, "since_s": 180}
    neighbor_args = {"pod": pod_short, "since_s": 180}

    anomalies, metrics, neighbors = await asyncio.gather(
        get_recent_anomalies(cache=cache, **anomaly_args),
        get_pod_metrics(
            client=client,
            ingestor_url=config.ingestor_url,
            metric_query=config.default_metric_query,
            **metric_args,
        ),
        get_pod_neighbors(
            client=client,
            ingestor_url=config.ingestor_url,
            **neighbor_args,
        ),
    )

    user_content = (
        f"Question: {question}\n\n"
        f"Recent anomalies:\n{json.dumps(anomalies, default=str)}\n\n"
        f"Recent metrics:\n{json.dumps(metrics, default=str)}\n\n"
        f"Network neighbors:\n{json.dumps(neighbors, default=str)}"
    )

    payload = {
        "model": config.model_name,
        "messages": [
            {"role": "system", "content": DETERMINISTIC_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
    }
    r = await client.post(
        f"{config.ollama_url}/api/chat",
        json=payload,
        timeout=60.0,
    )
    r.raise_for_status()
    msg = r.json().get("message") or {}
    answer = (msg.get("content") or "").strip()

    tools_called = [
        {"name": "get_recent_anomalies", "arguments": anomaly_args},
        {"name": "get_pod_metrics", "arguments": metric_args},
        {"name": "get_pod_neighbors", "arguments": neighbor_args},
    ]
    return {"answer": answer, "tools_called": tools_called}
