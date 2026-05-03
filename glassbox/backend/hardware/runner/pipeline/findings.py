"""findings.py -- polymorphic finding records for the run_detail schema.

Every detector in the GlassBox runner (TVLA, crash classifier, length-oracle
aggregator, memory-corruption parser, constant-time linter, CPA key recovery)
emits Finding objects rather than its own bespoke JSON shape. Findings flow
into a single list per scanned target so:

  * scan_target.py can merge & rank them with one rollup function
  * hardwarego/hardware.go can stuff them straight into audit results
  * the frontend renders them uniformly (mocks/types.ts -> Finding)
  * the GitHub PR comment summarizes them in one block

Design rules:

  * Each Finding has a stable envelope (id, type, severity, title, detail).
  * The polymorphic `data` blob's shape is fixed by `type` -- adding a new
    finding type means adding a builder here AND a TS interface in types.ts.
  * Severities are coarse (CRITICAL > HIGH > MEDIUM > LOW > INFO > pass) and
    map cleanly onto GitHub PR comment formatting + the dashboard color bar.
  * `to_dict()` produces JSON ready for run_detail.json. No numpy types leak
    out -- every numeric is a plain float / int.

Consumers:
  - scan_target.py: orchestrates everything; turns analyzer outputs into Findings.
  - analyze/ct_lint.py: emits StaticFinding per linter rule trip.
  - collect/traces.py: emits CrashFinding when traces fail in bulk.

Adapted from cs370/runner/findings.py.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional


# Severity ladder. Lower index = worse.
SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO", "pass"]

# Each finding type maps to a default Verdict value when it is the WORST
# finding in a run (drives RunSummary.verdict). The leaderboard / GitHub
# comment use this to pick the headline message.
TYPE_TO_VERDICT = {
    "tvla":              "leak_detected",
    "crash":             "crash_detected",
    "non_determinism":   "non_determinism_detected",
    "length_oracle":     "leak_detected",
    "memory_corruption": "memory_corruption_detected",
    "static":            "static_warning",
    "cpa_key_recovery":  "key_recovered",
}


# =============================================================================
# Severity helpers
# =============================================================================

def severity_rank(sev: str) -> int:
    """Lower = worse. Unknown severities sort last."""
    try:
        return SEVERITY_ORDER.index(sev)
    except ValueError:
        return len(SEVERITY_ORDER)


def worst_severity(severities: List[str]) -> str:
    if not severities:
        return "pass"
    return min(severities, key=severity_rank)


# =============================================================================
# Source-location helper (used by static / ct_lint findings)
# =============================================================================

@dataclass
class SourceLoc:
    file: str
    line: int
    col: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"file": self.file, "line": int(self.line)}
        if self.col is not None:
            d["col"] = int(self.col)
        return d


# =============================================================================
# Finding -- the envelope every detector returns
# =============================================================================

@dataclass
class Finding:
    """Common envelope for every finding type. Use a builder function below
    rather than constructing these directly -- the builders enforce schema
    consistency with the frontend's TS Finding type."""
    id: str
    type: Literal[
        "tvla", "crash", "non_determinism", "length_oracle",
        "memory_corruption", "static", "cpa_key_recovery",
    ]
    severity: str                                  # CRITICAL..pass
    title: str
    detail: str
    data: Dict[str, Any]
    remediation: Optional[str] = None
    source: Optional[SourceLoc] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id":       self.id,
            "type":     self.type,
            "severity": self.severity,
            "title":    self.title,
            "detail":   self.detail,
            "data":     self.data,
        }
        if self.remediation is not None:
            d["remediation"] = self.remediation
        if self.source is not None:
            d["source"] = self.source.to_dict()
        return d


# =============================================================================
# Builders -- detectors call these instead of constructing Findings directly
# =============================================================================

def next_id(existing: List[Finding]) -> str:
    """Assign a stable per-run finding id like 'f_001', 'f_002', ..."""
    return f"f_{len(existing) + 1:03d}"


