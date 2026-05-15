# PodMind — Project Brief

AI-native observability for single-node Kubernetes that answers *why*, not just
*what*. Built for the "Beyond Monitoring" competition. Single-node K3s,
edge/industrial framing, zero cloud dependencies.

## North Star

Existing tools tell you *what* spiked. PodMind tells you *what caused it*,
*what's about to fail*, and *what to do about it* — using eBPF for ground-truth
dependency discovery, causal inference (not just correlation) across pod metrics,
and a multi-agent system coordinated by a local LLM.

## Architecture

**Data plane:** K3s single-node, SmartHostel demo app (10 microservices across
3-4 namespaces, with PVCs and inter-service HTTP). Cilium + Hubble for eBPF flows.
Prometheus + node_exporter + cAdvisor for metrics.

**Ingestion:** FastAPI service polls Hubble + Prometheus on 1s cadence, writes to
a SQLite rolling buffer (5-min window).

**Agents (separate Python processes, publish to Redis pub/sub, never talk to
each other directly):**
- CPU Agent — Isolation Forest + change-point detection on CPU/throttling
- Memory Agent — RSS slope analysis, OOMKill prediction
- Storage/PVC Agent — I/O latency, fsync stalls, restart correlation
- Network/IO Agent — eBPF flows, retransmits, connection churn

**Causal layer:** `tigramite` PCMCI on the rolling buffer → directed edges with
lag and confidence, overlaid on the dependency graph.

**Coordinator Agent:** Local Qwen2.5-3B via Ollama. Tool-calling loop with:
`get_pod_metrics`, `get_causal_parents`, `get_recent_anomalies`,
`get_dependency_neighbors`. Endpoints: `/ask` (NLP), `/explain` (graph click).

**Frontend:** React + TypeScript + Tailwind. D3 force-directed graph
(centerpiece), Recharts time series, chat panel right-docked. Edge color =
causal vs observed. Node color = agent-reported health.

**Demo:** Chaos Mesh, 4 pre-written experiments (CPU stress, slow-disk PVC,
memory leak, network partition), one-click admin panel.

## Tiers

- **Tier 1 (must ship):** eBPF dep graph + CPU Agent + Coordinator + dashboard
- **Tier 2 (target):** + Storage + Memory agents + causal overlay
- **Tier 3 (wow):** + Network agent + LSTM forecasting (90s OOMKill prediction)

Never sacrifice Tier 1 quality for Tier 2 features.

## Repo Layout

podmind/
├── infra/              # K3s, Cilium, Prometheus, Chaos Mesh, SmartHostel manifests
├── services/
│   ├── smarthostel/    # 10 demo microservices (FastAPI + SQLite each)
│   ├── ingestor/       # Metrics + Hubble flow ingestion
│   ├── contracts/      # Pydantic models shared across services
│   ├── agents/{cpu,memory,storage,network}/
│   ├── causal/         # PCMCI pipeline
│   └── coordinator/    # Ollama + tool-calling loop
├── frontend/           # React + D3 + Recharts
├── scripts/            # dev-up.sh, demo runner
└── docs/               # Architecture, report drafts

## Non-negotiables

- No cloud LLM. Coordinator is local Ollama only (matches edge framing).
- No Grafana — we build our own UI.
- Single-node only.
- Python 3.11 + `uv` for backend. Node 20 + TypeScript for frontend.
- FastAPI everywhere. Pydantic v2. Async by default. Type hints mandatory.
- Conventional commits.

## What NOT to build

K8s operators, multi-cluster anything, dashboard auth, custom TSDB, CI/CD,
service mesh beyond Cilium, Helm charts for our own services.


## Current Status — Day 1 (2026-05-08)

### Landed

