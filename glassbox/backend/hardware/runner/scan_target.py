"""scan_target.py -- per-target scanner orchestrator.

This is the only thing hardwarego/hardware.go invokes per source file.
For ONE C/C++ target, we:

    1. install   copy source into esp/harness/gb_target.cpp (backed up first)
    2. ct_lint   static analysis (no hardware needed; ~10ms). Also extracts
                 a single static reference constant from the source when
                 present, attaches it to comparator-shaped findings as a
                 campaign hint for the auto picker below.
    3. flash     compile + upload via Pico bridge (auto_flash.flash_target)
    4. collect   run a TVLA campaign and produce traces. The default
                 --campaign 'auto' picks 'match_vs_random' (with the
                 reference ct_lint extracted) when the file looks like a
                 byte-compare leak, otherwise 'random_vs_zero'.
    5. tvla      Welch's t-test on cycles + power channels
    6. cpa       correlation power analysis on the same traces (best-effort)
    7. safety    if ct_lint flagged a comparator AND the run still used
                 random_vs_zero AND every TVLA channel returned 'pass',
                 synthesize a MEDIUM CT_AUTO finding so the report cannot
                 silently false-negative on code we statically flagged.
    8. merge     roll all Findings into one TargetReport
    9. emit      print TargetReport JSON to stdout, exit 0

We exit 0 even when the verdict is leak_detected. The orchestrator
(hardwarego) distinguishes "scanner found bugs" (exit 0, JSON with
verdict != safe) from "scanner itself broke" (non-zero exit, stderr text).

Usage:
    python scan_target.py path/to/target.cpp \\
        --pico-port /dev/cu.usbmodem1101 \\
        --n 1000 \\
        --out report.json

Hardwarego invokes it like:
    python -u scan_target.py <abs/path/to.cpp> --pico-port <p> --n 500 --out -

(`--out -` means "don't write a file, just print JSON to stdout".)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

# All runner-internal imports use the flat "from <pkg>" form -- runner/ is
# on sys.path because we're either invoked as `python scan_target.py ...`
# (so the script's dir is added) or `python -m scan_target ...` from cwd
# (which is also runner/). Matches eval.py's convention.
from analyze import cpa as cpa_mod
from analyze import ct_lint
from analyze import tvla as tvla_mod
from collect import pod as pod_mod
from collect import traces as traces_mod
from pipeline import findings as fmod
from pipeline.findings import Finding, TargetReport


# -----------------------------------------------------------------------------
# Repo paths
# -----------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))

# The slot we overwrite per target. Must match auto_flash.DEFAULT_SKETCH_DIR.
GB_TARGET_CPP = os.path.normpath(
    os.path.join(_HERE, "..", "esp", "harness", "gb_target.cpp"))
_GB_TARGET_BAK = GB_TARGET_CPP + ".scanbak"

# Default TVLA campaign size. Lower than collect/traces.py's default to
# keep per-target wall time reasonable when scanning a whole repo.
DEFAULT_N_PER_GROUP = 500


# =============================================================================
# Stage helpers
# =============================================================================

def _log(msg: str) -> None:
    """Stderr log -- keeps stdout reserved for the final JSON report."""
    sys.stderr.write(f"[scan] {msg}\n")
    sys.stderr.flush()


def install_target(source_cpp: str) -> None:
    """Copy `source_cpp` into esp/harness/gb_target.cpp, backing up first.
    Raises FileNotFoundError if the source is missing."""
    if not os.path.isfile(source_cpp):
        raise FileNotFoundError(f"target source not found: {source_cpp}")
    if os.path.exists(GB_TARGET_CPP):
        shutil.copy2(GB_TARGET_CPP, _GB_TARGET_BAK)
    shutil.copy2(source_cpp, GB_TARGET_CPP)
    _log(f"installed {source_cpp} -> {GB_TARGET_CPP}")


def restore_target() -> None:
    """Move the backup back. Idempotent -- safe to call multiple times."""
    if os.path.exists(_GB_TARGET_BAK):
        shutil.move(_GB_TARGET_BAK, GB_TARGET_CPP)
        _log("restored gb_target.cpp from backup")


def flash_and_verify(esp_port: Optional[str], pico_port: Optional[str],
                     bridge_seconds: int = 90) -> int:
    """Wraps auto_flash.flash_target. Returns the rc; 0 = success.
    Lazily-imported so a missing pyserial doesn't break ct_lint-only runs."""
    from auto_flash import flash_target           # noqa: F401  (lazy import)
    rc = flash_target(
        language="cpp",
        esp_port=esp_port,
        pico_port=pico_port,
        verify=True,
        via_pico=None,                            # auto-detect
        bridge_seconds=bridge_seconds,
    )
    if rc != 0:
        raise RuntimeError(f"auto_flash.flash_target returned rc={rc}")
    return rc


