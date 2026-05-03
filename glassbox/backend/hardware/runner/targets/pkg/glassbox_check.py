"""glassbox_check.py -- the unified `glassbox check <path>` entry point.

This is GlassBox's single command for "I have some source code, what does
the *hardware* think of it?" It is intentionally narrow: only files that
the ESP32 can actually execute go through this path.

Supported (hardware-runnable) file types:

    .c / .cpp / .h / .hpp / .cc / .ino   -> drop into esp/harness/gb_target.cpp
    .s / .S                              -> wrap in C++ shim, drop in
    .rs                                  -> generate Cargo + C++ shim scaffold
    .zig                                 -> generate build.zig + C++ shim

Anything else (.py, .js, .ts, .rb, ...) is INTENTIONALLY rejected.

Pipeline stages (each can be turned on individually, or all at once with --auto):

  Stage 1  install_target  drop the file into esp/harness/gb_target.cpp
                           (also handles asm/rust/zig FFI scaffolding)
  Stage 2  ct_lint         pre-flash constant-time linter (C/C++ only)
  Stage 3  flash           arduino-cli or platformio compile + upload to ESP32
  Stage 4  verify          open the Pico, confirm the new firmware is alive
  Stage 5  sweep+eval      collect traces and run TVLA + CPA + ML over them,
                           merging all findings into a single run_detail.json

Flags:

  (none)              --> stage 2 only (lint), write run_detail.json
  --install-target    --> stages 1 + 2
  --flash             --> stages 1 + 2 + 3 + 4 (--install-target implied)
  --sweep             --> stages 1 + 2 + 3 + 4 + 5 (everything)
  --auto              --> alias for --sweep (the default "do everything" mode)

Usage:

    python glassbox_check.py myfunc.cpp                  # lint only
    python glassbox_check.py myfunc.cpp --install-target # lint + drop in
    python glassbox_check.py myfunc.cpp --flash          # also flash + verify
    python glassbox_check.py myfunc.cpp --auto           # full pipeline
    python glassbox_check.py myfunc.py                   # rejected with a clear message
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shlex
import subprocess
import sys
from typing import Any, Dict, List, Optional

import findings as fmod
import compile_target


_HERE = os.path.dirname(os.path.abspath(__file__))


def _now_iso() -> str:
    """RFC-3339 UTC timestamp suitable for run_detail.created_at."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug_from_path(path: str) -> str:
    """Make a stable, filesystem-safe slug for run_id."""
    base = os.path.basename(os.path.abspath(path)).strip("/")
    base = base.replace(".", "_").replace(" ", "_") or "scan"
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"check_{base}_{ts}"


# Extensions the ESP32 hardware path can actually handle.
SUPPORTED_EXTS = set(compile_target.LANG_BY_EXT.keys())


def _is_supported(path: str) -> bool:
    return os.path.splitext(path)[1] in SUPPORTED_EXTS


def _run_ct_lint(path: str, id_offset: int = 0) -> List[fmod.Finding]:
    """Pre-flash constant-time linter on a C/C++ source file."""
    try:
        import ct_lint
    except Exception:
        return []
    return ct_lint.lint_file(path, id_offset=id_offset)


def install_to_harness(path: str, *, name: str = "my_target",
                       force: bool = False) -> int:
    """Drop a single hardware-runnable source file into the ESP32 harness slot."""
    lang = compile_target.detect_language(path)
    target = compile_target.DEFAULT_HARNESS_TARGET
    if lang in ("c", "cpp"):
        return compile_target.install_c_or_cpp(path, target, force=force)
    if lang == "asm":
        return compile_target.install_asm(path, target, name=name, force=force)
    if lang == "rust":
        return compile_target.install_rust(path, target, name=name, force=force)
    if lang == "zig":
        return compile_target.install_zig(path, target, name=name, force=force)
    print(f"glassbox check: cannot install {lang!r} into harness yet.")
    return 2


# -----------------------------------------------------------------------------
# Stage 5: sweep + eval (subprocess into eval.py for clean process isolation)
# -----------------------------------------------------------------------------