- **`services/contracts/`** (9 files) — Pydantic v2 models for `MetricRecord`, `HubbleFlow`, `Finding`, and the four coordinator tool-call schemas. Frozen, `extra="forbid"`. Workspace path-dep, no I/O, single runtime dep (`pydantic`).
- **`services/ingestor/`** (18 files) — FastAPI app: 1s Prometheus instant-query poller, Hubble Relay `Observer.GetFlows` streamer, 5-minute SQLite/WAL rolling buffer with 30s TTL sweeper, `/buffer/metrics` and `/buffer/flows` query endpoints, `/healthz` and `/readyz`. Vendored Hubble proto + committed gRPC stubs. Dockerfile.
- **`infra/smarthostel/`** (41 manifests) — three namespaces (`sh-core`, `sh-edge`, `sh-ops`), 10 service directories each with `deployment.yaml` / `service.yaml` / `pvc.yaml` / `kustomization.yaml`, `nginx:alpine` placeholders, default 50m/64Mi requests with 500m/512Mi bumps on `sensor-ingest` and `energy-meter`.
- **`infra/guest-sim/`** (4 files) — single Deployment in `sh-core` with stdlib-only Python sim mounted from a ConfigMap; tunable knobs (login rate, booking Poisson lambda, check-out flood interval) in a separate `guest-sim-knobs` ConfigMap. Generates guest traffic only — sensor data stays with `sensor-ingest`'s internal ticker.
- **`infra/ingestor/`** (5 files) — own `podmind` namespace, 1Gi buffer PVC, Recreate strategy, Prometheus alias service so `PROM_URL` doesn't depend on the helm release name.
- **Top-level `Makefile`** — `protos`, `up`, `down`, `test`, `lint`. `down` uses `--ignore-not-found=true` so partial teardowns still run.
- **`scripts/dev-up.sh`** — idempotent: K3s install, cilium-cli + Cilium with Hubble, kube-prometheus-stack via Helm, all three Kustomize stacks, ingestor image build + `k3s ctr import`, wait-for-Available across every namespace.
- **`docs/architecture.md`** — two-pager with the canonical Mermaid diagram, data-flow narrative, tech-choice justifications, and Tier 1/2/3 boundaries.

### Tests

- **28/28 passing** — 11 contracts (round-trip JSON, frozen, `extra="forbid"`, Literal validation), 17 ingestor (buffer insert/query/sweep, Prometheus parser including NaN/Inf handling, ASGI smoke for the buffer endpoints).
- **`ruff` clean** across `services/`.

### Design decisions resolved this round

| Decision | Outcome |
|----------|---------|
| Call-graph cycle | `hvac-controller ⇄ energy-meter` (HVAC reports load, meter publishes a per-room budget signal back). Not `booking ⇄ notifications`. |
| Auth pattern | Gateway does **not** call auth. Auth is a high fan-in leaf called by `booking` and `lock-controller` — services that touch guest-bound state validate the token themselves. |
| Billing back-edge | Removed. Booking pushes full booking context into the create-invoice payload; billing only calls `energy-meter`. |
| Sensor traffic source | `sensor-ingest`'s internal ticker is the only sensor source. Guest-sim generates logins / bookings / check-out floods only. |
| Prometheus poll shape | `/api/v1/query` instant query on the 1s tick — one sample per series per call, no overlapping windows. Range queries are reserved for agents doing their own lookback against Prometheus directly. |
| Hubble wire format | `services/ingestor/_proto/observer.proto` is a vendored minimal subset of the upstream cilium/cilium proto tree, with field numbers matched to upstream. Full upstream proto can be dropped in later without code changes. |
| Auth crypto realism | HMAC-signed token stub, stdlib only. No `python-jose` or similar. |
| gRPC client | `grpcio` + checked-in stubs from the vendored proto. No `hubble` Python package. |

### Outstanding caveats

- The 10 SmartHostel services are still **`nginx:alpine` placeholders**. The contracts and the dependency graph are pinned down; the application logic for each service is not.
- `kube-prometheus-stack` is used as-is via Helm. If we hit upstream churn there, a thinner manual Prometheus deployment is the fallback.

## Bring-up phase — CLOSED (2026-05-15)

End-to-end pipeline verified against a live single-node k3d + Cilium 1.19.1 + Hubble cluster. Stage 4 smoke test:

```
total flows in /buffer/flows?since=-60s : 3201
gateway -> booking rows                 : 21
rows with pod identity                  : 1743 / 3201
verdict distribution                    : FORWARDED=3199, DROPPED=2
l4 distribution                         : TCP=1567, UDP=180, <none>=1454
```

Every gateway → booking row carries full src/dst pod name, namespace, L4 protocol, source/destination ports. The ingestor is reading Hubble via the cilium-agent's local unix socket, the proto stubs decode upstream Cilium 1.19.1 flow shape correctly, and the buffer's query API returns the right rows. Tagged `bring-up-complete` in git.