def _resolve_pico_port(explicit: Optional[str]) -> str:
    """Return an explicit Pico path or auto-detect via USB VID:PID.
    Raises RuntimeError with a helpful message if no Pico is visible."""
    if explicit:
        return explicit
    # Lazy import so ct_lint-only runs don't pull in pyserial.
    from auto_flash import detect_pico_port, list_ports
    found = detect_pico_port()
    if found:
        _log(f"auto-detected Pico at {found}")
        return found
    visible = "\n  ".join(str(p) for p in list_ports()) or "(none)"
    raise RuntimeError(
        "no Pico detected via USB. Visible ports:\n  " + visible +
        "\nPass --pico-port explicitly if your Pico has a non-standard VID:PID."
    )


def _campaign_suggestion_from_findings(
        findings: List[Finding]) -> tuple[Optional[str], Optional[bytes]]:
    """Look through ct_lint findings for an auto-campaign hint.

    Returns (campaign_name, reference_bytes) or (None, None) if no hint
    is available. We prefer the FIRST hint we see -- ct_lint already
    de-dupes and orders findings, and any extracted reference is
    file-global, so subsequent suggestions would be redundant.
    """
    for f in findings:
        if f.type != "static":
            continue
        camp = f.data.get("suggested_campaign")
        ref_hex = f.data.get("suggested_reference_hex")
        if camp and ref_hex:
            try:
                return camp, bytes.fromhex(ref_hex)
            except ValueError:
                continue
    return None, None


def _build_campaign(name: str, secret_len: int,
                    reference: Optional[bytes],
                    *,
                    findings: Optional[List[Finding]] = None,
                    ) -> tuple["traces_mod.Campaign", str]:
    """Construct the requested Campaign. Returns (campaign, resolved_name)
    where `resolved_name` is what `auto` actually picked.

    `auto` mode (the default) inspects `findings` for a ct_lint suggestion;
    it picks `match_vs_random` if one is available, otherwise falls back
    to `random_vs_zero`. If --reference was passed alongside --campaign auto,
    we honor it (operator override beats inference).

    Validates that match_vs_random has a reference; falls back to
    RandomVsZero on unknown names.
    """
    if name == "auto":
        if reference:
            # Operator passed --reference explicitly -- use match_vs_random
            # with the operator-supplied bytes regardless of what ct_lint
            # may have inferred.
            return (traces_mod.MatchVsRandom(
                        secret_len=secret_len, reference=reference),
                    "match_vs_random")
        sugg_camp, sugg_ref = _campaign_suggestion_from_findings(findings or [])
        if sugg_camp == "match_vs_random" and sugg_ref:
            return (traces_mod.MatchVsRandom(
                        secret_len=secret_len, reference=sugg_ref),
                    "match_vs_random")
        return (traces_mod.RandomVsZero(secret_len=secret_len),
                "random_vs_zero")

    if name == "match_vs_random":
        if not reference:
            raise ValueError(
                "campaign 'match_vs_random' requires --reference <hex>"
            )
        return (traces_mod.MatchVsRandom(
                    secret_len=secret_len, reference=reference),
                "match_vs_random")
    if name == "random_vs_random":
        return (traces_mod.RandomVsRandom(secret_len=secret_len),
                "random_vs_random")
    return (traces_mod.RandomVsZero(secret_len=secret_len),
            "random_vs_zero")


