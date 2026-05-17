"""Isolation Forest wrapper, per-pod.

One detector per pod. We refit at most once every `refit_interval_s`
(refits are O(n_estimators * n_samples) and we don't want to pay that
per sample). Between refits we just score the most recent sample
against the cached model.

Known limitation: scores collapse 30-45s into a sustained anomaly
because the refit picks up the stressed samples as part of the
baseline. See `Follow-ups` in podmind-brief.md for why we accept
this for v1 and what a production deployment would do instead.

Score convention: higher = more anomalous. scikit-learn's
`score_samples` returns higher = more normal, so we negate. The
returned `anomaly_score` is then directly comparable to a threshold
like 0.7 with "above = anomaly."
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

import numpy as np
from sklearn.ensemble import IsolationForest

from podmind_contracts import BaselineSummary


@dataclass
class DetectionResult:
    anomaly_score: float
    current_value: float
    baseline: BaselineSummary
    fit_sample_count: int


@dataclass
class PodDetector:
    """Stateful per-pod Isolation Forest.

    `score()` fits on the window minus the most recent sample (the
    "baseline"), then scores the most recent sample against that fit.
    The fit is cached for `refit_interval_s`; subsequent calls reuse
    the cached model for `score_samples`. This is the cheap path.
    """

    min_samples: int
    refit_interval_s: float
    n_estimators: int = 100
    contamination: float = 0.05
    _model: IsolationForest | None = field(default=None, init=False, repr=False)
    _last_fit_t: float = field(default=0.0, init=False, repr=False)
    _last_fit_count: int = field(default=0, init=False, repr=False)
    _last_baseline: BaselineSummary | None = field(default=None, init=False, repr=False)

    def score(self, values: list[float]) -> DetectionResult | None:
        """Return DetectionResult for the LAST value in `values`, or
        None if we don't have enough history yet to fit."""
        if len(values) < self.min_samples + 1:
            return None

        baseline = values[:-1]
        current = values[-1]

        now = time.monotonic()
        stale_fit = (
            self._model is None
            or (now - self._last_fit_t) >= self.refit_interval_s
            or self._last_fit_count != len(baseline)
        )
        if stale_fit:
            self._fit(baseline)
            self._last_fit_t = now
            self._last_fit_count = len(baseline)
            self._last_baseline = _summarize(baseline)

        assert self._model is not None
        assert self._last_baseline is not None
        # IsolationForest.score_samples returns higher = more normal.
        # Negate so higher = more anomalous, in line with our threshold
        # semantics ("above = anomaly").
        raw = float(self._model.score_samples(np.array([[current]]))[0])
        anomaly_score = -raw

        return DetectionResult(
            anomaly_score=anomaly_score,
            current_value=current,
            baseline=self._last_baseline,
            fit_sample_count=len(baseline),
        )

    def _fit(self, baseline: list[float]) -> None:
        X = np.array(baseline).reshape(-1, 1)
        # random_state pinned so identical inputs produce identical scores;
        # otherwise the threshold drifts between refits and tests flake.
        self._model = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            random_state=0,
        )
        self._model.fit(X)


def _summarize(values: list[float]) -> BaselineSummary:
    if len(values) < 2:
        # statistics.stdev needs >= 2 samples; default to 0 stddev rather
        # than crashing for the degenerate one-sample case.
        return BaselineSummary(
            mean=float(values[0]) if values else 0.0,
            stddev=0.0,
            sample_count=len(values),
        )
    return BaselineSummary(
        mean=float(statistics.fmean(values)),
        stddev=float(statistics.stdev(values)),
        sample_count=len(values),
    )


def severity_from_score(score: float) -> str:
    """Bucket a (possibly noisy) anomaly score into the Finding severity
    levels. Buckets chosen to be liberal in the warn range and strict
    on critical — tunable in stage 6 if this is too jumpy."""
    if score >= 0.7:
        return "critical"
    if score >= 0.5:
        return "warn"
    return "info"