### Bring-up — what actually happened

Three issues fought us, in order:

1. **k3d + Cilium kube-proxy replacement = broken host→apiserver.** Cilium's eBPF datapath on the k3d server container disrupts the docker-bridge port forwarding the host uses to reach the API. Fixed by dropping kpr and letting k3s's built-in kube-proxy keep doing service routing. Loses no Tier-1 capability (Hubble flow capture doesn't require kpr).
2. **hubble-relay on k3d flaps on pod-to-agent-host-IP direct dial.** Relay's peer-discovery announces the agent's host IP; from the relay's pod netns that address gets TCP `connection refused` on the next dial cycle. Sidestepped by talking to the cilium-agent's local unix socket directly — single-node-clean, multi-node-ready via one env var flip.
3. **Vendored `observer.proto` had wrong upstream field numbers.** Hand-typed minimal subset diverged from upstream Cilium 1.19.1 on `Flow.l4`, `Flow.source`, `Flow.destination`, `Endpoint.namespace`, `Endpoint.pod_name`. Result: 6886 flows captured cleanly but all decoded with `src_pod=None / dst_pod=None`. Fixed by pulling the upstream `v1.19.1` tag's `flow.proto` and patching tags.

### Follow-ups / known operational hazards

- **Hubble Relay on k3d (Docker-in-Docker) is fragile across cluster
  restarts.** The agent's peer-discovery announces its own host IP, and
  pod→agent-host-IP direct dial gets refused on this stack while
  pod→Service ClusterIP works. We sidestepped this by having the ingestor
  read flows directly from the cilium-agent's local unix socket
  (`/var/run/cilium/hubble.sock`) via a read-only hostPath mount.
  Documented in `docs/architecture.md`. Multi-node will need the relay
  back — flip `HUBBLE_ADDR` to `hubble-relay.kube-system:80` and run on
  native k3s where the relay's pod→agent path is solid.
- **Cluster-restart recovery.** Verified path: `kubectl rollout restart
  -n kube-system ds/cilium` clears any post-restart node-identity drift
  (`CEP ownership` errors in `cilium status`); ingestor reconnects to
  the agent's socket on its own via exponential backoff. Sometimes a
  `cilium bpf ct flush global` is useful if stale conntrack lingers.
- **metrics-server `0/1 Running` on k3d** — failing `/readyz` with HTTP
  500 because of kubelet TLS. Standard fix is the `--kubelet-insecure-tls`
  flag on the metrics-server deployment. Not on PodMind's critical path
  (we don't depend on metrics-server for anything Tier 1).
- **Runtime-vs-dev dependency hygiene.** We shipped one bug where
  `protobuf` was imported at runtime but only present as a transitive
  dev-dep through `grpcio-tools`. Fixed in commit `13eb882`. Worth a CI
  step that imports every module of every service against a `--no-dev`
  install before shipping the image — prevents the same class of
  surprise from recurring.
- **Vendored upstream protos must come from a pinned tag, not be
  hand-typed.** Our initial `observer.proto` was hand-written from
  memory of upstream field numbers and got five of them wrong, silently
  zeroing pod identity, L4, ports on every captured flow until
  end-to-end testing surfaced it. Fixed in commit `e798f30` by fetching
  from `v1.19.1` of `cilium/cilium`. Worth a CI check that diffs
  vendored upstream files against the pinned reference — would have
  caught both this and the transitive-dep issue above.

### Next phase — agents

Bring-up is closed. The next thing to build is the agent layer, starting with the CPU agent (Tier 1):

1. **Stand up Redis** in `podmind/` namespace and bake the connection details into `services/common/` (new package) or the contracts module. Agents publish `Finding` events to a pub/sub channel; coordinator subscribes.
2. **CPU agent.** Polls `services/ingestor`'s `/buffer/metrics?name=rate(container_cpu_usage_seconds_total[30s])`, runs Isolation Forest + change-point detection, publishes `Finding`s to Redis when something looks anomalous. Smallest end-to-end loop that proves the Finding pipeline.
3. **One SmartHostel service fleshed out for real** — recommend `gateway` (because guest-sim already drives it) or `sensor-ingest` (because the storage agent will eventually want a real workload to chew on). Replace `nginx:alpine` with a FastAPI app per the Day 1 spec.