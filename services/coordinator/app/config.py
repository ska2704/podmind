import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    ingestor_url: str
    redis_url: str
    ollama_url: str
    model_name: str

    # Channel the cpu-agent (and future agents under findings.*)
    # publish to. Currently we only subscribe to cpu specifically;
    # promote to `findings.*` once more agents exist.
    findings_channel: str

    # Ring-buffer size for the in-process recent-findings cache that
    # the LLM's get_recent_anomalies tool queries from.
    findings_cache_size: int

    # Hard ceiling on tool-call rounds per /ask request. Keeps a
    # misbehaving model from looping forever.
    max_tool_rounds: int

    # Which metric the get_pod_metrics tool reads by default. Matches
    # the ingestor's DEFAULT_METRICS entry exactly.
    default_metric_query: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            ingestor_url=os.getenv("INGESTOR_URL", "http://ingestor.podmind:8000"),
            redis_url=os.getenv("REDIS_URL", "redis://redis.podmind:6379/0"),
            # host.docker.internal resolves to the Mac host from inside
            # k3d containers under OrbStack — Ollama runs on the host
            # for Metal acceleration.
            ollama_url=os.getenv("OLLAMA_URL", "http://host.docker.internal:11434"),
            model_name=os.getenv("MODEL_NAME", "qwen2.5:1.5b-instruct-q4_K_M"),
            findings_channel=os.getenv("FINDINGS_CHANNEL", "findings.cpu"),
            findings_cache_size=int(os.getenv("FINDINGS_CACHE_SIZE", "200")),
            max_tool_rounds=int(os.getenv("MAX_TOOL_ROUNDS", "5")),
            default_metric_query=os.getenv(
                "DEFAULT_METRIC_QUERY",
                "rate(container_cpu_usage_seconds_total[30s])",
            ),
        )
