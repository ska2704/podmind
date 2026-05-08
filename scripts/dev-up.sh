#!/usr/bin/env bash
# Bring up the whole single-node stack from a clean Linux box.
# Idempotent: every step checks before doing anything destructive,
# so re-running this is a no-op once the cluster is healthy.
#
# Assumes: bash, curl, sudo, kubectl, helm, docker available on PATH.
# If they're missing, the script tells you what to install rather
# than trying to be clever.

set -euo pipefail

# ----------------------------------------------------------------------
# preflight

require() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "missing: $1 — install it and re-run" >&2
        exit 1
    fi
}
require curl
require sudo

# kubectl and helm are required *after* k3s is up; we'll check then.

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ----------------------------------------------------------------------
# 1) k3s

if ! command -v k3s >/dev/null 2>&1; then
    echo ">> installing k3s (cilium will replace flannel)"
    curl -sfL https://get.k3s.io | \
        INSTALL_K3S_EXEC="--flannel-backend=none --disable-network-policy --disable=traefik --write-kubeconfig-mode=644" \
        sh -
else
    echo "== k3s already installed"
fi

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
require kubectl

# wait for the api server to be reachable before we start applying things
for _ in $(seq 1 30); do
    if kubectl get --raw='/healthz' >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# ----------------------------------------------------------------------
# 2) cilium-cli + cilium with hubble

if ! command -v cilium >/dev/null 2>&1; then
    echo ">> installing cilium-cli"
    CLI_VERSION="$(curl -fsSL https://raw.githubusercontent.com/cilium/cilium-cli/main/stable.txt)"
    CLI_ARCH="$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')"
    TARBALL="cilium-linux-${CLI_ARCH}.tar.gz"
    tmp="$(mktemp -d)"
    (
        cd "$tmp"
        curl -fsSL --remote-name-all "https://github.com/cilium/cilium-cli/releases/download/${CLI_VERSION}/${TARBALL}"
        sudo tar xzfC "$TARBALL" /usr/local/bin
    )
    rm -rf "$tmp"
fi

if ! kubectl -n kube-system get deployment cilium-operator >/dev/null 2>&1; then
    echo ">> installing cilium with hubble"
    cilium install \
        --set hubble.enabled=true \
        --set hubble.relay.enabled=true \
        --set hubble.metrics.enableOpenMetrics=true
    cilium status --wait
else
    echo "== cilium already installed"
fi

# ----------------------------------------------------------------------
# 3) prometheus stack via helm

require helm

if ! kubectl get namespace monitoring >/dev/null 2>&1; then
    kubectl create namespace monitoring
fi

if ! helm -n monitoring status prometheus >/dev/null 2>&1; then
    echo ">> installing kube-prometheus-stack"
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
    helm repo update >/dev/null
    helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
        -n monitoring \
        --set grafana.enabled=false \
        --set alertmanager.enabled=false \
        --wait
else
    echo "== prometheus already installed"
fi

# stable name alias so the ingestor's PROM_URL doesn't depend on the
# helm release name.
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Service
metadata:
  name: prometheus
  namespace: monitoring
spec:
  type: ExternalName
  externalName: prometheus-kube-prometheus-prometheus.monitoring.svc.cluster.local
  ports:
    - name: http
      port: 9090
EOF

# ----------------------------------------------------------------------
# 4) smarthostel + guest-sim

echo ">> applying smarthostel"
kubectl apply -k infra/smarthostel/

echo ">> applying guest-sim"
kubectl apply -k infra/guest-sim/

# ----------------------------------------------------------------------
# 5) ingestor — build image, import into k3s containerd, apply manifests

require docker

if ! docker image inspect podmind/ingestor:dev >/dev/null 2>&1; then
    echo ">> building ingestor image"
    docker build -t podmind/ingestor:dev -f services/ingestor/Dockerfile .
else
    echo "== ingestor image already built (rebuild manually with: docker build -t podmind/ingestor:dev -f services/ingestor/Dockerfile .)"
fi

echo ">> importing ingestor image into k3s containerd"
docker image save podmind/ingestor:dev | sudo k3s ctr images import -

echo ">> applying ingestor"
kubectl apply -k infra/ingestor/

# ----------------------------------------------------------------------
# 6) wait for everything

for ns in sh-core sh-edge sh-ops podmind; do
    echo ">> waiting for deployments in $ns"
    kubectl -n "$ns" wait --for=condition=Available \
        --timeout=180s deployment --all
done

# ----------------------------------------------------------------------
# 7) print useful endpoints

cat <<EOF

============================================================
podmind is up.

ingestor      kubectl -n podmind     port-forward svc/ingestor 8000:8000
prometheus    kubectl -n monitoring  port-forward svc/prometheus 9090:9090
hubble relay  kubectl -n kube-system port-forward svc/hubble-relay 4245:80

smoke test:
  curl http://localhost:8000/buffer/metrics?since=-30s | jq '.count'
============================================================
EOF
