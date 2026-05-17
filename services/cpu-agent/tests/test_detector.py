"""Detector tests.

The "5 minutes flat, then a spike" case is the headline scenario: it
proves the detector picks up CPU stress over a flat baseline. We seed
the random gen so the test isn't flaky across runs.
"""

import random

from app.detector import PodDetector, severity_from_score


def _flat_then_spike(n_baseline: int = 60, spike_value: float = 5.0) -> list[float]:
    """Quiet baseline around 0.1 CPU-cores plus a clear spike at the end."""
    rng = random.Random(0)
    baseline = [0.1 + rng.gauss(0, 0.01) for _ in range(n_baseline)]
    return baseline + [spike_value]


def _flat_then_normal(n_baseline: int = 60) -> list[float]:
    rng = random.Random(0)
    baseline = [0.1 + rng.gauss(0, 0.01) for _ in range(n_baseline)]
    return baseline + [0.105]  # well within the baseline cloud


def test_below_min_samples_returns_none():
    d = PodDetector(min_samples=30, refit_interval_s=30.0)
    # 29 samples + 1 current = 30 total. Need min_samples + 1 = 31 to fit.
    assert d.score([0.1] * 30) is None


def test_spike_scores_anomalously():
    d = PodDetector(min_samples=30, refit_interval_s=30.0)
    result = d.score(_flat_then_spike())
    assert result is not None
    # Threshold criterion from STAGE 6: anomaly_score > 0.5
    assert result.anomaly_score > 0.5
    assert result.current_value == 5.0
    assert result.fit_sample_count == 60
    # Baseline summary is over the pre-spike window
    assert 0.05 < result.baseline.mean < 0.15
    assert result.baseline.sample_count == 60


def test_in_distribution_sample_scores_below_threshold():
    d = PodDetector(min_samples=30, refit_interval_s=30.0)
    result = d.score(_flat_then_normal())
    assert result is not None
    # A value inside the baseline cloud should NOT trigger an anomaly.
    assert result.anomaly_score < 0.5


def test_refit_caches_model_between_calls():
    d = PodDetector(min_samples=30, refit_interval_s=30.0)
    # First score triggers a fit.
    d.score(_flat_then_normal())
    model_id_1 = id(d._model)
    # Second call right after (same baseline length) — should reuse cached model.
    d.score(_flat_then_normal())
    model_id_2 = id(d._model)
    assert model_id_1 == model_id_2


def test_severity_buckets():
    assert severity_from_score(0.3) == "info"
    assert severity_from_score(0.55) == "warn"
    assert severity_from_score(0.85) == "critical"
