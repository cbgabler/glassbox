"""Real-time per-trace classifier for the live monitor.

Wraps the trained baseline.joblib (sklearn RandomForestClassifier) and exposes
a `LiveClassifier` with one hot-path method, `classify(cycles, power)`, that
returns a (label, confidence) tuple in well under 1 ms.

Why this exists:
  * `train_real.py` produces a forest trained on real ESP32 traces.
  * `live_attacker.py` needs to classify each call's trace immediately so the
    anomaly detector can decide whether to fire quarantine.
  * Loading the model + featurizing in a hot loop without this abstraction
    leads to subtle bugs (column ordering, dtype mismatches). One source of
    truth here keeps live + offline analyses consistent.

Upgrade path:
  When the on-Pico TFLite Micro classifier ships, this file becomes the
  shim that just polls the Pico for verdicts instead of running sklearn
  locally. The rest of the runner doesn't change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from features import featurize, FEATURE_NAMES, N_FEATURES


# Class labels the classifier was trained on (must match runner/classifier.py).
CLASSES = ["safe", "timing_leak", "power_leak"]

# What we treat as a "leak" verdict from the model. Everything not in this
# set is considered benign for the purposes of the anomaly detector.
LEAK_LABELS = {"timing_leak", "power_leak"}


@dataclass
class LiveVerdict:
    label: str           # one of CLASSES
    confidence: float    # max class probability
    is_leak: bool        # label in LEAK_LABELS
    leak_confidence: float  # combined probability of any leak class


class LiveClassifier:
    """Stateless wrapper around a fitted sklearn classifier."""

    def __init__(self, model_path: str = "baseline.joblib"):
        import joblib
        self.clf = joblib.load(model_path)
        # sklearn classifiers expose .classes_ -- order may differ from CLASSES.
        self.model_classes = list(self.clf.classes_)
        # Index of each leak label in the model's class vector.
        self._leak_idx = [i for i, c in enumerate(self.model_classes)
                          if c in LEAK_LABELS]

    def classify(self, cycles: int, power: np.ndarray) -> LiveVerdict:
        """Featurize + predict for a single trace. Returns a LiveVerdict."""
        feats = featurize(cycles, power).reshape(1, -1)
        # Use predict_proba so we can report confidence + leak-class total.
        probs = self.clf.predict_proba(feats)[0]
        top_idx = int(np.argmax(probs))
        label = str(self.model_classes[top_idx])
        confidence = float(probs[top_idx])
        leak_conf = float(sum(probs[i] for i in self._leak_idx))
        return LiveVerdict(
            label=label,
            confidence=confidence,
            is_leak=label in LEAK_LABELS,
            leak_confidence=leak_conf,
        )

    def classify_many(self, cycles: list[int], powers: list[np.ndarray]
                      ) -> list[LiveVerdict]:
        """Batch convenience helper -- one predict_proba call for many traces."""
        feats = np.stack([featurize(c, p) for c, p in zip(cycles, powers)])
        probs = self.clf.predict_proba(feats)
        out = []
        for row in probs:
            top_idx = int(np.argmax(row))
            label = str(self.model_classes[top_idx])
            leak_conf = float(sum(row[i] for i in self._leak_idx))
            out.append(LiveVerdict(
                label=label, confidence=float(row[top_idx]),
                is_leak=label in LEAK_LABELS, leak_confidence=leak_conf,
            ))
        return out


# =============================================================================
# Self-test: synthetic traces should round-trip through the classifier.
# =============================================================================

def _selftest():
    import sys, os
    if not os.path.exists("baseline.joblib"):
        print("[live_classifier] baseline.joblib not found -- "
              "run train_real.py first. Skipping selftest.")
        sys.exit(0)
    rng = np.random.default_rng(0)
    clf = LiveClassifier("baseline.joblib")
    # Plausible "safe" trace: low cycle count, low-noise power.
    safe_v = clf.classify(150, rng.normal(2000, 5, 256))
    leak_v = clf.classify(50,  rng.normal(2000, 5, 256))
    print(f"safe-ish trace -> {safe_v}")
    print(f"leak-ish trace -> {leak_v}")
    print(f"feature names: {N_FEATURES} dims = {FEATURE_NAMES}")


if __name__ == "__main__":
    _selftest()
