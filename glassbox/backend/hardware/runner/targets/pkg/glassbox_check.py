"""glassbox_check.py -- the unified `glassbox check <path>` entry point.

This is GlassBox's single command for "I have some source code, what does
the *hardware* think of it?" It is intentionally narrow: only files that
the ESP32 can actually execute go through this path.

Supported (hardware-runnable) file types:

    .c / .cpp / .h / .hpp / .cc / .ino   -> drop into esp/harness/gb_target.cpp
    .s / .S                              -> wrap in C++ shim, drop in
    .rs                                  -> generate Cargo + C++ shim scaffold
    .zig                                 -> generate build.zig + C++ shim

Anything else (.py, .js, .ts, .rb, ...) is INTENTIONALLY rejected: those
files do not run on the ESP32 and a host-side static analyzer for them
lives in a separate tool, not here. That keeps this command honest about
what it does -- "if it ran on the hardware, here is what GlassBox saw."

What this command does, given a hardware-runnable source file:

  1. Validate the source against the harness ABI (gb_target_call /
     gb_target_name with C linkage).
  2. For C/C++: run the pre-flash constant-time linter (`ct_lint.py`)
     so leaky patterns are flagged BEFORE you waste a flash cycle.
  3. With --install-target: copy / scaffold the source into
     esp/harness/gb_target.cpp so the next Arduino-IDE flash picks it up.
  4. Emit a v2 run_detail.json with whatever findings already fired
     (currently just `static` from the linter; hardware-side findings
     come from the existing sweep / eval pipeline once the user flashes
     and sweeps).
  5. Exit non-zero if any HIGH or CRITICAL finding fired, so this drops
     into a CI step.

Usage:

    python glassbox_check.py path/to/myfunc.cpp                # lint only
    python glassbox_check.py path/to/myfunc.cpp --install-target  # also drop into harness
    python glassbox_check.py path/to/myfunc.rs --install-target --name my_target
    python glassbox_check.py myfunc.py                         # rejected with a clear message
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import Any, Dict, List, Optional

import findings as fmod
import compile_target


def _now_iso() -> str:
    """RFC-3339 UTC timestamp suitable for run_detail.created_at."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug_from_path(path: str) -> str:
    """Make a stable, filesystem-safe slug for run_id."""
    base = os.path.basename(os.path.abspath(path)).strip("/")
    base = base.replace(".", "_").replace(" ", "_") or "scan"
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"check_{base}_{ts}"


# Extensions the ESP32 hardware path can actually handle (C/C++ natively, the
# rest via compile_target.py's FFI scaffolding for PlatformIO builds).
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
    """Drop a single hardware-runnable source file into the ESP32 harness slot.

    Returns 0 on success, non-zero if the file fails the ABI check.
    Does not flash the firmware -- that's still a manual Arduino-IDE step.
    """
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
    """Write a v2 run_detail.json with the hardware-prep scan's results."""
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
            "Pre-flash check for hardware-runnable source files. "
            "Validates the gb_target_call ABI, runs the constant-time linter, "
            "and (with --install-target) drops the file into the ESP32 harness "
            "slot so the next flash picks it up. "
            "Files that don't run on the hardware (.py, .js, ...) are rejected."
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
    ap.add_argument("--install-target", action="store_true",
                    help="Also drop the source into esp/harness/gb_target.cpp "
                         "(C/C++ direct, asm/rust/zig via FFI shim) so the "
                         "next flash picks it up.")
    ap.add_argument("--name", default="my_target",
                    help="Symbol name for Rust/Zig/asm targets (used in the "
                         "generated shim and as gb_target_name()'s return value).")
    ap.add_argument("-f", "--force", action="store_true",
                    help="Overwrite existing harness target file.")
    args = ap.parse_args()

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

    # ABI check up front -- never silently install a broken target.
    lang = compile_target.detect_language(args.path)
    abi_problems: List[str] = []
    if lang in ("c", "cpp"):
        with open(args.path, "r", encoding="utf-8", errors="replace") as fh:
            abi_problems = compile_target.check_c_abi(fh.read())

    # Pre-flash linter for C/C++ sources.
    findings: List[fmod.Finding] = []
    if lang in ("c", "cpp"):
        findings.extend(_run_ct_lint(args.path, id_offset=len(findings)))

    out_path = args.out or f"check_{_slug_from_path(args.path)}.json"
    run_id = _slug_from_path(args.path)
    emit_run_detail(out_path, run_id=run_id, target=args.path, findings=findings)

    if args.install_target:
        if abi_problems:
            print("glassbox check: refusing to install -- ABI check FAILED:")
            for p in abi_problems:
                print(f"  - {p}")
            print("Fix the source so it exposes both required symbols with C "
                  "linkage, then re-run.")
            sys.exit(1)
        print()
        print("--- installing into ESP32 harness slot ---")
        rc = install_to_harness(args.path, name=args.name, force=args.force)
        if rc != 0:
            sys.exit(rc)

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
