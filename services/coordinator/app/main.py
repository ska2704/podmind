"""Coordinator entrypoint.

POST /ask  — natural-language question; coordinator runs the LLM tool loop.
GET  /healthz — 200 always; the pod is alive.
GET  /readyz  — 200 when Redis subscriber is wired and Ollama answered tags.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis

from .config import Config
from .findings_cache import FindingsCache, run_subscriber
from .llm import ask as llm_ask
from .orchestrator import collect_known_pods, deterministic_ask, extract_pod_short
from .tools import TOOL_SCHEMAS, dispatch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("coordinator")


config = Config.from_env()


class _State:
    redis: Redis | None = None
    cache: FindingsCache | None = None
    sub_task: asyncio.Task | None = None
    http: httpx.AsyncClient | None = None
    ollama_reachable: bool = False


state = _State()


async def _ping_ollama(client: httpx.AsyncClient) -> bool:
    try:
        r = await client.get(f"{config.ollama_url}/api/tags", timeout=2.0)
        r.raise_for_status()
        return True
    except Exception as exc:
        log.warning("ollama unreachable at startup: %s", exc)
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.cache = FindingsCache(size=config.findings_cache_size)
    state.redis = Redis.from_url(config.redis_url, decode_responses=False)
    state.http = httpx.AsyncClient()

    # Redis: ping but don't crash if briefly unreachable — the subscriber
    # loop reconnects with backoff.
    try:
        await state.redis.ping()
        log.info("redis ping ok at %s", config.redis_url)
    except Exception as exc:
        log.warning("redis ping failed at startup: %s — subscriber will retry", exc)

    state.ollama_reachable = await _ping_ollama(state.http)
    if state.ollama_reachable:
        log.info("ollama reachable at %s, model=%s", config.ollama_url, config.model_name)
    else:
        log.warning(
            "ollama NOT reachable at %s — /ask will return 503 until it is",
            config.ollama_url,
        )

    state.sub_task = asyncio.create_task(
        run_subscriber(state.redis, config.findings_channel, state.cache),
        name="findings-subscriber",
    )

    log.info(
        "coordinator up: ingestor=%s redis=%s ollama=%s channel=%s cache=%d",
        config.ingestor_url,
        config.redis_url,
        config.ollama_url,
        config.findings_channel,
        config.findings_cache_size,
    )

    try:
        yield
    finally:
        if state.sub_task is not None:
            state.sub_task.cancel()
            await asyncio.gather(state.sub_task, return_exceptions=True)
        if state.http is not None:
            await state.http.aclose()
        if state.redis is not None:
            await state.redis.aclose()


app = FastAPI(title="podmind-coordinator", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    tools_called: list[dict[str, Any]]


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    if state.cache is None or state.http is None:
        raise HTTPException(503, "not initialised")
    # Re-check Ollama on every readiness probe so the pod recovers
    # automatically once the host daemon comes back up.
    state.ollama_reachable = await _ping_ollama(state.http)
    if not state.ollama_reachable:
        raise HTTPException(503, f"ollama unreachable at {config.ollama_url}")
    return {
        "status": "ready",
        "findings_cached": len(state.cache),
        "ollama": config.ollama_url,
    }


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    """Dispatch on whether we can pin down a specific pod in the question:

    - If the question mentions a known pod (by its short Deployment name),
      run the deterministic 3-tool path. This is the production-shape
      call: guaranteed coverage, no autonomy.
    - If we can't pin down a pod, fall back to the autonomous LLM tool
      loop — useful for cluster-wide questions like "what is broken
      right now?" where the model needs to discover the subject itself.
    """
    if state.http is None or state.cache is None:
        raise HTTPException(503, "coordinator not initialised")
    if not state.ollama_reachable:
        state.ollama_reachable = await _ping_ollama(state.http)
        if not state.ollama_reachable:
            raise HTTPException(503, f"ollama unreachable at {config.ollama_url}")

    cache = state.cache
    http = state.http

    try:
        shorts = await collect_known_pods(
            client=http,
            ingestor_url=config.ingestor_url,
            metric_query=config.default_metric_query,
            cache=cache,
        )
        pod_short = extract_pod_short(req.question, shorts)

        if pod_short is not None:
            log.info("ask: deterministic path, pod=%s", pod_short)
            result = await deterministic_ask(
                config=config,
                client=http,
                cache=cache,
                question=req.question,
                pod_short=pod_short,
            )
        else:
            log.info("ask: autonomous path, no pod identified in question")

            async def _dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
                return await dispatch(
                    name,
                    args,
                    client=http,
                    cache=cache,
                    ingestor_url=config.ingestor_url,
                    metric_query=config.default_metric_query,
                )

            result = await llm_ask(
                client=http,
                ollama_url=config.ollama_url,
                model=config.model_name,
                question=req.question,
                tools=TOOL_SCHEMAS,
                dispatch=_dispatch,
                max_rounds=config.max_tool_rounds,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"upstream error: {exc}") from exc
    return AskResponse(**result)
