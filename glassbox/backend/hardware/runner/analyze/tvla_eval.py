"""tvla_eval.py -- run TVLA verdicts on a runner.py-style parquet.

The original `eval.py` is built around `sweep_target.py`'s schema (target/group
columns). This script is the equivalent for the parquet that `runner.py`
produces, so you can run the standard ISO/IEC 17825 TVLA test on the data
you just collected.

For each function in the parquet (strcmp_naive, strcmp_safe), we split traces
into two groups and ask Welch's t-test: "is the function's behaviour
statistically distinguishable between these two groups?"

  Group A: traces where the first input byte equals the secret's first byte
           (i.e. the comparison would proceed past byte 0)
  Group B: traces where the first input byte is anything else
           (the comparison should bail on byte 0 if the function is leaky)

For each measurement channel (cycles, micros, plus any power-derived scalar
features in the parquet), we compute |t|. |t| > 4.5 = LEAK with > 99.999%
confidence. That's the same threshold every commercial side-channel lab uses.

Usage:
    python tvla_eval.py --traces traces_real.parquet
"""
from __future__ import annotations

import argparse
import sys
from typing import List

import numpy as np
import pandas as pd

import tvla

# Must match SECRET in runner.py / harness.ino
SECRET_FIRST_BYTE = 0x68   # 'h' in "hunter2!"

# Function id -> human name (must match esp/harness/harness.ino)
FN_NAMES = {1: "strcmp_naive", 2: "strcmp_safe"}


def channels_in(df: pd.DataFrame) -> List[str]:
    """Pick out every numeric scalar column we should run TVLA on."""
    candidates = ["cycles", "micros", "insns", "branches",
                  "pwr_mean", "pwr_std", "pwr_ptp", "pwr_integ",
                  "pwr_fft_lo", "pwr_fft_mid", "pwr_fft_hi"]
    return [c for c in candidates if c in df.columns]


# Per-channel quantization noise floor. Welch's t fires on tiny shifts in
# the integer-rounding distribution even when the underlying physical effect
# is much smaller than the channel's measurement resolution. micros is the
# big offender: esp_timer_get_time() returns whole microseconds, so a delta
# of 0.1 us is "occasionally 1 vs 2", which the t-test eagerly picks up but
# isn't a clean signal. We tag those as BELOW_QUANTUM so they aren't read
# as practical leaks. The cycles channel has effectively no quantum (1 cycle
# = 4 ns), so we keep its floor low.
QUANT_NOISE_FLOOR = {
    "micros":   1.0,   # esp_timer_get_time() resolution
    "cycles":   2.0,   # CCOUNT is exact, but trigger/instr overhead jitters ~1-2
    "insns":    1.0,
    "branches": 1.0,
}