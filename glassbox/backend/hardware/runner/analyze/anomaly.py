"""Streaming anomaly detector.

Takes a sequence of per-call verdicts (label + leak_confidence) and decides
whether to fire quarantine. The trick is balancing sensitivity vs robustness:

  * Single high-confidence "leak" verdict -> too jumpy, fires on noise.
  * N consecutive high-confidence "leak" verdicts -> standard pattern in
    intrusion-detection systems and what we use here.

Default rule: 3 consecutive verdicts where the model's combined leak
confidence is >= 0.85. Both numbers are tunable on the CLI.

Why "consecutive" rather than "N out of M sliding window":
  * The attacker usually probes systematically -- they don't accidentally
    hit one suspicious input then back off. So a streak is the right thing
    to watch for.
  * "N consecutive" gives the operator a clean rule to explain on stage:
    "three strikes and you're out."

The detector is **stateful** -- a non-leak verdict resets the streak.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# Defaults that work well on the existing baseline.joblib.
DEFAULT_STREAK         = 3       # require N consecutive leak verdicts
DEFAULT_LEAK_THRESHOLD = 0.85    # leak_confidence >= this counts


@dataclass
class DetectorState:
    """Snapshot of detector state after one update; useful for live UI."""
    streak: int                  # current consecutive-leak counter
    streak_target: int           # what the streak needs to hit
    last_label: Optional[str]
    last_leak_conf: float
    fired: bool                  # True iff the threshold was just crossed
    total_calls: int
    total_leaks_seen: int


class AnomalyDetector:
    """Consecutive-vote anomaly detector with reset-on-clean semantics."""

    def __init__(self,
                 streak: int = DEFAULT_STREAK,
                 leak_threshold: float = DEFAULT_LEAK_THRESHOLD):
        if streak < 1:
            raise ValueError(f"streak must be >= 1, got {streak}")
        if not 0.0 <= leak_threshold <= 1.0:
            raise ValueError(
                f"leak_threshold must be in [0, 1], got {leak_threshold}"
            )
        self.streak_target = streak
        self.leak_threshold = leak_threshold

        self._streak = 0
        self._total_calls = 0
        self._total_leaks_seen = 0
        self._already_fired = False  # latches True once we've fired once

    @property
    def streak(self) -> int:
        return self._streak

    @property
    def fired(self) -> bool:
        return self._already_fired

    def reset(self) -> None:
        self._streak = 0
        self._already_fired = False

    def update(self, label: str, leak_confidence: float) -> DetectorState:
        """Feed one new verdict in. Returns the post-update state."""
        self._total_calls += 1
        is_leak = leak_confidence >= self.leak_threshold
        if is_leak:
            self._streak += 1
            self._total_leaks_seen += 1
        else:
            self._streak = 0

        # `fired` is True only on the call that first crosses the threshold.
        # The latch prevents repeated firings if the caller keeps feeding.
        crossed_now = (self._streak >= self.streak_target) and not self._already_fired
        if crossed_now:
            self._already_fired = True

        return DetectorState(
            streak=self._streak,
            streak_target=self.streak_target,
            last_label=label,
            last_leak_conf=leak_confidence,
            fired=crossed_now,
            total_calls=self._total_calls,
            total_leaks_seen=self._total_leaks_seen,
        )


# =============================================================================
# Self-test
# =============================================================================

def _selftest():
    d = AnomalyDetector(streak=3, leak_threshold=0.85)

    # Two safe, then three leaky -> should fire on the 5th call.
    seq = [
        ("safe",        0.10),
        ("safe",        0.05),
        ("timing_leak", 0.95),
        ("timing_leak", 0.92),
        ("timing_leak", 0.91),
    ]
    fired_at = None
    for i, (label, conf) in enumerate(seq):
        s = d.update(label, conf)
        print(f"  call {i}: streak={s.streak}/{s.streak_target}  fired={s.fired}")
        if s.fired:
            fired_at = i
    assert fired_at == 4, f"expected fire on 5th call, got {fired_at}"

    # A single safe in the middle should reset the streak.
    d.reset()
    seq2 = [
        ("timing_leak", 0.95),
        ("timing_leak", 0.92),
        ("safe",        0.10),  # reset!
        ("timing_leak", 0.92),
        ("timing_leak", 0.91),
    ]
    fired_at = None
    for i, (label, conf) in enumerate(seq2):
        s = d.update(label, conf)
        if s.fired:
            fired_at = i
    assert fired_at is None, f"expected no fire (streak broken), got {fired_at}"

    # Resume after the break: 3 more leaks in a row should fire.
    s = d.update("timing_leak", 0.95)  # streak = 3
    assert s.fired, "expected fire after resumed streak"

    print("\nselftest PASSED.")


if __name__ == "__main__":
    _selftest()