def collect_traces(pico_port: str, n_per_group: int,
                   *, campaign: "traces_mod.Campaign"):
    """Open the pod, run the campaign, return (DataFrame, FailureStats).
    The Pico is closed before we return so analyze stages don't hold the port."""
    pod = pod_mod.open_pod(pico_port)
    try:
        df, fails = traces_mod.collect_two_groups(
            pod, campaign,
            n_per_group=n_per_group,
            on_progress=_traces_progress,
        )
        sys.stderr.write("\n")                    # finish the progress line
        sys.stderr.flush()
        return df, fails
    finally:
        pod.close()


def _traces_progress(done: int, total: int, failed: int) -> None:
    pct = 100.0 * done / max(total, 1)
    sys.stderr.write(
        f"\r[scan] traces {done}/{total} ({pct:5.1f}%)  failed={failed}"
    )
    sys.stderr.flush()


# =============================================================================
# Analysis -- turns a DataFrame into Findings
# =============================================================================

# TVLA scalar channels we always check if present in the parquet. Matches
# the channel names in collect/traces.py's row schema.
_TVLA_SCALAR_CHANNELS = ("cycles", "micros", "insns", "branches")


def _split_groups(df) -> tuple:
    """Return (df_a, df_b, label_a, label_b) for the two campaign groups."""
    groups = sorted(df["group"].unique())
    if len(groups) != 2:
        raise RuntimeError(
            f"expected exactly 2 groups in trace df, got {groups}"
        )
    label_a, label_b = groups
    return df[df["group"] == label_a], df[df["group"] == label_b], label_a, label_b


def _stack_power(df) -> np.ndarray:
    """Convert the 'power' list-column into a (n, m) uint16 array."""
    return np.stack([np.asarray(p, dtype=np.uint16) for p in df["power"].values])


def _hex_to_pt_array(df, key_bytes: int) -> np.ndarray:
    """Build the (n, key_bytes) plaintext array CPA needs."""
    out = np.zeros((len(df), key_bytes), dtype=np.uint8)
    for i, hx in enumerate(df["input_hex"].values):
        b = bytes.fromhex(hx)[:key_bytes]
        out[i, :len(b)] = np.frombuffer(b, dtype=np.uint8)
    return out


