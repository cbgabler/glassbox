"""Trace collection -- run a TVLA-style sweep and write a parquet.

This is the bridge between collect/pod.py (one-shot trace requests) and
analyze/eval.py (parquet in, verdict out). One call to `collect_two_groups`
produces enough data for a full TVLA + CPA pass on a single target.

Output schema (parquet columns):

    target       str            "gb_target"  (always, in scanner mode)
    group        str            "A_zero" | "B_random"  (or whatever the campaign uses)
    fn_id        int            FN_GB_TARGET
    input_hex    str            16-byte input as hex
    cycles       int            CCOUNT delta
    micros       int            esp_timer_get_time delta
    insns        int (nullable) PMU instruction-retired counter, if present
    branches     int (nullable) PMU branch counter, if present
    power        list<uint16>   length TRACE_LEN ADC samples

This is the same schema analyze/eval.py already reads (load_traces, line 62).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import pod as pod_mod
from .pod import FN_GB_TARGET, Pod, Trace, TraceFailure


# Default TVLA campaign sizes. 500 per group is the lower end of "publishable";
# 1000+ is what every paper uses. 100 is fine for a smoketest verdict but
# CPA quality drops off a cliff below ~500.
DEFAULT_N_PER_GROUP = 1000


# =============================================================================
# Input generators -- each campaign defines how to pick group A vs group B inputs
# =============================================================================

@dataclass
class Campaign:
    name: str
    group_a_label: str
    group_b_label: str

    def sample_a(self, rng: np.random.Generator) -> bytes:
        raise NotImplementedError

    def sample_b(self, rng: np.random.Generator) -> bytes:
        raise NotImplementedError


class RandomVsZero(Campaign):
    """Group A: all-zero input. Group B: uniform random bytes.
    Standard non-specific TVLA -- catches almost any leak in one pass."""

    def __init__(self, secret_len: int = 16):
        super().__init__("random_vs_zero", "A_zero", "B_random")
        self.secret_len = secret_len

    def sample_a(self, rng):  # type: ignore[override]
        return bytes(self.secret_len)               # \x00 * 16

    def sample_b(self, rng):  # type: ignore[override]
        return bytes(rng.integers(0, 256, self.secret_len, dtype=np.uint8))


class RandomVsRandom(Campaign):
    """Both groups random. Used as a NEGATIVE control -- a correct
    constant-time impl should produce |t| < 4.5 here."""

    def __init__(self, secret_len: int = 16):
        super().__init__("random_vs_random", "A_random", "B_random")
        self.secret_len = secret_len

    def sample_a(self, rng):  # type: ignore[override]
        return bytes(rng.integers(0, 256, self.secret_len, dtype=np.uint8))

    sample_b = sample_a


CAMPAIGNS = {
    "random_vs_zero":   RandomVsZero,
    "random_vs_random": RandomVsRandom,
}


# =============================================================================
# Collection
# =============================================================================

def collect_two_groups(pod: Pod,
                       campaign: Campaign,
                       n_per_group: int = DEFAULT_N_PER_GROUP,
                       *,
                       interleave: bool = True,
                       seed: Optional[int] = None,
                       on_progress=None,
                       ) -> pd.DataFrame:
    """Run `n_per_group` traces for each of A and B; return a long-format DataFrame.

    Args:
        pod:           open Pod handle
        campaign:      Campaign instance (RandomVsZero etc.)
        n_per_group:   target trace count per group
        interleave:    if True (recommended), alternate A,B,A,B... so any
                       drift over the collection window cancels out
        seed:          PRNG seed; None = nondeterministic
        on_progress:   optional callback(traces_done, total) for live UIs

    Skips and logs traces that come back as TraceFailure (chip crashed on a
    pathological input) but counts them so we don't loop forever on a
    target that crashes 100% of the time -- bail out after a fixed ratio.
    """
    # TODO:
    rng = np.random.default_rng(seed)
    rows = []
    total = 2 * n_per_group
    for i in range(total):
        group_is_a = (i % 2 == 0) if interleave else (i < n_per_group)
        if group_is_a:
            input_bytes = campaign.sample_a(rng)
            label = campaign.group_a_label
        else:
            input_bytes = campaign.sample_b(rng)
            label = campaign.group_b_label
        try:
            tr = pod.request_trace(FN_GB_TARGET, input_bytes)
        except TraceFailure:
            rows.append(_trace_to_row(tr, target="gb_target", group=label))
            if on_progress: on_progress(len(rows), total)
    return pd.DataFrame(rows)
    ...


def _trace_to_row(tr: Trace, *, target: str, group: str) -> dict:
    """Convert a Trace into a dict matching the parquet schema documented
    at the top of this file."""
    # TODO:
    #   return {"target": target, "group": group, "fn_id": tr.fn_id,
    #           "input_hex": tr.input_hex, "cycles": tr.cycles,
    #           "micros": tr.micros, "insns": tr.insns, "branches": tr.branches,
    #           "power": tr.power.tolist()}
    ...


def write_parquet(df: pd.DataFrame, path: str) -> None:
    """Write to parquet with pyarrow. The 'power' column needs explicit
    list<uint16> handling so it round-trips through analyze/eval.py."""
    # TODO: df.to_parquet(path, engine="pyarrow", index=False)
    ...


# =============================================================================
# CLI -- mostly for debugging the collect layer in isolation
# =============================================================================

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--pico-port", required=True)
    p.add_argument("--campaign", choices=list(CAMPAIGNS), default="random_vs_zero")
    p.add_argument("--n", type=int, default=DEFAULT_N_PER_GROUP,
                   help="traces per group")
    p.add_argument("--secret-len", type=int, default=16)
    p.add_argument("--secret", default=None,
                   help="hex16; if omitted, all-zero secret is set")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--out", required=True, help="output parquet path")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    # TODO:
    #   pod = pod_mod.open_pod(args.pico_port)
    #   secret = bytes.fromhex(args.secret) if args.secret else bytes(16)
    #   pod.set_secret(secret)
    #   campaign = CAMPAIGNS[args.campaign](secret_len=args.secret_len)
    #   df = collect_two_groups(pod, campaign, n_per_group=args.n, seed=args.seed,
    #                           on_progress=lambda d, t: print(f"\r{d}/{t}", end=""))
    #   write_parquet(df, args.out)
    #   pod.close()
    #   print(f"\nwrote {len(df)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
