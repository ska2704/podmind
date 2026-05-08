import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException, Query

from . import hubble, prometheus
from .buffer import Buffer
from .config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("ingestor")


config = Config.from_env()
buffer = Buffer(config.buffer_path)


async def _sweep_forever() -> None:
    while True:
        await asyncio.sleep(config.sweep_interval_s)
        try:
            deleted = await buffer.sweep(datetime.now(UTC), config.buffer_window_s)
            if deleted:
                log.debug("buffer swept %d rows", deleted)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("buffer sweep failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await buffer.init()
    log.info(
        "ingestor up: prom=%s hubble=%s buffer=%s",
        config.prom_url,
        config.hubble_relay_addr,
        config.buffer_path,
    )

    tasks = [
        asyncio.create_task(
            prometheus.poll_forever(buffer, config.prom_url, config.poll_interval_s),
            name="prom-poll",
        ),
        asyncio.create_task(
            hubble.stream_forever(buffer, config.hubble_relay_addr),
            name="hubble-stream",
        ),
        asyncio.create_task(_sweep_forever(), name="sweep"),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await buffer.close()


app = FastAPI(title="podmind-ingestor", lifespan=lifespan)


def _parse_since(since: str) -> datetime:
    """Accept '-Ns' (relative), an ISO-8601 datetime, or a unix timestamp."""
    s = since.strip()
    if s.startswith("-") and s.endswith("s"):
        try:
            return datetime.now(UTC) - timedelta(seconds=int(s[1:-1]))
        except ValueError as exc:
            raise HTTPException(400, f"bad relative since: {since!r}") from exc
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    try:
        return datetime.fromtimestamp(float(s), tz=UTC)
    except ValueError as exc:
        raise HTTPException(400, f"bad since: {since!r}") from exc


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    if not buffer.is_open:
        raise HTTPException(503, "buffer not initialised")
    return {"status": "ready"}


@app.get("/buffer/metrics")
async def get_metrics(
    since: str = Query("-30s"),
    pod: str | None = None,
    name: str | None = None,
    namespace: str | None = None,
):
    rows = await buffer.query_metrics(
        _parse_since(since),
        pod=pod,
        name=name,
        namespace=namespace,
    )
    return {"count": len(rows), "rows": [r.model_dump(mode="json") for r in rows]}


@app.get("/buffer/flows")
async def get_flows(
    since: str = Query("-30s"),
    src: str | None = None,
    dst: str | None = None,
):
    rows = await buffer.query_flows(_parse_since(since), src=src, dst=dst)
    return {"count": len(rows), "rows": [r.model_dump(mode="json") for r in rows]}