def analyze_traces(df, *, true_key: bytes,
                   findings_far: List[Finding]) -> List[Finding]:
    """Run TVLA + CPA on the collected DataFrame. Appends to findings_far
    so id assignment stays sequential across stages. Returns the new ones."""
    new: List[Finding] = []

    df_a, df_b, label_a, label_b = _split_groups(df)
    if len(df_a) < 30 or len(df_b) < 30:
        _log(f"WARN: skipping TVLA/CPA -- need >=30 per group, got "
             f"({len(df_a)}/{len(df_b)})")
        return new

    # --- TVLA: scalar channels (cycles, micros, insns, branches) + power -----
    scalar_channels = {}
    for ch in _TVLA_SCALAR_CHANNELS:
        if ch in df_a.columns and ch in df_b.columns:
            scalar_channels[ch] = (
                df_a[ch].to_numpy(dtype=np.float64),
                df_b[ch].to_numpy(dtype=np.float64),
            )
    power_a = _stack_power(df_a)
    power_b = _stack_power(df_b)

    report = tvla_mod.tvla_multi(
        target_name="gb_target",
        scalar_channels=scalar_channels,
        power_a=power_a,
        power_b=power_b,
        threshold=tvla_mod.TVLA_THRESHOLD,
        second_order=True,
    )

    for ch_name, v in report.first_order.items():
        new.append(fmod.build_tvla_finding(
            fmod.next_id(findings_far + new),
            channel=ch_name, order=1,
            t_abs=float(v.max_abs_t),
            threshold=float(v.threshold),
            leak_detected=bool(v.leak_detected),
            is_flat=bool(v.is_flat),
            argmax_sample=(int(v.argmax) if v.argmax is not None else None),
        ))
    for ch_name, v in report.second_order.items():
        new.append(fmod.build_tvla_finding(
            fmod.next_id(findings_far + new),
            channel=ch_name, order=2,
            t_abs=float(v.max_abs_t),
            threshold=float(v.threshold),
            leak_detected=bool(v.leak_detected),
            is_flat=bool(v.is_flat),
            argmax_sample=(int(v.argmax) if v.argmax is not None else None),
        ))

    # --- CPA: best-effort key recovery on the random group -------------------
    # Only the random group has varied plaintexts (the zero group is degenerate
    # for HW correlation). We attack each of the first len(true_key) bytes.
    try:
        key_bytes = max(1, min(len(true_key), 16))
        plaintexts = _hex_to_pt_array(df_b, key_bytes)
        per_byte = []
        for i in range(key_bytes):
            res = cpa_mod.attack_byte(
                plaintexts, power_b.astype(np.float64),
                byte_index=i,
                true_key_byte=int(true_key[i]) if i < len(true_key) else None,
            )
            per_byte.append({
                "byte_index":  res.byte_index,
                "best_guess":  res.best_guess,
                "correlation": res.correlation,
                "true_rank":   res.true_rank,
                "top5":        res.top5,
            })
        full = all(b["true_rank"] == 1 for b in per_byte)
        new.append(fmod.build_cpa_finding(
            fmod.next_id(findings_far + new),
            per_byte=per_byte,
            full_key_recovered=full,
            n_traces=len(df_b),
        ))
    except Exception as e:                            # noqa: BLE001
        _log(f"CPA skipped: {type(e).__name__}: {e}")

    return new


# =============================================================================
# Verdict safety net
# =============================================================================

# Rule IDs whose presence means "the function looks like a byte-compare
# whose leak random_vs_zero cannot excite". Mirrors ct_lint's set; kept
# here so the orchestrator doesn't need to reach into ct_lint internals.
_COMPARATOR_RULE_IDS = ("CT001", "CT002")


def _maybe_inconclusive(findings: List[Finding],
                        used_campaign_name: str) -> Optional[Finding]:
    """If ct_lint flagged a comparator AND we ran random_vs_zero AND every
    TVLA channel returned 'pass', synthesize a MEDIUM CT_AUTO finding so
    the report cannot silently come out as 'TVLA clean'.

    See pipeline.findings.build_inconclusive_tvla_finding for why this
    exact combination is a known false-negative shape.

    Returns None when the run is fine as-is (different campaign, no
    comparator hit, or TVLA actually detected something).
    """
    if used_campaign_name != "random_vs_zero":
        return None
    comparator_hits = [
        f for f in findings
        if f.type == "static"
        and f.data.get("rule_id") in _COMPARATOR_RULE_IDS
    ]
    if not comparator_hits:
        return None
    tvla_hits = [f for f in findings if f.type == "tvla"]
    if not tvla_hits:
        # No TVLA at all (e.g. failed early); no claim to second-guess.
        return None
    if any(f.severity != "pass" for f in tvla_hits):
        # TVLA actually detected something -- the report is honest.
        return None
    sugg_camp = next(
        (f.data.get("suggested_campaign") for f in comparator_hits
         if f.data.get("suggested_campaign")),
        None,
    )
    sugg_ref = next(
        (f.data.get("suggested_reference_hex") for f in comparator_hits
         if f.data.get("suggested_reference_hex")),
        None,
    )
    return fmod.build_inconclusive_tvla_finding(
        fmod.next_id(findings),
        used_campaign=used_campaign_name,
        suggested_campaign=sugg_camp,
        suggested_reference_hex=sugg_ref,
        comparator_rule_ids=[f.data.get("rule_id") for f in comparator_hits],
    )


