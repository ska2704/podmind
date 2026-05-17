import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    ingestor_url: str
    redis_url: str

    # Cadence: how often the poller asks the ingestor for new samples.
    poll_interval_s: float

    # Per-pod rolling window of (ts, cpu_rate) samples, bounded by WINDOW_S
    # seconds. Pods unseen for 2 * WINDOW_S are evicted from the agent's
    # state to keep memory bounded as pods come and go.
    window_s: int

    # Number of samples a window must hold before the detector will fit
    # an Isolation Forest against it. Below this we just collect.
    min_samples: int

    # Isolation Forest refits aren't cheap — once per refit_interval_s,
    # not once per sample. Each new sample is *scored* against the most
    # recent fit (cheap).
    refit_interval_s: float

    # Findings are published only when the score crosses this threshold.
    # Higher = more anomalous in our convention (we negate the raw IF
    # score so the comparison is intuitive). Tune with stage 6.
    anomaly_threshold: float

    # Which Prom metric the agent consumes from the ingestor buffer.
    # The ingestor stores rate(...[30s]) under the literal query string,
    # so match that exactly.
    cpu_metric_query: str

    # Redis pub/sub channel for our findings.
    findings_channel: str

    # Agent identity stamped into each Finding.
    agent_id: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            ingestor_url=os.getenv("INGESTOR_URL", "http://ingestor.podmind:8000"),
            redis_url=os.getenv("REDIS_URL", "redis://redis.podmind:6379/0"),
            poll_interval_s=float(os.getenv("POLL_INTERVAL_S", "5.0")),
            window_s=int(os.getenv("WINDOW_S", "300")),
            min_samples=int(os.getenv("MIN_SAMPLES", "30")),
            refit_interval_s=float(os.getenv("REFIT_INTERVAL_S", "30.0")),
            anomaly_threshold=float(os.getenv("ANOMALY_THRESHOLD", "0.5")),
            cpu_metric_query=os.getenv(
                "CPU_METRIC_QUERY",
                "rate(container_cpu_usage_seconds_total[30s])",
            ),
            findings_channel=os.getenv("FINDINGS_CHANNEL", "findings.cpu"),
            agent_id=os.getenv("AGENT_ID", "cpu-agent"),
        )