def build_tvla_finding(
    fid: str,
    *,
    channel: str,
    order: int,
    t_abs: Optional[float],
    threshold: float,
    leak_detected: bool,
    is_flat: bool,
    argmax_sample: Optional[int] = None,
    fraction_through_function: Optional[float] = None,
    severity_override: Optional[str] = None,
    remediation: Optional[str] = None,
) -> Finding:
    """One TVLA channel/order pair as a Finding."""
    if is_flat:
        sev = "pass"
        title = f"TVLA {channel} (order {order}): no measurable signal"
        detail = (f"The {channel} channel had ~zero variance across both "
                  f"groups -- this channel did not measure anything we can "
                  f"perform a t-test on. This is normal on the LX6 PMU "
                  f"channels (insns/branches), which read 0 on this silicon.")
    elif leak_detected:
        # Map |t| to coarse severity: CRITICAL > 100 > HIGH > 20 > MEDIUM > thr.
        t = float(t_abs) if t_abs is not None else float("inf")
        sev = (severity_override
               or ("CRITICAL" if t > 100 else "HIGH" if t > 20 else "MEDIUM"))
        title = (f"Side-channel leak on {channel} "
                 f"(order {order}, |t|={t:.1f} > {threshold:.1f})")
        if order == 1:
            detail = (f"Welch's t-test rejects H0 (input-independent execution) "
                      f"on the {channel} channel with |t|={t:.1f}. The function's "
                      f"mean {channel} measurably depends on the secret input.")
        else:
            detail = (f"Second-order Welch's t-test rejects H0 on the {channel} "
                      f"channel: the *variance* of {channel} depends on the "
                      f"secret. This is the signature of a partially-masked "
                      f"implementation.")
    else:
        sev = "pass"
        title = f"TVLA {channel} (order {order}): OK"
        detail = (f"|t|={float(t_abs or 0.0):.2f} <= threshold {threshold:.1f}; "
                  f"no leak detected on this channel.")

    data: Dict[str, Any] = {
        "channel":   channel,
        "order":     int(order),
        "t_abs":     None if t_abs is None else float(t_abs),
        "threshold": float(threshold),
    }
    if argmax_sample is not None:
        data["argmax_sample"] = int(argmax_sample)
    if (fraction_through_function is not None
            and fraction_through_function == fraction_through_function):
        # second clause filters NaN
        data["fraction_through_function"] = float(fraction_through_function)
    return Finding(id=fid, type="tvla", severity=sev,
                   title=title, detail=detail, data=data,
                   remediation=remediation)


