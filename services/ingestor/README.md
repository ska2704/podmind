# podmind-ingestor

FastAPI service that polls Prometheus + Hubble Relay into a 5-minute
SQLite rolling buffer. Agents and the coordinator query this buffer
instead of hammering Prometheus directly.

## Running locally

```
uv run --package podmind-ingestor uvicorn app.main:app --reload
```

Config is via env vars (see `app/config.py`). Sane defaults aim at the
in-cluster deployment, so for local runs you'll want at least:

```
PROM_URL=http://localhost:9090
HUBBLE_RELAY_ADDR=localhost:4245
BUFFER_PATH=./buffer.sqlite
```

## Endpoints

- `GET /healthz` — liveness
- `GET /readyz`  — buffer is initialised
- `GET /buffer/metrics?since=-30s&pod=&name=&namespace=`
- `GET /buffer/flows?since=-30s&src=&dst=`

`since` accepts `-Ns` (relative seconds), an ISO-8601 datetime, or a
unix timestamp.

## Tests

```
uv run --package podmind-ingestor pytest
```

## Proto stubs

Hubble's gRPC stubs live under `app/_proto/`. They're committed; to
regenerate after editing `_proto/observer.proto`:

```
make protos
```