def run_sweep_and_eval(*, pico_port: str,
                       run_detail_out: str,
                       lint_path: Optional[str],
                       campaign: str = "random_vs_zero",
                       secret_len: int = 16,
                       n_per_group: int = 500,
                       run_cpa: bool = False,
                       cpa_true_key: Optional[str] = None,
                       extra_args: Optional[List[str]] = None) -> int:
    """Drive eval.py to collect a fresh campaign + TVLA + CPA + ML + write JSON.

    eval.py with --port runs collect+analyze in one shot, including reading
    crash / non-determinism / length-oracle findings out of sweep_target's
    module-level state. Subprocess (instead of import) means we get clean
    cleanup of the serial handle and a fresh process per file when scanning
    a whole repo.
    """
    cmd = [
        sys.executable, os.path.join(_HERE, "eval.py"),
        "--port", pico_port,
        "--campaign", campaign,
        "--secret-len", str(secret_len),
        "--n-per-group", str(n_per_group),
        "--run-detail", run_detail_out,
    ]
    if lint_path:
        cmd += ["--lint", lint_path]
    if run_cpa:
        cmd.append("--cpa")
    if cpa_true_key:
        cmd += ["--cpa-true-key", cpa_true_key]
    if extra_args:
        cmd += extra_args

    print(f"[glassbox check] sweep+eval: {' '.join(shlex.quote(c) for c in cmd)}")
    try:
        rc = subprocess.run(cmd, check=False).returncode
    except KeyboardInterrupt:                                   # pragma: no cover
        return 130
    return rc


# -----------------------------------------------------------------------------
# Reporting
# -----------------------------------------------------------------------------

def _print_human_summary(findings: List[fmod.Finding],
                         path: str,
                         emitted_run_detail: Optional[str]) -> None:
    print("=" * 72)
    print(f"GlassBox check: {path}")
    print(f"  Total findings:  {len(findings)}")
    summary = fmod.summarize(findings)
    print(f"  Worst severity:  {summary['worst_severity']}")
    if emitted_run_detail:
        print(f"  Wrote:           {emitted_run_detail}")
    print("-" * 72)

    bad = [f for f in findings if f.severity in ("CRITICAL", "HIGH", "MEDIUM")]
    if not bad:
        print("  No HIGH/CRITICAL/MEDIUM findings. Looks clean.")
        return

    bad.sort(key=lambda f: fmod.severity_rank(f.severity))
    for f in bad[:80]:
        loc = ""
        if f.source:
            loc = f"  [{f.source.file}:{f.source.line}"
            if f.source.col is not None:
                loc += f":{f.source.col}"
            loc += "]"
        print(f"  [{f.severity:>8}] {f.type:<18s} {f.title}{loc}")
    if len(bad) > 80:
        print(f"  ... and {len(bad) - 80} more (see run_detail.json)")


def emit_run_detail(out_path: str, *,
                    run_id: str,
                    target: str,
                    findings: List[fmod.Finding]) -> None:
    """Write a v2 run_detail.json with the (lint-only) scan's results.

    NOTE: When --flash --sweep is used, eval.py overwrites this file with
    the full TVLA + CPA + lint payload. This function is for the lint-only
    path where we never reach the hardware.
    """
    summary = fmod.summarize(findings)
    payload: Dict[str, Any] = {
        "schema_version": 2,
        "run_id":         run_id,
        "created_at":     _now_iso(),
        "source": {
            "kind":     "hardware_prep_scan",
            "path":     os.path.abspath(target),
            "language": compile_target.detect_language(target),
        },
        "verdict":           fmod.derive_verdict(findings),
        "findings":          fmod.to_json_list(findings),
        "findings_summary":  summary,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)


# =============================================================================
# CLI
# =============================================================================

