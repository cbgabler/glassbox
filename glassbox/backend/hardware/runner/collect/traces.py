"""Trace collection -- run a TVLA campaign and write a parquet.

This is the bridge between collect/pod.py (one-shot trace requests) and
analyze/tvla.py (numpy in, verdict out). One call to `collect_two_groups`
produces enough data for a full TVLA + CPA pass on a single target.

Output schema (parquet columns -- matches analyze/eval.py:load_traces):

    target       str            "gb_target" (always, in scanner mode)
    group        str            "A_zero" | "B_random"  (or campaign-specific)
    fn_id        int            FN_GB_TARGET (3)
    input_hex    str            input bytes as hex (CPA needs these)
    cycles       int            CCOUNT delta
    micros       int            esp_timer_get_time delta
    insns        int            PMU instruction-retired delta (0 if PMU n/a)
    branches     int            PMU branch delta (0 if PMU n/a)
    power        list<uint16>   length TRACE_LEN ADC samples

Failure handling: per-trace failures (TraceFailure) are NOT raised. They
are tallied in a `FailureStats` and returned alongside the DataFrame, so
scan_target.py can build a CrashFinding without re-running the campaign.
We DO bail out early if the failure rate exceeds `max_failure_ratio`
(default 25%) -- a target that crashes that often is not safely sweep-able.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from collect import pod as pod_mod
from collect.pod import FN_GB_TARGET, Pod, Trace, TraceFailure


# Default TVLA campaign sizes. 500 per group is "publishable" lower bound;
# 1000+ matches academic papers. 100 is fine for a smoketest verdict but
# CPA quality drops off a cliff below ~500.
DEFAULT_N_PER_GROUP = 1000

# How many traces can fail before we abort the whole run. 25% means we
# treat the target as "structurally broken" rather than "occasionally bad
# input." Tuned with scan_target.py's verdict policy in mind.
DEFAULT_MAX_FAILURE_RATIO = 0.25

# Default secret length for fixed-vs-random campaigns. The harness's
# input buffer is 64 bytes, so we cap there. AES uses 16; the demo
# strcmp uses 8.
DEFAULT_SECRET_LEN = 16


# =============================================================================
# Failure tracking
# =============================================================================

@dataclass
class FailureStats:
    """Per-kind counters for traces that didn't return a clean Trace.

    `examples[kind]` keeps the first ~5 hex_inputs that triggered each kind
    so the orchestrator can attach reproducers to the CrashFinding.
    """
    by_kind: Dict[str, int] = field(default_factory=dict)
    examples: Dict[str, List[str]] = field(default_factory=dict)
    panic_pcs: List[str] = field(default_factory=list)
    panic_reasons: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.by_kind.values())

    def record(self, fail: TraceFailure, hex_input: str) -> None:
        self.by_kind[fail.kind] = self.by_kind.get(fail.kind, 0) + 1
        ex = self.examples.setdefault(fail.kind, [])
        if len(ex) < 5:
            ex.append(hex_input)
        if fail.kind == "panic":
            if fail.panic_pc:     self.panic_pcs.append(fail.panic_pc)
            if fail.panic_reason: self.panic_reasons.append(fail.panic_reason)


# =============================================================================
# Campaigns -- each picks group A vs group B inputs
# =============================================================================

@dataclass
class Campaign:
    name: str
    group_a_label: str
    group_b_label: str
    secret_len: int = DEFAULT_SECRET_LEN

    def sample_a(self, rng: np.random.Generator) -> bytes:
        raise NotImplementedError

    def sample_b(self, rng: np.random.Generator) -> bytes:
        raise NotImplementedError


@dataclass
class RandomVsZero(Campaign):
    """Group A: all-zero input. Group B: uniform random bytes.

    Standard non-specific TVLA -- catches almost any leak in one pass.
    Use this as the default for new targets.
    """
    name: str = "random_vs_zero"
    group_a_label: str = "A_zero"
    group_b_label: str = "B_random"

    def sample_a(self, rng):                         # type: ignore[override]
        return bytes(self.secret_len)                # \x00 * N

    def sample_b(self, rng):                         # type: ignore[override]
        return bytes(rng.integers(0, 256, self.secret_len, dtype=np.uint8))


@dataclass
class RandomVsRandom(Campaign):
    """Both groups random. NEGATIVE control -- a correct constant-time
    impl should produce |t| < 4.5 here. Used to validate the harness."""
    name: str = "random_vs_random"
    group_a_label: str = "A_random"
    group_b_label: str = "B_random"

    def sample_a(self, rng):                         # type: ignore[override]
        return bytes(rng.integers(0, 256, self.secret_len, dtype=np.uint8))

    sample_b = sample_a


@dataclass
class MatchVsRandom(Campaign):
    """Group A: input matches the supplied reference (loop / comparator runs
    to completion -- slow path). Group B: random (the comparator exits
    early -- fast path). This is the campaign you want for byte-compare
    functions like strcmp / memcmp -- it exposes early-return timing leaks
    that random_vs_zero CANNOT see (because all-zero inputs also early-exit).

    The reference bytes are padded out to `secret_len` with random tail
    bytes per call -- so `cycles` differences are about the comparison loop,
    not about the bytes after the comparator stops looking.
    """
    name: str = "match_vs_random"
    group_a_label: str = "A_match"
    group_b_label: str = "B_random"
    reference: bytes = b""

    def __post_init__(self):
        if not self.reference:
            raise ValueError("MatchVsRandom requires a non-empty reference")

    def sample_a(self, rng):                         # type: ignore[override]
        out = bytearray(self.secret_len)
        out[:len(self.reference)] = self.reference[:self.secret_len]
        if self.secret_len > len(self.reference):
            tail = rng.integers(0, 256,
                                self.secret_len - len(self.reference),
                                dtype=np.uint8)
            out[len(self.reference):] = bytes(tail)
        return bytes(out)

    def sample_b(self, rng):                         # type: ignore[override]
        return bytes(rng.integers(0, 256, self.secret_len, dtype=np.uint8))


CAMPAIGNS: Dict[str, type] = {
    "random_vs_zero":   RandomVsZero,
    "random_vs_random": RandomVsRandom,
    "match_vs_random":  MatchVsRandom,
}


# =============================================================================
# Collection
# =============================================================================

ProgressCb = Callable[[int, int, int], None]    # (done, total, failed)


def collect_two_groups(
    pod: Pod,
    campaign: Campaign,
    n_per_group: int = DEFAULT_N_PER_GROUP,
    *,
    interleave: bool = True,
    seed: Optional[int] = None,
    max_failure_ratio: float = DEFAULT_MAX_FAILURE_RATIO,
    on_progress: Optional[ProgressCb] = None,
) -> Tuple[pd.DataFrame, FailureStats]:
    """Run `n_per_group` traces for each of A and B.

    Args:
        pod:               open Pod handle
        campaign:          Campaign instance (RandomVsZero etc.)
        n_per_group:       target trace count per group
        interleave:        if True (recommended), alternate A,B,A,B... so
                           any drift over the collection window cancels out
        seed:              PRNG seed; None = nondeterministic
        max_failure_ratio: abort if failures exceed this fraction of total
                           attempts (after a warmup of 50 traces)
        on_progress:       optional callback(done, total, failed)

    Returns:
        (DataFrame, FailureStats). The DataFrame has the schema documented
        at the top of this file. FailureStats has per-kind counters so the
        orchestrator can build a CrashFinding without re-collecting.

    Raises:
        RuntimeError if the failure ratio exceeds max_failure_ratio after
        warmup. The DataFrame is dropped in that case (corrupt anyway).
    """
    rng = np.random.default_rng(seed)
    rows: List[dict] = []
    fails = FailureStats()
    total = 2 * n_per_group
    attempted = 0
    warmup = 50                                      # don't bail before this

    a_count = 0
    b_count = 0
    for i in range(total):
        if interleave:
            group_is_a = (i % 2 == 0)
        else:
            group_is_a = (i < n_per_group)

        if group_is_a and a_count >= n_per_group:
            group_is_a = False                       # group A full
        elif (not group_is_a) and b_count >= n_per_group:
            group_is_a = True                        # group B full

        if group_is_a:
            input_bytes = campaign.sample_a(rng)
            label = campaign.group_a_label
        else:
            input_bytes = campaign.sample_b(rng)
            label = campaign.group_b_label

        result = pod.request_trace_safe(FN_GB_TARGET, input_bytes)
        attempted += 1

        if isinstance(result, TraceFailure):
            fails.record(result, input_bytes.hex())
            if attempted >= warmup:
                ratio = fails.total / attempted
                if ratio > max_failure_ratio:
                    raise RuntimeError(
                        f"abort: failure ratio {ratio:.0%} > "
                        f"{max_failure_ratio:.0%} after {attempted} attempts "
                        f"(by_kind={dict(fails.by_kind)})"
                    )
            if on_progress:
                on_progress(len(rows), total, fails.total)
            continue

        rows.append(_trace_to_row(result, target="gb_target", group=label))
        if group_is_a:
            a_count += 1
        else:
            b_count += 1

        if on_progress:
            on_progress(len(rows), total, fails.total)

    df = pd.DataFrame(rows)
    return df, fails


def _trace_to_row(tr: Trace, *, target: str, group: str) -> dict:
    """Convert a Trace into one parquet row."""
    return {
        "target":    target,
        "group":     group,
        "fn_id":     int(tr.fn_id),
        "input_hex": tr.input_hex,
        "cycles":    int(tr.cycles),
        "micros":    int(tr.micros),
        "insns":     int(tr.insns),
        "branches":  int(tr.branches),
        "power":     tr.power.tolist(),       # list<uint16> in parquet
    }


def write_parquet(df: pd.DataFrame, path: str) -> None:
    """Write to parquet via pyarrow. The 'power' column is list<uint16>
    which pyarrow handles natively (no custom schema needed)."""
    df.to_parquet(path, engine="pyarrow", index=False)


# =============================================================================
# CLI -- exercise the collect layer in isolation
# =============================================================================

def _print_progress(done: int, total: int, failed: int) -> None:
    pct = (100.0 * done / max(total, 1))
    print(f"\r[traces] {done}/{total} ({pct:5.1f}%)  failed={failed}",
          end="", flush=True)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--pico-port", required=True)
    p.add_argument("--campaign", choices=list(CAMPAIGNS),
                   default="random_vs_zero")
    p.add_argument("--n", type=int, default=DEFAULT_N_PER_GROUP,
                   help="traces per group")
    p.add_argument("--secret-len", type=int, default=DEFAULT_SECRET_LEN)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--out", required=True, help="output parquet path")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    print(f"[traces] opening {args.pico_port}")
    pod = pod_mod.open_pod(args.pico_port)
    try:
        campaign_cls = CAMPAIGNS[args.campaign]
        campaign = campaign_cls(secret_len=args.secret_len)   # type: ignore[call-arg]
        t0 = time.monotonic()
        df, fails = collect_two_groups(
            pod, campaign,
            n_per_group=args.n,
            seed=args.seed,
            on_progress=_print_progress,
        )
        elapsed = time.monotonic() - t0
        print()
        print(f"[traces] collected {len(df)} traces in {elapsed:.1f}s")
        if fails.total:
            print(f"[traces] failures: {dict(fails.by_kind)}")
        write_parquet(df, args.out)
        print(f"[traces] wrote {args.out}")
    finally:
        pod.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
