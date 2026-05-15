#!/usr/bin/env bash
# Bring up the whole single-node stack from a clean Linux box.
# Idempotent: every step checks before doing anything destructive,
# so re-running this is a no-op once the cluster is healthy.
#
# Works against either:
#   - a cluster you've already created (k3d, kind, native k3s, ...) —
#     in which case we leave it alone and just install Cilium etc.
#   - a clean host with no cluster — we install native k3s with
#     flannel disabled so Cilium can take over the CNI.
#
# Assumes: bash, curl, kubectl, helm, docker on PATH. cilium-cli is
# installed automatically if missing; sudo is needed only for the
# native-k3s path. k3d is required if the active context is a k3d
# cluster (used for image import).

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
require kubectl

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ----------------------------------------------------------------------
# 1) cluster — detect existing, install native k3s only if nothing is up

if kubectl cluster-info >/dev/null 2>&1; then
    echo "== cluster already reachable via current kubeconfig"
else
    require sudo
    if ! command -v k3s >/dev/null 2>&1; then
        echo ">> installing k3s (cilium will replace flannel)"
        curl -sfL https://get.k3s.io | \
            INSTALL_K3S_EXEC="--flannel-backend=none --disable-network-policy --disable=traefik --write-kubeconfig-mode=644" \
            sh -
    fi
    export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

    for _ in $(seq 1 30); do
        if kubectl get --raw='/healthz' >/dev/null 2>&1; then break; fi
        sleep 1
    done
fi

# Cluster flavour drives Cilium API-server discovery and image import.
# k3d uses the convention `k3d-<cluster>` for the kubeconfig context.
K3D_CLUSTER=""
ctx="$(kubectl config current-context 2>/dev/null || true)"
if [[ "$ctx" == k3d-* ]]; then
    K3D_CLUSTER="${ctx#k3d-}"
    echo "== detected k3d cluster: $K3D_CLUSTER"
fi

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

CILIUM_VERSION="1.19.1"

if ! kubectl -n kube-system get deployment cilium-operator >/dev/null 2>&1; then
    echo ">> installing cilium $CILIUM_VERSION with hubble"

    # We leave kube-proxy in place rather than running Cilium in
    # kube-proxy-replacement mode. On k3d the eBPF datapath that kpr
    # installs disrupts host→apiserver traffic; the failure surfaces
    # the moment the cilium-agent goes Ready. Hubble flow capture —
    # the actual Tier-1 dependency — does not require kpr.
    cilium install \
        --version "$CILIUM_VERSION" \
        --set hubble.enabled=true \
        --set hubble.relay.enabled=true
else
    echo "== cilium already installed"
fi

# Always wait, even on re-runs — covers the case where a previous run
# applied the operator but the daemonset hadn't finished rolling out
# before everything else started.
echo ">> waiting for cilium to be healthy"
cilium status --wait

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
# 5) ingestor — build image, import into the cluster, apply manifests

require docker

if ! docker image inspect podmind/ingestor:dev >/dev/null 2>&1; then
    echo ">> building ingestor image"
    docker build -t podmind/ingestor:dev -f services/ingestor/Dockerfile .
else
    echo "== ingestor image already built (rebuild manually with: docker build -t podmind/ingestor:dev -f services/ingestor/Dockerfile .)"
fi

echo ">> importing ingestor image into the cluster"
if [[ -n "$K3D_CLUSTER" ]]; then
    require k3d
    k3d image import podmind/ingestor:dev -c "$K3D_CLUSTER"
elif command -v k3s >/dev/null 2>&1; then
    docker image save podmind/ingestor:dev | sudo k3s ctr images import -
else
    echo "!! unknown cluster type; ingestor pod will go ImagePullBackOff" >&2
fi

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