# =============================================================================
# Top-level orchestration
# =============================================================================

def scan(source_cpp: str,
         *,
         pico_port: Optional[str],
         esp_port: Optional[str],
         n_per_group: int,
         secret: bytes,
         campaign_name: str = "auto",
         reference: Optional[bytes] = None,
         skip_flash: bool = False,
         skip_collect: bool = False,
         bridge_seconds: int = 90,
         ) -> TargetReport:
    """Per-target pipeline. Always returns a TargetReport even on partial
    failure -- a stage that can't run produces a Finding rather than aborting."""
    started = time.time()
    stage_secs: dict[str, float] = {}
    findings: List[Finding] = []

    def stage(name: str):
        """Context-manager-ish helper: usage `with stage('flash'): ...`."""
        class _S:
            def __enter__(self_inner):
                self_inner._t0 = time.monotonic()
                _log(f"--- stage: {name} ---")
                return self_inner
            def __exit__(self_inner, *exc):
                stage_secs[name] = time.monotonic() - self_inner._t0
                _log(f"    stage {name} took {stage_secs[name]:.2f}s")
        return _S()

    n_traces_collected = 0

    try:
        with stage("install"):
            install_target(source_cpp)

        with stage("ct_lint"):
            findings += ct_lint.lint_file(source_cpp, id_offset=len(findings))

        if not skip_flash:
            with stage("flash"):
                flash_and_verify(esp_port, pico_port,
                                 bridge_seconds=bridge_seconds)

        if not skip_collect:
            resolved_pico = _resolve_pico_port(pico_port)
            campaign, resolved_campaign_name = _build_campaign(
                campaign_name, secret_len=16, reference=reference,
                findings=findings)
            if campaign_name == "auto":
                _log(f"campaign auto -> {resolved_campaign_name}  "
                     f"({campaign.group_a_label} vs {campaign.group_b_label})")
            else:
                _log(f"campaign: {campaign.name}  "
                     f"({campaign.group_a_label} vs {campaign.group_b_label})")
            with stage("collect"):
                df, fails = collect_traces(
                    resolved_pico, n_per_group, campaign=campaign)
                n_traces_collected = len(df)
                if fails.total:
                    # One CrashFinding per failure kind.
                    for kind, count in fails.by_kind.items():
                        examples = fails.examples.get(kind, [])
                        findings.append(fmod.build_crash_finding(
                            fmod.next_id(findings),
                            kind=("memory" if kind == "memory" else kind),
                            count=count,
                            total=2 * n_per_group,
                            panic_pc=(fails.panic_pcs[0] if fails.panic_pcs else None),
                            panic_reason=(fails.panic_reasons[0] if fails.panic_reasons else None),
                            hex_input=(examples[0] if examples else None),
                        ))

            with stage("analyze"):
                findings += analyze_traces(df, true_key=secret,
                                           findings_far=findings)

            # Safety net: refuse to silently emit "TVLA pass" on a target
            # whose ct_lint output literally said "early-exit byte-compare".
            # See _maybe_inconclusive's docstring for the full failure-mode
            # rationale.
            warn = _maybe_inconclusive(findings, resolved_campaign_name)
            if warn is not None:
                findings.append(warn)
                _log(f"WARN: appending CT_AUTO inconclusive finding "
                     f"(campaign={resolved_campaign_name!r} cannot detect "
                     f"the comparator leak ct_lint flagged)")

    except Exception as e:                            # noqa: BLE001
        _log(f"FATAL: {type(e).__name__}: {e}")
        _log(traceback.format_exc())
        findings.append(fmod.build_crash_finding(
            fmod.next_id(findings),
            kind="err_response",
            count=1, total=1,
            hex_input=None,
            panic_reason=f"scanner: {type(e).__name__}: {e}",
        ))
    finally:
        try:
            restore_target()
        except Exception as e:                        # noqa: BLE001
            _log(f"WARN: restore_target failed: {e}")

    return fmod.merge(
        findings,
        target=source_cpp,
        started=started,
        finished=time.time(),
        n_traces=n_traces_collected,
        stage_secs=stage_secs,
    )


