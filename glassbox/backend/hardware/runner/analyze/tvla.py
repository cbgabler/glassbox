"""TVLA -- Test Vector Leakage Assessment.

Implements the standard non-specific TVLA test (Welch's two-sample t-test)
used by every cryptographic side-channel evaluation lab in the world (NIST,

Hypothesis test:
    H0:  the function's measurable execution profile is independent of secret
    H1:  the profile depends on the secret  (i.e. it leaks)

Procedure (this file):
    1. Collect two groups of N traces each from the same function:
         - Group A: secret = fixed (typically all-zero)
         - Group B: secret = random per call
       (or any other two distributions you want to distinguish.)
    2. For each measurement axis (cycles is one scalar; the power trace
       is M sample points), compute Welch's t-statistic between the two
       groups.
    3. If |t| > 4.5 anywhere, the function is leaking with > 99.999%
       confidence (Bonferroni-corrected for typical M ~ 1000 sample points).

The 4.5 threshold comes from ISO/IEC 17825 and is the de facto industry
standard. Lower thresholds (e.g. 3.5) are sometimes used for pre-screening.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import numpy as np

# ISO/IEC 17825 standard threshold. |t| above this = leak detected with
# > 99.999% confidence, after Bonferroni correction across typical
# trace lengths (M ~ 100..10_000 sample points).
TVLA_THRESHOLD = 4.5

# A channel whose total per-trace variance is below this tolerance is
# considered "flat" (no signal). Cleaner than reporting a 0/0 t-statistic
# as "no leak"; we want to tell the user the channel measured nothing.
FLAT_VARIANCE_TOL = 1e-12


def welch_t(group_a: np.ndarray, group_b: np.ndarray) -> np.ndarray:
    """Welch's two-sample t-statistic, computed per-column.

    Args:
        group_a: shape (n_a,) or (n_a, m). One trace per row.
        group_b: shape (n_b,) or (n_b, m). One trace per row, same m.

    Returns:
        Per-column t-statistic. Scalar if inputs are 1D, length-m array if 2D.
    """
    a = np.asarray(group_a, dtype=np.float64)
    b = np.asarray(group_b, dtype=np.float64)
    if a.ndim == 1:
        a = a.reshape(-1, 1)
    if b.ndim == 1:
        b = b.reshape(-1, 1)
    if a.shape[1] != b.shape[1]:
        raise ValueError(
            f"groups disagree on trace length: a={a.shape}, b={b.shape}"
        )

    mean_a = a.mean(axis=0)
    mean_b = b.mean(axis=0)
    var_a  = a.var(axis=0, ddof=1)
    var_b  = b.var(axis=0, ddof=1)
    n_a, n_b = a.shape[0], b.shape[0]

    # Numerical floor avoids divide-by-zero on perfectly constant features
    # (e.g. cycles is identical across all traces -- which itself proves
    # there is no leak in that dimension, so t = 0 is the right answer).
    denom = np.sqrt(var_a / n_a + var_b / n_b)
    denom = np.where(denom == 0, np.inf, denom)
    t = (mean_a - mean_b) / denom
    return t.squeeze() if t.size > 1 else float(t.item())


def welch_t_higher_order(group_a: np.ndarray, group_b: np.ndarray,
                         order: int = 2) -> np.ndarray:
    """Higher-order Welch's t -- detect leaks in central moments.

    First-order TVLA (welch_t above) catches leaks where the *mean* of the
    trace depends on the secret. Higher-order TVLA catches leaks where the
    *variance* (order=2), *skewness* (order=3), etc depend on the secret
    even though the mean is identical.

    This is the standard test for **masked implementations**: a function
    that XORs every secret with a random mask before processing it has
    secret-independent means by construction, but its variance still
    depends on the secret unless the masking is correctly applied. First-
    order TVLA passes; higher-order TVLA fails. Industry-standard
    countermeasure to first-order TVLA defeats; second-order TVLA
    standard countermeasure to that defeat.

    Procedure: per-group, center the traces (subtract group mean), raise
    to `order`, then run ordinary Welch's t-test on the centered moments.

    order=1 is identical to welch_t() (provided here for symmetry).
    """
    if order < 1:
        raise ValueError(f"order must be >= 1, got {order}")
    if order == 1:
        return welch_t(group_a, group_b)

    a = np.asarray(group_a, dtype=np.float64)
    b = np.asarray(group_b, dtype=np.float64)
    if a.ndim == 1:
        a = a.reshape(-1, 1)
    if b.ndim == 1:
        b = b.reshape(-1, 1)

    # Per-group centering, then raise to the requested order.
    a_ho = (a - a.mean(axis=0, keepdims=True)) ** order
    b_ho = (b - b.mean(axis=0, keepdims=True)) ** order
    return welch_t(a_ho, b_ho)


@dataclass
class ChannelVerdict:
    """Result of one TVLA test on a single measurement channel."""
    channel: str                  # "cycles", "micros", "insns", "branches", "power", ...
    order: int                    # 1 = first-order (mean leak), 2 = second-order (variance leak)
    n_a: int                      # samples in group A
    n_b: int                      # samples in group B
    max_abs_t: float              # max |t| across the channel's samples
    argmax: Optional[int]         # sample index where the max occurred (None for scalar channels)
    threshold: float              # the |t| threshold used
    leak_detected: bool           # max_abs_t > threshold AND channel had signal
    is_flat: bool                 # True if both groups had ~zero variance (no signal)
    t_curve: Optional[np.ndarray] # full per-sample t-statistic (None for scalar)


def _scalar_verdict(name: str, order: int, t_value: float, n_a: int, n_b: int,
                    flat: bool, threshold: float) -> ChannelVerdict:
    abs_t = float(abs(t_value))
    return ChannelVerdict(
        channel=name, order=order,
        n_a=n_a, n_b=n_b,
        max_abs_t=abs_t,
        argmax=None,
        threshold=threshold,
        leak_detected=(not flat) and abs_t > threshold,
        is_flat=flat,
        t_curve=None,
    )


def tvla_scalar(name: str, group_a: np.ndarray, group_b: np.ndarray,
                threshold: float = TVLA_THRESHOLD,
                order: int = 1) -> ChannelVerdict:
    """TVLA on a scalar-per-trace channel (cycles, micros, insns, branches, ...).

    With order=1, this is the standard t-test on the means.
    With order=2, this catches variance leaks (masked implementations).
    """
    a = np.asarray(group_a, dtype=np.float64)
    b = np.asarray(group_b, dtype=np.float64)
    flat = (a.var() < FLAT_VARIANCE_TOL) and (b.var() < FLAT_VARIANCE_TOL)
    if flat:
        return _scalar_verdict(name, order, 0.0, a.size, b.size, True, threshold)
    if order == 1:
        t = float(welch_t(a, b))
    else:
        t = float(np.asarray(welch_t_higher_order(a, b, order=order)).item())
    return _scalar_verdict(name, order, t, a.size, b.size, False, threshold)


def tvla_power(power_a: np.ndarray, power_b: np.ndarray,
               threshold: float = TVLA_THRESHOLD,
               order: int = 1) -> ChannelVerdict:
    """TVLA on a power trace channel (M samples per trace).

    power_a, power_b: 2D arrays of shape (n_a, m), (n_b, m).
    With order=2 you get the second-order test (variance-leak detector).
    """
    a = np.asarray(power_a, dtype=np.float64)
    b = np.asarray(power_b, dtype=np.float64)
    flat = (a.var() < FLAT_VARIANCE_TOL) and (b.var() < FLAT_VARIANCE_TOL)
    if flat:
        return ChannelVerdict(
            channel="power", order=order,
            n_a=a.shape[0], n_b=b.shape[0],
            max_abs_t=0.0, argmax=None,
            threshold=threshold, leak_detected=False, is_flat=True, t_curve=None,
        )
    t_curve = np.asarray(welch_t_higher_order(a, b, order=order) if order > 1
                         else welch_t(a, b))
    abs_t = np.abs(t_curve)
    argmax = int(abs_t.argmax())
    return ChannelVerdict(
        channel="power", order=order,
        n_a=a.shape[0], n_b=b.shape[0],
        max_abs_t=float(abs_t[argmax]),
        argmax=argmax,
        threshold=threshold,
        leak_detected=bool(abs_t[argmax] > threshold),
        is_flat=False,
        t_curve=t_curve,
    )


@dataclass
class MultiChannelReport:
    """Combined first- and second-order verdicts across many measurement channels."""
    target_name: str
    # First-order (mean-leak) verdicts, one per channel name.
    first_order: Dict[str, ChannelVerdict] = field(default_factory=dict)
    # Second-order (variance-leak) verdicts -- typically applied to power
    # plus optionally cycles/insns. Subset of channels in first_order.
    second_order: Dict[str, ChannelVerdict] = field(default_factory=dict)

    @property
    def leak_detected(self) -> bool:
        return any(v.leak_detected for v in self.first_order.values()) \
            or any(v.leak_detected for v in self.second_order.values())

    def leaking_channels(self) -> list[str]:
        out = []
        for name, v in self.first_order.items():
            if v.leak_detected:
                out.append(f"{name} (1st order)")
        for name, v in self.second_order.items():
            if v.leak_detected:
                out.append(f"{name} (2nd order)")
        return out

    def summary(self) -> str:
        rows = []
        rows.append(f"  {'channel':<10s}  {'order':>5s}  "
                    f"{'|t|':>9s}  {'thr':>5s}   verdict")
        rows.append(f"  {'-'*10:<10s}  {'-'*5:>5s}  {'-'*9:>9s}  "
                    f"{'-'*5:>5s}   {'-'*7}")
        def line(v: ChannelVerdict) -> str:
            if v.is_flat:
                tag = "FLAT (no signal)"
            elif v.leak_detected:
                tag = "LEAK"
                if v.argmax is not None:
                    tag += f"   peak at sample {v.argmax}"
            else:
                tag = "OK"
            return (f"  {v.channel:<10s}  {v.order:>5d}  "
                    f"{v.max_abs_t:>9.2f}  {v.threshold:>5.1f}   {tag}")
        for v in self.first_order.values():
            rows.append(line(v))
        for v in self.second_order.values():
            rows.append(line(v))
        return "\n".join(rows)


def tvla_multi(target_name: str,
               scalar_channels: Dict[str, Tuple[np.ndarray, np.ndarray]],
               power_a: Optional[np.ndarray] = None,
               power_b: Optional[np.ndarray] = None,
               threshold: float = TVLA_THRESHOLD,
               second_order: bool = True) -> MultiChannelReport:
    """Run first-order TVLA on every scalar channel + power, plus second-order
    (variance-leak) on power and on the cycles channel if present.

    Args:
        scalar_channels: name -> (group_a_array, group_b_array). Each array
            is 1D, one row per trace. Typical names: "cycles", "micros",
            "insns", "branches".
        power_a, power_b: optional 2D arrays (n_traces, m_samples) for the
            power-trace channel. Pass None to skip (e.g. for synthetic tests).
        second_order: if True, also run order-2 TVLA on power and cycles.
    """
    first: Dict[str, ChannelVerdict] = {}
    second: Dict[str, ChannelVerdict] = {}

    for name, (a, b) in scalar_channels.items():
        first[name] = tvla_scalar(name, a, b, threshold=threshold, order=1)

    if power_a is not None and power_b is not None:
        first["power"] = tvla_power(power_a, power_b, threshold=threshold, order=1)
        if second_order:
            second["power"] = tvla_power(power_a, power_b, threshold=threshold, order=2)

    if second_order and "cycles" in scalar_channels:
        a, b = scalar_channels["cycles"]
        second["cycles"] = tvla_scalar("cycles", a, b, threshold=threshold, order=2)

    return MultiChannelReport(
        target_name=target_name,
        first_order=first,
        second_order=second,
    )


# =============================================================================
# Backwards-compatible single-channel helpers (used by older callers).
# =============================================================================

def tvla_cycles(cycles_a: np.ndarray, cycles_b: np.ndarray,
                threshold: float = TVLA_THRESHOLD) -> ChannelVerdict:
    return tvla_scalar("cycles", cycles_a, cycles_b, threshold=threshold)


@dataclass
class TVLAReport:
    """Two-channel verdict (legacy). Prefer MultiChannelReport for new code."""
    target_name: str
    cycles: ChannelVerdict
    power: ChannelVerdict
    leak_detected: bool

    def summary(self) -> str:
        lines = [
            f"  Cycle channel:  |t| = {self.cycles.max_abs_t:8.2f}   "
            f"threshold = {self.cycles.threshold:.1f}   "
            f"{'LEAK' if self.cycles.leak_detected else 'OK'}",
            f"  Power channel:  |t| = {self.power.max_abs_t:8.2f}   "
            f"threshold = {self.power.threshold:.1f}   "
            f"{'LEAK' if self.power.leak_detected else 'OK'}"
            + (f"   (max at sample {self.power.argmax})" if self.power.argmax is not None else ""),
        ]
        return "\n".join(lines)


def tvla_report(target_name: str,
                cycles_a: np.ndarray, cycles_b: np.ndarray,
                power_a: np.ndarray, power_b: np.ndarray,
                threshold: float = TVLA_THRESHOLD) -> TVLAReport:
    """Two-channel report (legacy). New code should use tvla_multi()."""
    c = tvla_scalar("cycles", cycles_a, cycles_b, threshold)
    p = tvla_power(power_a, power_b, threshold)
    return TVLAReport(
        target_name=target_name,
        cycles=c,
        power=p,
        leak_detected=c.leak_detected or p.leak_detected,
    )


# =============================================================================
# Self-test: synthetic leaky vs safe to verify the math.
# Run me directly with `python tvla.py` as a sanity check.
# =============================================================================

def _selftest():
    rng = np.random.default_rng(0)
    n = 500
    m = 256

    # ----- 1. SAFE function: identical distributions on every channel. -----
    cyc_safe = (rng.normal(150, 1, n), rng.normal(150, 1, n))
    ins_safe = (rng.normal(450, 5, n), rng.normal(450, 5, n))
    br_safe  = (rng.normal( 12, 1, n), rng.normal( 12, 1, n))
    us_safe  = (rng.normal(  3, 0.1, n), rng.normal(  3, 0.1, n))
    pwr_safe_a = rng.normal(2000, 5, (n, m))
    pwr_safe_b = rng.normal(2000, 5, (n, m))
    rep_safe = tvla_multi(
        "self_test_safe",
        scalar_channels={"cycles": cyc_safe, "insns": ins_safe,
                         "branches": br_safe, "micros": us_safe},
        power_a=pwr_safe_a, power_b=pwr_safe_b,
    )
    print("=== SAFE (should be OK on every channel; second-order also OK) ===")
    print(rep_safe.summary())
    assert not rep_safe.leak_detected, "SAFE selftest spuriously flagged a leak"

    # ----- 2. LEAKY function: clear mean shifts on cycles + branches + power. -----
    cyc_leak = (rng.normal( 50, 5, n), rng.normal(200, 5, n))
    ins_leak = (rng.normal(150, 8, n), rng.normal(600, 8, n))
    br_leak  = (rng.normal(  3, 0.5, n), rng.normal( 24, 1, n))
    us_leak  = (rng.normal(  1.0, 0.1, n), rng.normal(  4.5, 0.2, n))
    pwr_leak_a = rng.normal(2000, 5, (n, m))
    pwr_leak_b = rng.normal(2000, 5, (n, m))
    pwr_leak_b[:, 100:120] += 50
    rep_leak = tvla_multi(
        "self_test_leak",
        scalar_channels={"cycles": cyc_leak, "insns": ins_leak,
                         "branches": br_leak, "micros": us_leak},
        power_a=pwr_leak_a, power_b=pwr_leak_b,
    )
    print("\n=== LEAKY (should LEAK on every channel) ===")
    print(rep_leak.summary())
    assert rep_leak.leak_detected, "LEAKY selftest failed to flag the leak"
    for name in ("cycles", "insns", "branches", "micros", "power"):
        assert rep_leak.first_order[name].leak_detected, \
            f"LEAKY first-order {name} missed"
    assert 100 <= rep_leak.first_order["power"].argmax < 120, \
        "LEAKY power argmax outside injected window"

    # ----- 3. MASKED function: identical mean, but variance depends on secret.
    # First-order TVLA should pass (no leak). Second-order should catch it.
    cyc_mask_a = rng.normal(150, 1, n)        # tight distribution
    cyc_mask_b = rng.normal(150, 8, n)        # same mean, much wider
    pwr_mask_a = rng.normal(2000, 5, (n, m))  # identical means
    pwr_mask_b = rng.normal(2000, 5, (n, m))
    pwr_mask_b[:, 100:120] += rng.normal(0, 30, (n, 20))   # extra variance only here
    rep_mask = tvla_multi(
        "self_test_masked",
        scalar_channels={"cycles": (cyc_mask_a, cyc_mask_b)},
        power_a=pwr_mask_a, power_b=pwr_mask_b,
    )
    print("\n=== MASKED (1st-order OK on cycles+power, 2nd-order LEAK) ===")
    print(rep_mask.summary())
    assert not rep_mask.first_order["cycles"].leak_detected, \
        "MASKED first-order cycles spuriously flagged"
    assert rep_mask.second_order["cycles"].leak_detected, \
        "MASKED second-order cycles missed the variance leak"
    assert rep_mask.second_order["power"].leak_detected, \
        "MASKED second-order power missed the variance leak"

    # ----- 4. FLAT channel: zero variance (constant value). Should NOT flag. -----
    flat_a = np.full(n, 42.0)
    flat_b = np.full(n, 42.0)
    rep_flat = tvla_multi("self_test_flat",
                          scalar_channels={"cycles": (flat_a, flat_b)})
    print("\n=== FLAT channel (constant; should report FLAT, not LEAK) ===")
    print(rep_flat.summary())
    assert rep_flat.first_order["cycles"].is_flat, "FLAT channel not flagged as flat"
    assert not rep_flat.leak_detected, "FLAT channel spuriously flagged a leak"

    print("\nSelftest PASSED.")


if __name__ == "__main__":
    _selftest()