def build_crash_finding(
    fid: str,
    *,
    kind: str,                    # "timeout" | "panic" | "err_response" | "wdt_reset" | "memory" | "framing"
    count: int,
    total: int,
    panic_pc: Optional[str] = None,
    panic_reason: Optional[str] = None,
    hex_input: Optional[str] = None,
) -> Finding:
    """A crash / hang / panic during sweeping the target.

    Severity scales with frequency: a single rare crash is MEDIUM,
    >=1% or >=2 of N is HIGH, >=5% or >=5 is CRITICAL."""
    if count == 0:
        sev = "pass"
    elif count >= max(5, total // 20):
        sev = "CRITICAL"
    elif count >= max(2, total // 100):
        sev = "HIGH"
    else:
        sev = "MEDIUM"
    title = f"{kind.replace('_', ' ').title()} during sweep ({count}/{total} traces)"
    detail = _crash_detail(kind, count, total, panic_pc, panic_reason, hex_input)
    rem = _crash_remediation(kind)
    data: Dict[str, Any] = {"kind": kind, "count": int(count), "total": int(total)}
    if panic_pc is not None:     data["panic_pc"]     = str(panic_pc)
    if panic_reason is not None: data["panic_reason"] = str(panic_reason)
    if hex_input is not None:    data["hex_input"]    = str(hex_input[:128])
    return Finding(id=fid, type="crash", severity=sev,
                   title=title, detail=detail, data=data, remediation=rem)


def _crash_detail(kind, count, total, pc, reason, hex_input) -> str:
    base = f"{count} of {total} traces terminated abnormally ({kind})."
    extras = []
    if pc is not None:
        extras.append(f"PC=0x{pc.lstrip('0x')}")
    if reason is not None:
        extras.append(f"reason={reason!r}")
    if hex_input is not None:
        extras.append(f"trigger input ~{hex_input[:32]}")
    if extras:
        base += "  " + ", ".join(extras)
    return base


def _crash_remediation(kind: str) -> str:
    if kind == "timeout":
        return ("Long execution suggests an infinite loop, blocking call, or "
                "secret-controlled iteration count. Audit loop bounds and any "
                "calls into Serial / Wi-Fi / FreeRTOS yields.")
    if kind == "panic":
        return ("A panic during a sweep means a crafted input crashes the "
                "function. Treat as a memory-safety bug -- audit pointer "
                "arithmetic and buffer accesses near the input range.")
    if kind == "wdt_reset":
        return ("Task watchdog tripped. Function ran for too long without "
                "yielding; likely an unbounded loop on a malformed input.")
    if kind == "memory":
        return ("Memory-safety guard fired during sweep -- the function "
                "wrote past a buffer end. Treat as a CVE-class bug.")
    return ("Function returned an error response on certain inputs. If the "
            "error path leaks state (different errors for valid-MAC-bad-padding "
            "vs bad-MAC), that is itself an oracle -- audit the error branches.")


def build_memory_finding(
    fid: str,
    *,
    kind: str,                    # input_shadow_overflow | output_shadow_overflow | stack_canary | heap_poison | ubsan
    hex_input: Optional[str] = None,
    overrun_bytes: Optional[int] = None,
    raw: Optional[str] = None,
) -> Finding:
    sev = "CRITICAL"     # any memory-safety trip is critical
    title_map = {
        "input_shadow_overflow":  "Input buffer overflow (shadow sentinel tripped)",
        "output_shadow_overflow": "Output buffer overflow (shadow sentinel tripped)",
        "stack_canary":           "Stack smash (canary corrupted)",
        "heap_poison":            "Heap corruption (heap poisoning fired)",
        "ubsan":                  "Undefined behavior detected (UBSan)",
    }
    title = title_map.get(kind, f"Memory-safety violation ({kind})")
    detail = (
        f"The harness's memory-safety guards detected a {kind} during a call "
        f"to the function. The user code wrote past a buffer boundary, "
        f"corrupted the stack, or triggered undefined behavior on an "
        f"adversarial input."
    )
    if overrun_bytes is not None:
        detail += f"  Detected overrun: {overrun_bytes} byte(s) past the buffer end."
    rem = ("Treat this as a CVE-class bug. Audit pointer arithmetic and "
           "length checks near the failure site; consider rebuilding with "
           "AddressSanitizer for full backtraces (works on host-side tests).")
    data: Dict[str, Any] = {"kind": kind}
    if hex_input is not None:    data["hex_input"]     = str(hex_input[:128])
    if overrun_bytes is not None: data["overrun_bytes"] = int(overrun_bytes)
    if raw is not None:           data["raw"]           = str(raw)[:512]
    return Finding(id=fid, type="memory_corruption", severity=sev,
                   title=title, detail=detail, data=data, remediation=rem)


def build_static_finding(
    fid: str,
    *,
    rule_id: str,
    severity: str,
    file: str,
    line: int,
    col: Optional[int],
    message: str,
    excerpt: str,
    remediation: Optional[str] = None,
    suggested_campaign: Optional[str] = None,
    suggested_reference_hex: Optional[str] = None,
) -> Finding:
    """One ct_lint rule trip -> Finding.

    `suggested_campaign` / `suggested_reference_hex` are populated by
    ct_lint when the rule's leak shape implies a particular collect-stage
    campaign (e.g. CT001/CT002 -> match_vs_random against the file's
    static reference constant). scan_target.py reads them in
    --campaign auto mode to pick the right campaign automatically.
    """
    data: Dict[str, Any] = {"rule_id": rule_id, "excerpt": excerpt}
    if suggested_campaign:
        data["suggested_campaign"] = suggested_campaign
    if suggested_reference_hex:
        data["suggested_reference_hex"] = suggested_reference_hex
    return Finding(
        id=fid, type="static", severity=severity,
        title=f"[{rule_id}] {message}",
        detail=(
            f"Static analysis on `{file}:{line}` flagged a likely "
            f"side-channel pattern before flashing. Rule {rule_id}.\n\n"
            f"```c\n{excerpt}\n```"
        ),
        data=data,
        source=SourceLoc(file=file, line=line, col=col),
        remediation=remediation,
    )


def build_inconclusive_tvla_finding(
    fid: str,
    *,
    used_campaign: str,
    suggested_campaign: Optional[str],
    suggested_reference_hex: Optional[str],
    comparator_rule_ids: List[str],
) -> Finding:
    """Synthesized after collect+analyze when a comparator-shaped target
    (CT001/CT002 fired) was swept with `random_vs_zero` and TVLA returned
    pass on every channel. That outcome is structurally suspicious -- both
    A and B inputs early-exit at byte 0 of the comparator with very high
    probability, so the cycles distributions are degenerate even when the
    function is grossly leaky. We surface that as a MEDIUM finding so the
    report cannot silently report 'pass' on code ct_lint just flagged.

    Carries the orchestrator's auto-extracted reference (if any) so the
    operator can rerun directly without re-greppng their own source.
    """
    sev = "MEDIUM"
    rules = ", ".join(comparator_rule_ids) if comparator_rule_ids else "CT001/CT002"
    title = (f"TVLA inconclusive: {used_campaign!r} can't excite the leak "
             f"flagged by {rules}")
    parts = [
        f"ct_lint reported a comparator-shaped leak ({rules}) on this target, "
        f"but the collect stage swept with the {used_campaign!r} campaign and "
        f"every TVLA channel returned 'pass'.",
        "",
        "That combination is a known false-negative shape: with all-zero vs "
        "uniform-random inputs, both groups almost always early-exit on byte "
        "0 of the comparator, so cycles/power distributions look identical "
        "even when the function is grossly variable-time. The correct "
        "campaign for byte-compare-shaped functions is `match_vs_random`, "
        "which gives group A the actual reference value so it runs the "
        "comparator to completion.",
    ]
    if suggested_campaign and suggested_reference_hex:
        parts += [
            "",
            f"Recommended rerun (reference auto-extracted from source):",
            f"    --campaign {suggested_campaign} "
            f"--reference {suggested_reference_hex}",
        ]
        rem = (f"Re-run with `--campaign {suggested_campaign} "
               f"--reference {suggested_reference_hex}` to actually exercise "
               f"the leak path.")
    else:
        parts += [
            "",
            "No static reference constant was recoverable from the source, "
            "so the auto-campaign picker fell back to `random_vs_zero`. "
            "Pass `--campaign match_vs_random --reference <hex>` explicitly "
            "with the value the function compares against.",
        ]
        rem = ("Pass `--campaign match_vs_random --reference <hex>` with the "
               "byte sequence the function compares against, then re-run.")
    detail = "\n".join(parts)
    data: Dict[str, Any] = {
        "rule_id":         "CT_AUTO",
        "used_campaign":   used_campaign,
        "comparator_rule_ids": list(comparator_rule_ids),
    }
    if suggested_campaign:
        data["suggested_campaign"] = suggested_campaign
    if suggested_reference_hex:
        data["suggested_reference_hex"] = suggested_reference_hex
    return Finding(
        id=fid, type="static", severity=sev,
        title=f"[CT_AUTO] {title}",
        detail=detail,
        data=data,
        remediation=rem,
    )


def build_cpa_finding(
    fid: str,
    *,
    per_byte: List[Dict[str, Any]],
    full_key_recovered: bool,
    n_traces: int,
) -> Finding:
    """CPA key-recovery report. CRITICAL on full recovery, HIGH on partial,
    INFO on 'attempted, nothing found' (typical for non-AES targets)."""
    n_recovered = sum(1 for b in per_byte if b.get("true_rank") == 1)
    if full_key_recovered:
        sev = "CRITICAL"
        title = (f"CPA recovered the full key from power traces "
                 f"({len(per_byte)} bytes, n={n_traces})")
        detail = ("Correlation power analysis recovered every secret byte "
                  "with rank 1 from the existing power traces. The function "
                  "is broken under chosen-plaintext + power-trace access.")
        rem = ("Use a constant-power AES implementation: bitsliced AES, "
               "a hardware AES accelerator, or first-order Boolean masking "
               "with mask refresh.")
    elif n_recovered > 0:
        sev = "HIGH"
        title = (f"CPA partially recovered the key "
                 f"({n_recovered}/{len(per_byte)} bytes ranked 1, n={n_traces})")
        detail = ("Correlation power analysis recovered SOME but not all "
                  "secret bytes from the power traces. With more traces or "
                  "a refined leak model, a full recovery is plausible.")
        rem = ("Same as full recovery: deploy a masked / bitsliced / "
               "hardware-accelerated AES.")
    else:
        sev = "INFO"
        title = f"CPA attempted, no bytes recovered (n={n_traces})"
        detail = ("Correlation power analysis was attempted with the "
                  "Hamming-weight model on the AES S-box output. No byte's "
                  "true value reached rank 1. Either the function is not "
                  "AES-shaped, the leak model doesn't fit the silicon, or "
                  "more traces are needed.")
        rem = None
    return Finding(
        id=fid, type="cpa_key_recovery", severity=sev,
        title=title, detail=detail,
        data={"per_byte": per_byte,
              "full_key_recovered": bool(full_key_recovered),
              "n_traces": int(n_traces)},
        remediation=rem,
    )


# =============================================================================
# Aggregation
# =============================================================================

def summarize(findings: List[Finding]) -> Dict[str, Any]:
    """FindingsSummary block -- counts by severity + type, plus the worst sev."""
    by_sev = Counter(f.severity for f in findings)
    by_type = Counter(f.type for f in findings)
    sevs = [f.severity for f in findings if f.severity != "pass"]
    worst = worst_severity(sevs) if sevs else "pass"
    full_sev = {s: int(by_sev.get(s, 0)) for s in SEVERITY_ORDER}
    full_type = {t: int(by_type.get(t, 0)) for t in TYPE_TO_VERDICT.keys()}
    return {
        "total":          int(len(findings)),
        "by_severity":    full_sev,
        "by_type":        full_type,
        "worst_severity": worst,
    }


# When two findings tie on severity, prefer the one whose detection method
# is hardest to dismiss. Measurements (TVLA, crash, memory, CPA) outrank
# predictions (static lint) because they are evidence the function ACTUALLY
# misbehaved on real silicon, not a guess from grepping the source. A run
# where ct_lint says "comparator" AND TVLA says |t|>4.5 should headline as
# `leak_detected`, not `static_warning` -- the former tells the operator
# "we observed it leak", the latter tells them "we suspect it might".
_TYPE_TIEBREAK = {
    "memory_corruption": 0,
    "crash":             1,
    "tvla":              2,
    "cpa_key_recovery":  3,
    "length_oracle":     4,
    "non_determinism":   5,
    "static":            6,
}


def derive_verdict(findings: List[Finding]) -> str:
    """Pick the headline verdict from the worst finding's type.

    Sort key is `(severity_rank, type_tiebreak)`: severity dominates, but
    when two findings tie at the same severity we prefer measurement-based
    types over the static lint so the headline reflects the strongest
    evidence we have.
    """
    if not findings or all(f.severity == "pass" for f in findings):
        return "safe"
    bad = [f for f in findings if f.severity != "pass"]
    bad.sort(key=lambda f: (severity_rank(f.severity),
                            _TYPE_TIEBREAK.get(f.type, 99)))
    return TYPE_TO_VERDICT.get(bad[0].type, "leak_detected")


def to_json_list(findings: List[Finding]) -> List[Dict[str, Any]]:
    return [f.to_dict() for f in findings]


# =============================================================================
# TargetReport -- one scanned source file's full result envelope
# =============================================================================

@dataclass
class TargetReport:
    """All findings for ONE scanned source file, plus rollup metadata.
    This is the JSON shape scan_target.py prints to stdout for hardwarego."""
    target: str                        # repo-relative path of the source file
    verdict: str                       # "safe" | "leak_detected" | "crash_detected" | ...
    worst_severity: str                # severity rollup over `findings`
    findings: List[Finding]
    started: float                     # epoch seconds
    finished: float                    # epoch seconds
    n_traces: int
    stage_secs: Dict[str, float]       # per-stage timing for dashboards

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target":         self.target,
            "verdict":        self.verdict,
            "worst_severity": self.worst_severity,
            "findings":       to_json_list(self.findings),
            "summary":        summarize(self.findings),
            "started":        float(self.started),
            "finished":       float(self.finished),
            "duration_secs":  float(self.finished - self.started),
            "n_traces":       int(self.n_traces),
            "stage_secs":     {k: float(v) for k, v in self.stage_secs.items()},
        }


def merge(findings: List[Finding],
          *,
          target: str,
          started: float,
          finished: float,
          n_traces: int,
          stage_secs: Dict[str, float]) -> TargetReport:
    """Roll a flat list of Findings into a TargetReport."""
    sevs = [f.severity for f in findings if f.severity != "pass"]
    return TargetReport(
        target=target,
        verdict=derive_verdict(findings),
        worst_severity=worst_severity(sevs) if sevs else "pass",
        findings=list(findings),
        started=started,
        finished=finished,
        n_traces=n_traces,
        stage_secs=dict(stage_secs),
    )