def main():
    ap = argparse.ArgumentParser(
        prog="glassbox check",
        description=(
            "Pre-flash + flash + sweep + eval pipeline for hardware-runnable "
            "source files. Stages can be turned on individually with "
            "--install-target / --flash / --sweep, or all at once with --auto. "
            "Files that don't run on the ESP32 (.py, .js, ...) are rejected."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("path",
                    help="Hardware-runnable source: "
                         + ", ".join(sorted(SUPPORTED_EXTS)))
    ap.add_argument("-o", "--out", default=None,
                    help="Where to write run_detail.json "
                         "(default: ./check_<slug>.json).")
    ap.add_argument("--json", action="store_true",
                    help="Print findings as JSON to stdout instead of a "
                         "human summary.")

    # Stage selectors.
    ap.add_argument("--install-target", action="store_true",
                    help="Drop the source into esp/harness/gb_target.cpp.")
    ap.add_argument("--flash", action="store_true",
                    help="Also compile + upload the harness to the ESP32 "
                         "(implies --install-target).")
    ap.add_argument("--sweep", action="store_true",
                    help="Also collect traces and run TVLA + CPA + ML through "
                         "eval.py (implies --flash, requires the Pico to be "
                         "connected).")
    ap.add_argument("--auto", action="store_true",
                    help="Shortcut: --install-target + --flash + --sweep "
                         "(end-to-end pipeline).")

    # Install knobs.
    ap.add_argument("--name", default="my_target",
                    help="Symbol name for Rust/Zig/asm targets (used in the "
                         "generated shim and as gb_target_name()'s return value).")
    ap.add_argument("-f", "--force", action="store_true",
                    help="Overwrite existing harness target file.")

    # Flash knobs.
    ap.add_argument("--esp-port", default=None,
                    help="ESP32 serial port for flashing (auto-detected by "
                         "USB VID:PID if omitted).")
    ap.add_argument("--pico-port", default=None,
                    help="Pico serial port for sweep + verification "
                         "(auto-detected if omitted).")
    ap.add_argument("--fqbn", default=None,
                    help="Override arduino-cli FQBN (default: esp32:esp32:esp32).")
    ap.add_argument("--toolchain", default=None,
                    choices=["arduino-cli", "platformio"],
                    help="Force a specific flashing toolchain.")
    ap.add_argument("--no-verify", action="store_true",
                    help="Skip the post-flash 'is harness alive?' check.")
    ap.add_argument("--via-pico", dest="via_pico", action="store_true",
                    default=None,
                    help="Force bridged flashing through the Pico (Route A: "
                         "only the Pico USB cable is plugged in). Default: "
                         "auto-detect.")
    ap.add_argument("--no-via-pico", dest="via_pico", action="store_false",
                    help="Refuse to bridge -- require a directly-attached "
                         "ESP32 USB port.")

    # Sweep knobs (forwarded to eval.py).
    ap.add_argument("--campaign", default="random_vs_zero",
                    help="Input distribution for the sweep (default: %(default)s).")
    ap.add_argument("--secret-len", type=int, default=16,
                    help="Secret length in bytes for the sweep (default: %(default)s).")
    ap.add_argument("--n-per-group", type=int, default=500,
                    help="Traces per TVLA group (default: %(default)s).")
    ap.add_argument("--cpa", action="store_true",
                    help="Run AES-S-box CPA on the collected power traces.")
    ap.add_argument("--cpa-true-key", default=None,
                    help="Hex 16-byte key for CPA per-byte rank reporting.")

    args = ap.parse_args()

    # --- input validation ---------------------------------------------------
    if not os.path.exists(args.path):
        print(f"glassbox check: {args.path}: no such file", file=sys.stderr)
        sys.exit(2)
    if not os.path.isfile(args.path):
        print(f"glassbox check: {args.path}: not a regular file. "
              f"This command only operates on a single source file at a time.",
              file=sys.stderr)
        sys.exit(2)
    if not _is_supported(args.path):
        ext = os.path.splitext(args.path)[1] or "<no extension>"
        print(f"glassbox check: {args.path!r} ({ext}) is not a hardware-runnable "
              f"source file. This command only handles files the ESP32 can "
              f"actually execute:", file=sys.stderr)
        for e in sorted(SUPPORTED_EXTS):
            print(f"  {e}", file=sys.stderr)
        print("\nFor host-side analysis of other languages, use the dedicated "
              "host-side tool (separate from GlassBox's hardware pipeline).",
              file=sys.stderr)
        sys.exit(2)

    # --auto / --sweep / --flash imply earlier stages.
    if args.auto:
        args.sweep = True
    if args.sweep:
        args.flash = True
    if args.flash:
        args.install_target = True

    # --- stage 0: ABI + lint ------------------------------------------------
    lang = compile_target.detect_language(args.path)
    abi_problems: List[str] = []
    if lang in ("c", "cpp"):
        with open(args.path, "r", encoding="utf-8", errors="replace") as fh:
            abi_problems = compile_target.check_c_abi(fh.read())

    findings: List[fmod.Finding] = []
    if lang in ("c", "cpp"):
        findings.extend(_run_ct_lint(args.path, id_offset=len(findings)))

    out_path = args.out or f"check_{_slug_from_path(args.path)}.json"
    run_id = _slug_from_path(args.path)
    emit_run_detail(out_path, run_id=run_id, target=args.path, findings=findings)

    # --- stage 1: install --------------------------------------------------
    if args.install_target:
        if abi_problems:
            print("glassbox check: refusing to install -- ABI check FAILED:")
            for p in abi_problems:
                print(f"  - {p}")
            sys.exit(1)
        print()
        print("--- stage 1: install into ESP32 harness slot ---")
        rc = install_to_harness(args.path, name=args.name, force=args.force)
        if rc != 0:
            sys.exit(rc)

    # --- stage 3 + 4: flash + verify ---------------------------------------
    if args.flash:
        try:
            import auto_flash
        except Exception as e:
            print(f"glassbox check: cannot import auto_flash: {e}", file=sys.stderr)
            sys.exit(1)
        print()
        print("--- stage 3: compile + upload to ESP32 ---")
        flash_kwargs: Dict[str, Any] = {
            "language":          lang,
            "esp_port":          args.esp_port,
            "pico_port":         args.pico_port,
            "toolchain_override": args.toolchain,
            "verify":            (not args.no_verify),
            "via_pico":          args.via_pico,
        }
        if args.fqbn:
            flash_kwargs["fqbn"] = args.fqbn
        rc = auto_flash.flash_target(**flash_kwargs)
        if rc != 0:
            print(f"glassbox check: flash failed (rc={rc})", file=sys.stderr)
            sys.exit(rc)

    # --- stage 5: sweep + eval ---------------------------------------------
    if args.sweep:
        # Pico port detection (eval.py needs an explicit one).
        pico_port = args.pico_port
        if pico_port is None:
            try:
                import auto_flash
                pico_port = auto_flash.detect_pico_port()
            except Exception:
                pass
        if pico_port is None:
            print("glassbox check: cannot find the Pico port for sweep. "
                  "Pass --pico-port /dev/<...>.", file=sys.stderr)
            sys.exit(1)
        print()
        print("--- stage 5: sweep + eval ---")
        rc = run_sweep_and_eval(
            pico_port=pico_port,
            run_detail_out=out_path,
            lint_path=args.path,
            campaign=args.campaign,
            secret_len=args.secret_len,
            n_per_group=args.n_per_group,
            run_cpa=args.cpa,
            cpa_true_key=args.cpa_true_key,
        )
        if rc not in (0, 1):
            # eval.py exits 1 when leakage is found (still valid output);
            # any other non-zero is a real failure.
            print(f"glassbox check: sweep+eval failed (rc={rc})", file=sys.stderr)
            sys.exit(rc)

    # --- final report -------------------------------------------------------
    if args.sweep and os.path.isfile(out_path):
        # eval.py wrote the authoritative run_detail.json; reload it for
        # the headline.
        try:
            doc = json.load(open(out_path))
            print()
            print("=" * 72)
            print(f"GlassBox check (full pipeline): {args.path}")
            print(f"  Wrote:           {out_path}")
            print(f"  Verdict:         {doc.get('verdict', '?')}")
            fs = doc.get("findings_summary", {})
            print(f"  Worst severity:  {fs.get('worst_severity', '?')}")
            print(f"  Findings total:  {fs.get('total', '?')}")
            print(f"  By severity:     {fs.get('by_severity', {})}")
        except Exception as e:
            print(f"glassbox check: could not parse final run_detail.json: {e}",
                  file=sys.stderr)
        # eval.py's exit code already reflects leak/no-leak; mirror it via
        # findings_summary instead.
        sev = (doc.get("findings_summary") or {}).get("worst_severity", "pass")
        sys.exit(1 if sev in ("CRITICAL", "HIGH") else 0)

    # Pre-hardware-only path: print the lint summary.
    if args.json:
        print(json.dumps([f.to_dict() for f in findings], indent=2))
    else:
        _print_human_summary(findings, args.path, emitted_run_detail=out_path)
        if abi_problems and not args.install_target:
            print()
            print("Note: ABI check would FAIL (use --install-target to see details):")
            for p in abi_problems:
                print(f"  - {p}")
    has_blocking = any(f.severity in ("CRITICAL", "HIGH") for f in findings)
    sys.exit(1 if has_blocking else 0)


if __name__ == "__main__":
    main()
