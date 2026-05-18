"""Ollama wrapper + tool-calling loop.

Ollama exposes /api/chat with native tool calling (since v0.3). The
request body is OpenAI-shaped: `messages` is a list of role/content/
optional tool_calls dicts, `tools` is the list of function descriptors.

The model can either:
  (a) emit `message.content` only — final answer, we return it.
  (b) emit `message.tool_calls` — we run each, append a role=tool
      message with the JSON result keyed by tool_call_id, and ask
      the model again.

We bound this loop at `max_rounds` so a misbehaving model can't pin a
worker forever. Each round logs the tool name + args + brief result
shape so the smoke test can show which tools were used.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)


ToolDispatch = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


SYSTEM_PROMPT = (
    "You are PodMind, a Kubernetes observability assistant. When a user "
    "asks about a pod or service, you MUST call ALL THREE tools — "
    "get_recent_anomalies, then get_pod_metrics, then get_pod_neighbors — "
    "before producing your final answer. Do not answer in prose until you "
    "have invoked all three. After all three tools have returned, write a "
    "single paragraph in plain English (not lists, not JSON, not 'Next I "
    "will...') of three to five sentences. The paragraph must reference "
    "specific numbers from the tool outputs (an anomaly score or a CPU "
    "value) and name at least one neighbour pod from get_pod_neighbors. "
    "When passing since_s, use values between 60 and 300 — calling with "
    "since_s=0 returns nothing useful."
)


class LLMError(RuntimeError):
    pass


async def ask(
    *,
    client: httpx.AsyncClient,
    ollama_url: str,
    model: str,
    question: str,
    tools: list[dict[str, Any]],
    dispatch: ToolDispatch,
    max_rounds: int = 5,
) -> dict[str, Any]:
    """Run the tool-calling loop until the model produces a final
    content-only answer, or we hit max_rounds.

    Returns {"answer": str, "tools_called": [{"name": ..., "arguments": ...}, ...]}.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    tools_called: list[dict[str, Any]] = []

    for round_idx in range(max_rounds):
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False,
        }
        r = await client.post(
            f"{ollama_url}/api/chat",
            json=payload,
            timeout=60.0,
        )
        r.raise_for_status()
        data = r.json()
        msg = data.get("message") or {}
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            log.info(
                "llm: final answer after %d tool round(s); model eval_count=%s",
                round_idx,
                data.get("eval_count"),
            )
            return {"answer": content.strip(), "tools_called": tools_called}

        # Append the assistant turn that asked for tools.
        messages.append(
            {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }
        )

        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            args_raw = fn.get("arguments")
            # Ollama returns arguments as a dict already; some clients send a
            # JSON string. Normalize.
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw)
                except json.JSONDecodeError:
                    args = {}
            elif isinstance(args_raw, dict):
                args = args_raw
            else:
                args = {}
            tools_called.append({"name": name, "arguments": args})
            log.info("llm: round=%d tool=%s args=%s", round_idx, name, args)
            try:
                result = await dispatch(name, args)
            except Exception as exc:
                log.exception("llm: tool %s failed", name)
                result = {"error": f"tool execution failed: {exc!s}"}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id") or name,
                    "content": json.dumps(result, default=str),
                }
            )

    raise LLMError(
        f"tool-calling loop exceeded {max_rounds} rounds without a final answer"
    )
