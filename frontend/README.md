# PodMind dashboard

Three-column React/Vite dashboard for the PodMind cluster: pod list,
dependency graph, /ask chat panel, per-pod CPU time-series.

The dashboard is a single-page localhost app — no auth, no routing,
no mobile. It's optimised for screen-recording at 1440×900 or 1080p.

## Running in dev

Two terminals:

```bash
# 1. Forward the in-cluster services to localhost.
#    Leaves itself attached; Ctrl-C kills both forwards via trap.
./scripts/dev-ports.sh

# 2. Start Vite with hot-reload.
npm run dev
```

Vite proxies:

| URL prefix        | Upstream                |
| ----------------- | ----------------------- |
| `/api/buffer/*`   | `http://localhost:8000` |
| `/api/ask`        | `http://localhost:8001` |
| `/api/findings/*` | `http://localhost:8001` |

The proxy strips the `/api` prefix so backend routes stay
`/buffer/flows`, `/ask`, `/findings/recent` on the wire.

## Layout

```
+------------+--------------------------+-----------+
|   POD LIST |    DEPENDENCY GRAPH     |   CHAT    |
|  (280px)   |       (1fr)              | (360px)   |
+------------+--------------------------+-----------+
|             FOOTER · TIME SERIES (200px)         |
+--------------------------------------------------+
```

Polling cadence (all via TanStack Query):

- `getFlows`            every 3 s
- `getMetrics`          every 5 s (selected pod only)
- `getRecentFindings`   every 2 s

Findings cross the wire as HTTP polls, not Redis pub/sub — the
browser never speaks Redis.
