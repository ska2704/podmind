# podmind-contracts

Shared Pydantic v2 models. No I/O, no side effects, no dependencies
beyond Pydantic. Every Python service in PodMind imports from here so
the buffer, agents, and coordinator can't disagree about wire shapes.

```
podmind_contracts/
├── metrics.py     MetricRecord  — Prometheus sample → buffer row
├── flows.py       HubbleFlow    — Hubble flow → buffer row
├── findings.py    Finding       — agent → Redis pub/sub event
└── tools.py       Coordinator tool-call request/response models
```

Every model is `frozen=True, extra="forbid"`. Anything that wants to
extend a contract should propose a new field upstream rather than
adding fields locally.

## Tests

```
uv run --package podmind-contracts pytest
```

Round-trip JSON tests are exhaustive. If you change a model and the
tests still pass, add a case that proves the new field is exercised.
