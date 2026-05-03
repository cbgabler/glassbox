"""eval.py -- end-to-end leak verdict for an arbitrary C++ function under test.

This is the user-facing tool. Two modes:

  ANALYZE-ONLY (you already collected traces with sweep_target.py):
      python eval.py --traces target_traces.parquet

  COLLECT + ANALYZE (one-shot):
      python eval.py --port /dev/cu.usbmodem1101 \\
          --campaign random_vs_zero --secret-len 16 --n-per-group 500

It runs TVLA on the cycles + power channels (no training data required,
works on any function) and -- if a trained classifier is on disk -- also
runs the ML model for a second opinion. Prints a human-readable verdict
and, if a leak is detected, a recommendation pointing toward common
constant-time fixes.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import tvla
import findings as fmod
import cpa

# ESP32 CPU frequency (Hz). Used to convert CCOUNT cycles to wall-clock time
# for the per-cycle leak localization. ESP32 default = 240 MHz.
ESP32_CPU_HZ = 240_000_000.0

# Pico analogRead() on the Earle Philhower core takes ~3-5 us per call. We
# pick 4 us as the typical value and let the user override on the CLI for
# higher-precision conversions if they've calibrated their pod.
DEFAULT_ADC_US_PER_SAMPLE = 4.0

# Channels we report on if present in the parquet. Order matters for display.
SCALAR_CHANNELS = ["cycles", "micros", "insns", "branches"]


# Default group labels emitted by sweep_target.py campaigns.
# (group_a_label, group_b_label) for each campaign type.
GROUP_PAIRS = {
    "random_vs_zero":   ("A_zero",   "B_random"),
    "random_vs_random": ("A_random", "B_random"),
    # byte_sweep doesn't have natural fixed-vs-random groups -- we split
    # the byte range in half as a (weak) sanity check.
    "byte_sweep":       (None,       None),
}


# =============================================================================
# Data loading / reshaping
# =============================================================================

def load_traces(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if df.empty:
        raise SystemExit(f"{path} is empty")
    needed = {"target", "group", "cycles", "power"}
    missing = needed - set(df.columns)
    if missing:
        raise SystemExit(f"{path} missing columns: {sorted(missing)}")
    return df


def split_groups(df: pd.DataFrame,
                 group_a: Optional[str], group_b: Optional[str]
                 ) -> Tuple[pd.DataFrame, pd.DataFrame, str, str]:
    """Return (df_a, df_b, label_a, label_b), inferring labels if not given."""
    groups_seen = sorted(df["group"].unique())
    if group_a and group_b:
        df_a = df[df["group"] == group_a]
        df_b = df[df["group"] == group_b]
        if df_a.empty or df_b.empty:
            raise SystemExit(
                f"groups {group_a!r}/{group_b!r} not found. "
                f"Available: {groups_seen}"
            )
        return df_a, df_b, group_a, group_b

    # Auto-infer.
    if all(g in groups_seen for g in ("A_zero", "B_random")):
        return (df[df["group"] == "A_zero"], df[df["group"] == "B_random"],
                "A_zero", "B_random")
    if all(g in groups_seen for g in ("A_random", "B_random")):
        return (df[df["group"] == "A_random"], df[df["group"] == "B_random"],
                "A_random", "B_random")
    if len(groups_seen) == 2:
        ga, gb = groups_seen
        return df[df["group"] == ga], df[df["group"] == gb], ga, gb
    # byte_sweep / multi-group: split into halves by group name lex order.
    half = len(groups_seen) // 2
    a_set = set(groups_seen[:half])
    b_set = set(groups_seen[half:])
    print(f"[eval] {len(groups_seen)} groups present; splitting into halves "
          f"({len(a_set)} vs {len(b_set)}) for TVLA")
    return (df[df["group"].isin(a_set)], df[df["group"].isin(b_set)],
            "first_half", "second_half")


def stack_power(df: pd.DataFrame) -> np.ndarray:
    """Convert the 'power' column (lists) into a 2D (n_traces, m_samples) array."""
    return np.stack([np.asarray(p, dtype=np.float64) for p in df["power"].values])


def collect_scalar_channels(df_a: pd.DataFrame, df_b: pd.DataFrame
                            ) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """Build the {channel_name: (group_a_array, group_b_array)} dict for tvla_multi.

    Skips channels that aren't in the parquet (e.g. when the data was collected
    with the older RES protocol that only reported cycles).
    """
    out: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    for ch in SCALAR_CHANNELS:
        if ch in df_a.columns and ch in df_b.columns:
            a = df_a[ch].to_numpy(dtype=np.float64)
            b = df_b[ch].to_numpy(dtype=np.float64)
            out[ch] = (a, b)
    return out


# =============================================================================
# Per-cycle leak localization
# =============================================================================

def localize_power_leak(sample_idx: int, total_samples: int,
                        avg_cycles: float,
                        adc_us_per_sample: float = DEFAULT_ADC_US_PER_SAMPLE,
                        cpu_hz: float = ESP32_CPU_HZ) -> Dict[str, float]:
    """Map a power-channel argmax sample index back to a cycle number.

    The Pico samples the INA169 ADC continuously at ~adc_us_per_sample
    microseconds per sample, starting the moment the ESP32 raises its
    trigger pin (i.e. the moment the function under test starts). The
    function then runs for `avg_cycles` cycles at `cpu_hz` Hz before
    returning; the trigger stays HIGH for at least 200 us after that (so
    the Pico can always catch the rising edge), and the Pico keeps
    sampling for the rest of the trace window.

    Given the sample index where the leak peaks, we can compute:
      * approximate elapsed microseconds since trigger HIGH
      * approximate cycle number within the function (if the leak is
        during the function, not in the trigger-padding tail)
      * approximate fraction through the function (0..1)

    Returns a dict suitable for printing.
    """
    sample_us       = sample_idx * adc_us_per_sample
    function_us     = (avg_cycles / cpu_hz) * 1e6
    cycle_at_sample = sample_us * (cpu_hz / 1e6)
    if function_us > 0 and sample_us <= function_us:
        in_function = True
        fraction = sample_us / function_us
    else:
        in_function = False
        fraction = float("nan")
    return {
        "sample_idx":      float(sample_idx),
        "total_samples":   float(total_samples),
        "sample_us":       float(sample_us),
        "function_us":     float(function_us),
        "cycle_at_sample": float(cycle_at_sample),
        "avg_cycles":      float(avg_cycles),
        "in_function":     float(in_function),
        "fraction":        float(fraction),
    }


def format_localization(loc: Dict[str, float]) -> str:
    lines = [
        f"  Power-channel leak peaks at sample {int(loc['sample_idx'])} "
        f"of {int(loc['total_samples'])}",
        f"  Approx. elapsed time since trigger HIGH: ~{loc['sample_us']:.1f} us",
        f"  Function takes ~{loc['function_us']:.1f} us "
        f"({int(loc['avg_cycles'])} cycles avg @ {ESP32_CPU_HZ/1e6:.0f} MHz)",
    ]
    if loc["in_function"]:
        lines.append(
            f"  Approx. cycle in function: ~{int(loc['cycle_at_sample'])} of "
            f"~{int(loc['avg_cycles'])} ({100*loc['fraction']:.0f}% through)"
        )
    else:
        lines.append(
            "  Sample falls AFTER the function returned -- the leak is "
            "in the trigger-hold tail, which usually means the function is "
            "very fast (sub-us) and the leak is too narrow for our ADC rate."
        )
    return "\n".join(lines)


# =============================================================================
# Leak-class heuristics (which channel fired tells you what kind of leak)
# =============================================================================

LEAK_CLASS_HINTS = {
    "branches": ("branch-on-secret",
                 "the branch counter differs significantly between groups, "
                 "which means the function takes different conditional paths "
                 "depending on the secret. Replace `if (secret_bit) ...` with "
                 "a constant-time conditional move."),
    "insns":    ("variable-instruction-count",
                 "the instructions-retired counter differs, meaning different "
                 "code paths execute. Often a sibling of the branch-on-secret "
                 "class but can also indicate variable-length loops."),
    "cycles":   ("timing leak (variable execution time)",
                 "wall-clock time depends on the secret. The classic side-"
                 "channel class -- and exactly what early-return loops "
                 "(strcmp/memcmp/bcrypt naive) produce."),
    "micros":   ("timing-with-noise leak",
                 "wall time differs but cycles do not -- usually means the "
                 "function takes a slow path (interrupt, syscall, flash read) "
                 "depending on the secret."),
    "power":    ("power-channel leak",
                 "current draw varies with the secret even when timing is "
                 "constant. Classic culprits: secret-indexed table lookups, "
                 "Hamming-weight effects on register writes, asymmetric "
                 "dual-rail logic on cryptographic accelerators."),
}


def diagnose_leak_class(report: tvla.MultiChannelReport) -> str:
    """Pick the single 'most explanatory' leaking channel and return human prose."""
    leaking = [(name, v) for name, v in report.first_order.items() if v.leak_detected]
    leaking_2nd = [(name, v) for name, v in report.second_order.items() if v.leak_detected]

    if not leaking and not leaking_2nd:
        return "No leak detected on any channel."

    # If only second-order channels fired, we're looking at a masked
    # implementation that defeats first-order TVLA but still leaks variance.
    if not leaking and leaking_2nd:
        return ("Variance-only leak (second-order TVLA): the function passes "
                "first-order tests but its trace variance still depends on the "
                "secret. This is the signature of a **partially-masked** "
                "implementation -- the masking neutralizes the mean leak but "
                "not the variance. Used to be considered safe; modern "
                "evaluation labs treat it as a fail.")

    # Otherwise pick the strongest first-order channel and explain its class.
    leaking.sort(key=lambda kv: -kv[1].max_abs_t)
    top_name, top_v = leaking[0]
    klass, blurb = LEAK_CLASS_HINTS.get(
        top_name, ("unknown leak class", "no specific hint for this channel.")
    )
    n_channels = len(leaking) + len(leaking_2nd)
    summary = (
        f"Strongest signal: **{top_name}** (|t| = {top_v.max_abs_t:.1f}). "
        f"Leak class: {klass}. {blurb}"
    )
    if n_channels > 1:
        all_names = ", ".join(f"{n}({v.max_abs_t:.0f})"
                              for n, v in leaking + leaking_2nd)
        summary += (f"\n  All channels firing: {all_names}.")
    return summary


# =============================================================================
# Optional: ML classifier second opinion
# =============================================================================

def maybe_run_classifier(df: pd.DataFrame, model_path: str) -> Optional[dict]:
    """If baseline.joblib is present, run it on every trace and report votes."""
    if not os.path.exists(model_path):
        return None
    try:
        import joblib
        from features import featurize, FEATURE_NAMES  # noqa: F401
    except Exception as e:
        print(f"[eval] classifier import failed: {e}; skipping ML opinion")
        return None
    clf = joblib.load(model_path)

    X = []
    for _, row in df.iterrows():
        power = np.asarray(row["power"], dtype=np.float64)
        X.append(featurize(int(row["cycles"]), power))
    X = np.stack(X)
    preds = clf.predict(X)
    votes = {label: int((preds == label).sum()) for label in sorted(set(preds))}
    return {"n": len(preds), "votes": votes, "model_path": model_path}


# =============================================================================
# Verdict + remediation copy
# =============================================================================

REMEDIATION_HINTS = [
    ("strcmp", "Replace strcmp / memcmp with a constant-time comparator "
               "such as CRYPTO_memcmp (BoringSSL/OpenSSL) or "
               "sodium_memcmp (libsodium). Both branches must touch every byte."),
    ("compare", "If you're comparing secrets (passwords, MACs, tokens), the "
                "comparison must touch every byte regardless of mismatches. "
                "Use a constant-time equality primitive."),
    ("password", "Stored credential checks should never short-circuit on the "
                 "first wrong byte. Hash both sides (bcrypt/argon2) and compare "
                 "with a constant-time equality."),
    ("lookup", "Secret-indexed lookup tables leak through the cache. Replace "
               "with a scan-and-mask: read all entries, select with a "
               "constant-time conditional move."),
    ("branch", "Conditional execution on a secret bit leaks via timing AND power. "
               "Compute both branches and select the result with a "
               "constant-time conditional move (`x = mask & a | (~mask) & b`)."),
    ("modexp", "Square-and-multiply RSA leaks the private key. Switch to "
               "Montgomery ladder or RSA-CRT with constant-time exponentiation."),
    ("sbox",   "T-table AES SubBytes leaks via cache. Use bitsliced AES, masked "
               "AES, or hardware AES-NI / accelerator if available."),
]


def remediation_for(target_name: str) -> str:
    name = target_name.lower()
    for needle, text in REMEDIATION_HINTS:
        if needle in name:
            return text
    return ("Audit the function for secret-dependent control flow, secret-indexed "
            "memory access, and variable-time arithmetic. Replace with "
            "constant-time primitives from a vetted library "
            "(libsodium, BoringSSL, mbedTLS).")


# =============================================================================
# v2: TVLA report -> Finding list
# =============================================================================

def tvla_report_to_findings(
    report: tvla.MultiChannelReport,
    *,
    id_offset: int = 0,
    fraction_through_function: Optional[float] = None,
    target_label: str = "",
) -> List[fmod.Finding]:
    """Each ChannelVerdict becomes one TvlaFinding. Severity ladder:
       leak + |t| > 100 = CRITICAL; > 20 = HIGH; > threshold = MEDIUM;
       flat = pass; otherwise pass.
    """
    out: List[fmod.Finding] = []
    rem = remediation_for(target_label) if target_label else None

    def push(verdict: tvla.ChannelVerdict, order: int):
        nonlocal out
        fid = f"f_{id_offset + len(out) + 1:03d}"
        frac = fraction_through_function if verdict.channel == "power" else None
        out.append(fmod.build_tvla_finding(
            fid,
            channel=verdict.channel,
            order=order,
            t_abs=(None if verdict.is_flat else float(verdict.max_abs_t)),
            threshold=float(verdict.threshold),
            leak_detected=bool(verdict.leak_detected),
            is_flat=bool(verdict.is_flat),
            argmax_sample=verdict.argmax,
            fraction_through_function=frac,
            remediation=(rem if verdict.leak_detected else None),
        ))
    for v in report.first_order.values():
        push(v, 1)
    for v in report.second_order.values():
        push(v, 2)
    return out


# =============================================================================
# v2: write run_detail.json
# =============================================================================

def emit_run_detail(
    *,
    out_path: str,
    target_label: str,
    n_traces_total: int,
    started_at: str,
    finished_at: str,
    duration_s: float,
    findings: List[fmod.Finding],
    tvla_summary: Optional[Dict[str, Any]] = None,
) -> None:
    """Write a v2 RunDetailPayload-shaped JSON file. Schema-compatible with
    glassbox/frontend/mocks/types.ts (RunDetailPayload + Finding)."""
    summary = fmod.summarize(findings)
    verdict = fmod.derive_verdict(findings)

    payload: Dict[str, Any] = {
        "schema": "glassbox.run_detail.v1",   # field-additive; v1 consumers still work
        "id":       f"run_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "repo":     "",
        "commit_sha": "",
        "pr_number": 0,
        "branch":    "",
        "function":  target_label,
        "victim":    {"chip": "ESP32-WROOM-32", "freq_mhz": 240,
                      "harness": "v2 quarantine-capable"},
        "monitor":   {"chip": "RP2040", "trace_len": 256, "sample_period_us": 4.0,
                      "model": "baseline.joblib",
                      "model_classes": ["safe", "timing_leak"]},
        "started_at":  started_at,
        "finished_at": finished_at,
        "duration_s":  float(duration_s),
        "verdict":     verdict,
        "summary": {
            "leak_channel":      None,
            "leak_severity":     None,
            "leak_confidence":   0.0,
            "calls_total":       int(n_traces_total),
            "calls_before_fire": 0,
            "bytes_recovered":   0,
            "secret_len":        0,
            "quarantine_fired":  False,
            "quarantine_method": "",
        },
        "tvla_summary": tvla_summary or {
            "channels": {}, "leak_detected": False,
            "strongest_channel": None, "strongest_severity": None,
        },
        "histogram_thumbnail": {"fn": target_label, "channel": "cycles",
                                "min": 0, "max": 0, "argmax_byte": 0},
        "sample_traces": [],
        "quarantine_events_id":  "quarantine_events.json",
        "orchestrator_id":       "orchestrator_report.json",
        "live_attack_stream_id": "live_attack_stream.jsonl",
        "byte_histogram_id":     "byte_histogram.json",
        "tvla_report_id":        "tvla_report.json",
        "github": {"comment_state": "pending", "comment_url": "", "comment_md": ""},
        # v2 additions:
        "findings":         fmod.to_json_list(findings),
        "findings_summary": summary,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"[eval] wrote v2 run_detail -> {out_path}  "
          f"verdict={verdict}  worst={summary['worst_severity']}  "
          f"findings={summary['total']}")


# =============================================================================
# Top-level analyze
# =============================================================================

def analyze(df: pd.DataFrame, *, group_a: Optional[str], group_b: Optional[str],
            threshold: float, model_path: str,
            adc_us_per_sample: float = DEFAULT_ADC_US_PER_SAMPLE,
            run_cpa: bool = False,
            cpa_true_key: Optional[bytes] = None,
            run_detail_out: Optional[str] = None,
            extra_findings: Optional[List[fmod.Finding]] = None,
            ) -> bool:
    """Run multi-channel TVLA (+ HO-TVLA + ML opinion + leak localization).

    Returns True iff a leak was detected on any channel.

    Optional v2 args:
        run_cpa:        if True, run AES-S-box CPA on the power traces.
        cpa_true_key:   if known, populates per-byte true_rank in CPA report.
        run_detail_out: when set, write a polymorphic run_detail.json here.
        extra_findings: additional findings to merge in (e.g. crash, static).
    """
    target_names = sorted(df["target"].unique())
    target_label = ", ".join(target_names) if target_names else "unknown"

    df_a, df_b, label_a, label_b = split_groups(df, group_a, group_b)
    scalar = collect_scalar_channels(df_a, df_b)
    power_a = stack_power(df_a)
    power_b = stack_power(df_b)

    print()
    print("=" * 76)
    print(f"GlassBox eval: {target_label}")
    print("=" * 76)
    print(f"Group A = {label_a:<14s}  n = {len(df_a):4d}")
    print(f"Group B = {label_b:<14s}  n = {len(df_b):4d}")

    # Per-channel descriptive stats so the user can sanity-check the data
    # before believing the t-statistics.
    if scalar:
        print()
        print("--- Per-call channel statistics ---")
        print(f"  {'channel':<10s}  {'mean(A)':>10s}  {'mean(B)':>10s}  "
              f"{'std(A)':>10s}  {'std(B)':>10s}  {'delta':>10s}")
        print(f"  {'-'*10:<10s}  {'-'*10:>10s}  {'-'*10:>10s}  "
              f"{'-'*10:>10s}  {'-'*10:>10s}  {'-'*10:>10s}")
        for name, (a, b) in scalar.items():
            ma, mb = float(a.mean()), float(b.mean())
            sa, sb = float(a.std()), float(b.std())
            delta = mb - ma
            pct = (100.0 * delta / ma) if ma != 0 else float("nan")
            print(f"  {name:<10s}  {ma:>10.2f}  {mb:>10.2f}  "
                  f"{sa:>10.2f}  {sb:>10.2f}  {delta:>+8.2f}  ({pct:+5.1f}%)")

    print()
    print("--- TVLA, first-order (mean leak; works on any function, no training) ---")
    report = tvla.tvla_multi(
        target_label, scalar, power_a, power_b,
        threshold=threshold, second_order=True,
    )
    # Print first-order rows.
    fo_lines = []
    fo_lines.append(f"  {'channel':<10s}  {'order':>5s}  "
                    f"{'|t|':>10s}  {'thr':>5s}   verdict")
    fo_lines.append(f"  {'-'*10:<10s}  {'-'*5:>5s}  "
                    f"{'-'*10:>10s}  {'-'*5:>5s}   {'-'*7}")
    def _line(v: tvla.ChannelVerdict) -> str:
        if v.is_flat:
            tag = "FLAT (no signal)"
        elif v.leak_detected:
            tag = "LEAK"
            if v.argmax is not None:
                tag += f"   peak at sample {v.argmax}"
        else:
            tag = "OK"
        return (f"  {v.channel:<10s}  {v.order:>5d}  "
                f"{v.max_abs_t:>10.2f}  {v.threshold:>5.1f}   {tag}")
    for v in report.first_order.values():
        fo_lines.append(_line(v))
    print("\n".join(fo_lines))

    if report.second_order:
        print()
        print("--- TVLA, second-order (variance leak; catches masked implementations) ---")
        for v in report.second_order.values():
            print(_line(v))

    # Per-cycle leak localization for the power channel (when it leaked).
    pwr1 = report.first_order.get("power")
    pwr2 = report.second_order.get("power")
    avg_cycles = float(np.concatenate([scalar["cycles"][0], scalar["cycles"][1]]).mean()) \
                 if "cycles" in scalar else 0.0
    n_samples = power_a.shape[1] if power_a is not None else 0
    if avg_cycles > 0 and n_samples > 0:
        print()
        if pwr1 is not None and pwr1.leak_detected and pwr1.argmax is not None:
            print("--- Leak localization (power channel, first order) ---")
            loc = localize_power_leak(pwr1.argmax, n_samples, avg_cycles,
                                      adc_us_per_sample=adc_us_per_sample)
            print(format_localization(loc))
        elif pwr2 is not None and pwr2.leak_detected and pwr2.argmax is not None:
            print("--- Leak localization (power channel, second order / variance) ---")
            loc = localize_power_leak(pwr2.argmax, n_samples, avg_cycles,
                                      adc_us_per_sample=adc_us_per_sample)
            print(format_localization(loc))

    # Optional ML second opinion.
    ml = maybe_run_classifier(df, model_path)
    if ml is not None:
        print()
        print(f"--- ML classifier ({os.path.basename(ml['model_path'])}) "
              f"-- second opinion ---")
        print(f"  Predictions across all {ml['n']} traces:")
        for label, n in sorted(ml["votes"].items(), key=lambda kv: -kv[1]):
            pct = 100.0 * n / ml["n"]
            print(f"    {label:<14s}  {n:5d}   ({pct:5.1f}%)")

    print()
    print("=" * 76)
    if report.leak_detected:
        n_channels = len(report.leaking_channels())
        print(f"VERDICT: {target_label} -- LEAK DETECTED on {n_channels} "
              f"channel{'s' if n_channels != 1 else ''}")
        print("=" * 76)
        print()
        print("Diagnosis:")
        print(f"  {diagnose_leak_class(report)}")
        print()
        print("Recommendation:")
        print(f"  {remediation_for(target_label)}")
    else:
        print(f"VERDICT: {target_label} -- no leak detected at |t| > {threshold}")
        print("=" * 76)
        print()
        print("Notes:")
        print("  TVLA proves the function's measurable behavior is statistically")
        print("  independent of the secret across every channel we monitored")
        active = [n for n, v in report.first_order.items() if not v.is_flat]
        if active:
            print(f"  ({', '.join(active)}). This is strong evidence -- but not proof --")
        else:
            print("  (no channel had measurable signal -- check pod wiring). This is")
        print("  of constant-time/constant-power behavior. For a stronger guarantee,")
        print("  also test with --campaign random_vs_random as a control.")

    # ---------- v2 findings pipeline ----------
    findings: List[fmod.Finding] = list(extra_findings or [])
    fraction = None
    pwr1 = report.first_order.get("power")
    if (pwr1 is not None and pwr1.leak_detected and pwr1.argmax is not None
            and avg_cycles > 0 and n_samples > 0):
        loc = localize_power_leak(pwr1.argmax, n_samples, avg_cycles,
                                  adc_us_per_sample=adc_us_per_sample)
        if loc.get("in_function") and loc.get("fraction") == loc.get("fraction"):
            fraction = float(loc["fraction"])

    findings += tvla_report_to_findings(
        report, id_offset=len(findings),
        fraction_through_function=fraction,
        target_label=target_label,
    )

    # ---------- optional CPA ----------
    if run_cpa and power_a is not None and power_b is not None:
        # Decode the hex_input column into a (n, 16) byte matrix using the
        # FULL parquet (both groups) so we have as many traces as possible.
        if "hex_input" not in df.columns:
            print("[eval] CPA skipped: parquet has no hex_input column")
        else:
            hex_inputs = df["hex_input"].astype(str).tolist()
            byte_lens = {len(h) // 2 for h in hex_inputs}
            if len(byte_lens) != 1 or 16 not in byte_lens:
                print(f"[eval] CPA skipped: expected 16-byte plaintexts, "
                      f"saw {sorted(byte_lens)}")
            else:
                pts = np.array(
                    [list(bytes.fromhex(h))[:16] for h in hex_inputs],
                    dtype=np.uint8,
                )
                power_full = np.stack([
                    np.asarray(p, dtype=np.float64) for p in df["power"].values
                ])
                print()
                print("--- CPA (AES first-round S-box, Hamming-weight model) ---")
                rep = cpa.attack_full_key(pts, power_full, true_key=cpa_true_key)
                n_recov = sum(1 for b in rep.per_byte if b.true_rank == 1)
                print(f"  n_traces={rep.n_traces}  full_key_recovered="
                      f"{rep.full_key_recovered}  n_byte_rank_1={n_recov}/16")
                if rep.recovered_key is not None:
                    print(f"  recovered key: {rep.recovered_key.hex()}")
                if cpa_true_key is not None:
                    print(f"  true key     : {bytes(cpa_true_key).hex()}")
                findings.append(fmod.build_cpa_finding(
                    f"f_{len(findings) + 1:03d}",
                    per_byte=cpa.report_to_per_byte_json(rep),
                    full_key_recovered=rep.full_key_recovered,
                    n_traces=rep.n_traces,
                ))

    if run_detail_out is not None:
        # Build a tvla_summary block (v1 shape) so old consumers still render.
        tvla_summary = {
            "channels": {
                v.channel: {
                    "verdict": ("flat" if v.is_flat
                                else "CRITICAL" if v.max_abs_t > 100
                                else "HIGH" if v.max_abs_t > 20
                                else "MEDIUM" if v.leak_detected
                                else "pass"),
                    "t_abs":   (None if v.is_flat else float(v.max_abs_t)),
                    "mean_a":  0.0,
                    "mean_b":  0.0,
                }
                for v in report.first_order.values()
            },
            "leak_detected":      bool(report.leak_detected),
            "strongest_channel":  None,
            "strongest_severity": None,
        }
        if report.leak_detected:
            leakers = [v for v in report.first_order.values() if v.leak_detected]
            if leakers:
                top = max(leakers, key=lambda v: v.max_abs_t)
                tvla_summary["strongest_channel"]  = top.channel
                tvla_summary["strongest_severity"] = (
                    "CRITICAL" if top.max_abs_t > 100
                    else "HIGH" if top.max_abs_t > 20 else "MEDIUM"
                )
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")
        emit_run_detail(
            out_path=run_detail_out,
            target_label=target_label,
            n_traces_total=int(len(df)),
            started_at=now, finished_at=now,
            duration_s=0.0,
            findings=findings,
            tvla_summary=tvla_summary,
        )
    return report.leak_detected


# =============================================================================
# CLI
# =============================================================================

def main():
    ap = argparse.ArgumentParser(
        description=("End-to-end TVLA + ML leak verdict for the user_target slot. "
                     "In analyze-only mode, point --traces at a parquet from "
                     "sweep_target.py. In one-shot mode, pass --port and --campaign "
                     "to collect+analyze."),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--traces", help="Parquet from sweep_target.py (analyze-only)")
    src.add_argument("--port",   help="Pico USB CDC port (collect+analyze)")

    # Collection-only flags (ignored in analyze-only mode).
    ap.add_argument("--campaign", choices=list(GROUP_PAIRS),
                    default="random_vs_zero",
                    help="Input distribution to use when collecting")
    ap.add_argument("--target-name", default="user_target")
    ap.add_argument("--secret-len", type=int, default=16)
    ap.add_argument("--n-per-group", type=int, default=500)
    ap.add_argument("--n-repeats", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--save", default=None,
                    help="In collect mode, also write the raw traces here (parquet).")

    # Analysis flags (apply in both modes).
    ap.add_argument("--group-a", default=None,
                    help="Override group label for TVLA group A")
    ap.add_argument("--group-b", default=None,
                    help="Override group label for TVLA group B")
    ap.add_argument("--threshold", type=float, default=tvla.TVLA_THRESHOLD,
                    help=f"|t| threshold for declaring a leak (default {tvla.TVLA_THRESHOLD})")
    ap.add_argument("--model", default="baseline.joblib",
                    help="Optional classifier checkpoint for second-opinion ML scoring.")
    ap.add_argument("--adc-us-per-sample", type=float,
                    default=DEFAULT_ADC_US_PER_SAMPLE,
                    help="Pico ADC sample interval in us, used for cycle localization.")

    # v2: emit polymorphic run_detail.json + optional CPA + optional pre-flash lint.
    ap.add_argument("--run-detail", default=None,
                    help="Write polymorphic run_detail.json (v2 schema) to this path.")
    ap.add_argument("--cpa", action="store_true",
                    help="Run AES-S-box CPA on the power traces (target name "
                         "should look AES-shaped; works on any 16-byte input).")
    ap.add_argument("--cpa-true-key", default=None,
                    help="Hex string of the true 16-byte key, used by CPA to "
                         "report per-byte ranks. Optional.")
    ap.add_argument("--lint", default=None,
                    help="Path to a C/C++ source file (e.g. gb_target.cpp). "
                         "Run the constant-time linter before TVLA and merge "
                         "results into --run-detail.")

    args = ap.parse_args()

    if args.traces:
        df = load_traces(args.traces)
    else:
        # Collect now.
        from runner import open_pod
        from sweep_target import CAMPAIGNS, collect
        ser = open_pod(args.port)
        print(f"[eval] opened {args.port}, running campaign={args.campaign}")
        campaign_iter = CAMPAIGNS[args.campaign](args)
        rows = collect(ser, campaign_iter, args.target_name)
        if not rows:
            raise SystemExit("Collected zero traces -- check pod / target.")
        df = pd.DataFrame(rows)
        if args.save:
            df.to_parquet(args.save)
            print(f"[eval] saved raw traces -> {args.save}")

    extra: List[fmod.Finding] = []
    if args.lint:
        try:
            import ct_lint
            extra += ct_lint.lint_file(args.lint, id_offset=0)
            non_pass = [f for f in extra if f.severity != "pass"]
            print(f"[eval] ct_lint: {len(non_pass)} hit(s) in {args.lint}")
            for f in non_pass:
                print(f"  [{f.severity}] {f.title}")
        except Exception as e:
            print(f"[eval] ct_lint failed: {e}; continuing without static findings")

    cpa_true_key: Optional[bytes] = None
    if args.cpa_true_key:
        try:
            cpa_true_key = bytes.fromhex(args.cpa_true_key)
        except ValueError:
            print(f"[eval] --cpa-true-key not valid hex: {args.cpa_true_key}")
            cpa_true_key = None

    leaked = analyze(df, group_a=args.group_a, group_b=args.group_b,
                     threshold=args.threshold, model_path=args.model,
                     adc_us_per_sample=args.adc_us_per_sample,
                     run_cpa=args.cpa, cpa_true_key=cpa_true_key,
                     run_detail_out=args.run_detail,
                     extra_findings=extra)
    sys.exit(1 if leaked else 0)


if __name__ == "__main__":
    main()
