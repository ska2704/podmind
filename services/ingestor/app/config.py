import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    prom_url: str
    hubble_addr: str
    buffer_path: str

    poll_interval_s: float
    buffer_window_s: int
    sweep_interval_s: float

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            prom_url=os.getenv("PROM_URL", "http://prometheus.monitoring:9090"),
            # Default to the cilium-agent's local unix socket. For multi-node
            # set HUBBLE_ADDR=hubble-relay.kube-system:80 instead.
            hubble_addr=os.getenv("HUBBLE_ADDR", "unix:///var/run/cilium/hubble.sock"),
            buffer_path=os.getenv("BUFFER_PATH", "/var/lib/podmind/buffer.sqlite"),
            poll_interval_s=float(os.getenv("POLL_INTERVAL_S", "1.0")),
            buffer_window_s=int(os.getenv("BUFFER_WINDOW_S", "300")),
            sweep_interval_s=float(os.getenv("SWEEP_INTERVAL_S", "30.0")),
        )