# =============================================================================
# CLI
# =============================================================================

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Per-target GlassBox scanner. "
                    "Flashes a C/C++ target, runs TVLA + CPA, prints a JSON report."
    )
    p.add_argument("source", help="C/C++ target source file")
    p.add_argument("--pico-port", default=None,
                   help="Pico USB CDC port (auto-detected if omitted)")
    p.add_argument("--esp-port", default=None,
                   help="ESP32 USB port (rarely needed; default = via Pico)")
    p.add_argument("--n", type=int, default=DEFAULT_N_PER_GROUP,
                   dest="n_per_group",
                   help="traces per TVLA group (default 500)")
    p.add_argument("--campaign",
                   choices=["auto", "random_vs_zero",
                            "random_vs_random", "match_vs_random"],
                   default="auto",
                   help="TVLA input distribution. Default 'auto' inspects "
                        "ct_lint findings: it picks 'match_vs_random' when "
                        "a static reference constant is recoverable from the "
                        "source, otherwise falls back to 'random_vs_zero'. "
                        "Use 'match_vs_random' explicitly (with --reference) "
                        "to force byte-compare-style sweeps.")
    p.add_argument("--reference", default=None,
                   help="hex string -- the value the target compares against. "
                        "Required for --campaign match_vs_random; optional "
                        "operator override for --campaign auto.")
    p.add_argument("--secret", default=None,
                   help="hex-16 secret for CPA true_rank; default = "
                        "the harness's hardcoded 'hunter2!' padded to 16 bytes")
    p.add_argument("--skip-flash", action="store_true",
                   help="assume the right firmware is already on the chip")
    p.add_argument("--skip-collect", action="store_true",
                   help="run ct_lint + flash only; no traces / TVLA / CPA")
    p.add_argument("--bridge-seconds", type=int, default=90,
                   help="Pico bridge-mode timeout for the flash stage")
    p.add_argument("--out", default=None,
                   help="if set (and not '-'), also write the JSON report here. "
                        "'-' means 'no file' -- JSON only goes to stdout.")
    return p.parse_args()


def _default_secret() -> bytes:
    """Default CPA reference key: the harness's hardcoded 'hunter2!' padded
    to 16 bytes. Lets CPA's `true_rank` be sensible for the demo strcmp
    targets; for user targets the operator should pass --secret explicitly."""
    return pod_mod.SECRET + b"\x00" * (16 - len(pod_mod.SECRET))


def main() -> int:
    args = _parse_args()

    if args.secret:
        secret = bytes.fromhex(args.secret)
        if len(secret) != 16:
            print("--secret must be exactly 16 bytes (32 hex chars)",
                  file=sys.stderr)
            return 2
    else:
        secret = _default_secret()

    reference = bytes.fromhex(args.reference) if args.reference else None

    report = scan(
        args.source,
        pico_port=args.pico_port,
        esp_port=args.esp_port,
        n_per_group=args.n_per_group,
        secret=secret,
        campaign_name=args.campaign,
        reference=reference,
        skip_flash=args.skip_flash,
        skip_collect=args.skip_collect,
        bridge_seconds=args.bridge_seconds,
    )

    blob = json.dumps(report.to_dict(), indent=None)
    print(blob)                                       # the canonical output
    if args.out and args.out != "-":
        with open(args.out, "w") as f:
            f.write(blob)
    _log(f"verdict={report.verdict}  worst={report.worst_severity}  "
         f"findings={len(report.findings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
