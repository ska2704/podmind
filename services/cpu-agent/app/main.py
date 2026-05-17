"""CPU agent entrypoint. Polls the ingestor, scores, publishes."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from redis.asyncio import Redis

from .config import Config
from .poller import Poller
from .publisher import Publisher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("cpu-agent")


config = Config.from_env()


class _State:
    poller: Poller | None = None
    redis: Redis | None = None


state = _State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.redis = Redis.from_url(config.redis_url, decode_responses=True)
    # Verify Redis is reachable before we start advertising readiness.
    await state.redis.ping()

    publisher = Publisher(client=state.redis, channel=config.findings_channel)
    state.poller = Poller(config=config, publisher=publisher)

    log.info(
        "cpu-agent up: ingestor=%s redis=%s channel=%s "
        "poll=%.1fs window=%ds min_samples=%d threshold=%.2f",
        config.ingestor_url,
        config.redis_url,
        config.findings_channel,
        config.poll_interval_s,
        config.window_s,
        config.min_samples,
        config.anomaly_threshold,
    )

    task = asyncio.create_task(state.poller.run_forever(), name="cpu-agent-poll")
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if state.redis is not None:
            await state.redis.aclose()


app = FastAPI(title="podmind-cpu-agent", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    if state.poller is None or state.redis is None:
        raise HTTPException(503, "not initialised")
    return {"status": "ready", "pods_watched": len(state.poller.windows)}
