#!/usr/bin/env bash
# Bring up the two port-forwards the frontend dev server proxies to.
#
# The Vite config (vite.config.ts) proxies:
#   /api/buffer/*   -> http://localhost:8000  (ingestor)
#   /api/ask        -> http://localhost:8001  (coordinator)
#   /api/findings/* -> http://localhost:8001  (coordinator)
#
# Run this in a terminal alongside `npm run dev`. Ctrl-C tears both
# port-forwards down via the trap.
set -euo pipefail

trap 'echo "stopping port-forwards"; kill 0' EXIT INT TERM

echo ">> ingestor    on localhost:8000"
kubectl -n podmind port-forward svc/ingestor 8000:8000 &

echo ">> coordinator on localhost:8001"
kubectl -n podmind port-forward svc/coordinator 8001:8000 &

wait
