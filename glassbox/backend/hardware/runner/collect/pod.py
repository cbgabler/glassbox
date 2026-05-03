"""Pod I/O -- talk to the hardware harness over USB CDC.

The "pod" is the Raspberry Pi Pico running raspberry/harness/harness.ino.
The Pico is the ONLY thing the host directly speaks to over USB; it in turn
drives UART0 to the ESP32 victim and proxies measurement traces back.

Wire protocol (host <-> Pico, baud 115200, line-oriented):

    READY harness v1\n                <- Pico boot banner (DTR-triggered reset)

    RUN <fn_id> <hex_input>\n         -> request a trace
    RES2 <c> <us> <i> <b> <hex_out>\n <- ESP's response (forwarded by Pico)
    TRACE s0,s1,s2,...,s255\n         <- INA169 ADC samples, captured by Pico
                                         during the function's trigger window

    STATUS\n                          -> liveness probe
    STATUS running\n                  <- ack (or "STATUS quarantined" -- legacy)

    BRIDGE <seconds>\n                -> enter USB<->UART0 passthrough
                                         (used ONLY by auto_flash; pod.py never
                                         calls this -- it would lose the line)

Fault responses (any of these can replace RES2/TRACE on a given RUN):

    ERR <reason>\n                    bad command, bad hex, etc.
    PANIC <pc> <reason>\n             ESP32 panic handler tripped
    MEMVIOL <kind> overrun=<n>\n      v2 memory-safety guard fired
    (no reply within timeout)         hang -- chip wedged or wire fell off

Function IDs (must match esp/harness/harness.ino:188 FUNCTIONS[]):

    0 = noop              calibration baseline
    1 = strcmp_naive      timing-leak demo
    2 = strcmp_safe       constant-time demo
    3 = gb_target_call    USER-SUPPLIED target (the scanner's only target)

This module is the ONLY place that imports pyserial. Everything above it
(collect/traces.py, scan_target.py) gets a `Pod` handle and high-level
verbs. Tests can fake `Pod` by subclassing without touching real hardware.

Reference impl: cs370/runner/runner.py (lines 38-261). Adapted to the
class-based shape used by the new collect/ package layout.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple, Union

import numpy as np
import serial


# Must match TRACE_LEN in raspberry/harness/harness.ino.
TRACE_LEN = 256

# Function IDs registered in esp/harness/harness.ino's FUNCTIONS[] table.
FN_NOOP         = 0
FN_STRCMP_NAIVE = 1
FN_STRCMP_SAFE  = 2
FN_GB_TARGET    = 3   # the user-supplied target -- scanner mode only uses this

# The ESP firmware HARDCODES the secret (esp/harness/harness.ino:138).
# There is no over-the-wire command to change it; if we ever add one we'll
# expose `Pod.set_secret(...)`. For now, the analyze layer can use this
# constant to compute true_rank for CPA against fn_id=1/2.
SECRET = b"hunter2!"

# Hard cap on a single line of serial output. The longest legitimate line is
# `TRACE` with 256 uint16 samples in decimal-comma form, ~5 chars per sample
# = ~1.5 KB. 8 KB is conservative; anything larger is corrupted framing.
_MAX_LINE_BYTES = 8192


# =============================================================================
# Errors
# =============================================================================

class PodError(RuntimeError):
    """Anything wrong with the serial conversation -- timeouts, bad framing,
    harness reporting an internal fault that we don't classify further."""


# =============================================================================
# Result types
# =============================================================================

@dataclass
class Trace:
    """One successful measurement returned from the harness."""
    fn_id: int
    input_hex: str
    cycles: int
    micros: int
    insns: int                        # 0 if PMU not available
    branches: int                     # 0 if PMU not available
    hex_output: str                   # function's return bytes, hex-encoded
    power: np.ndarray                 # shape (TRACE_LEN,), uint16 ADC samples


@dataclass
class TraceFailure:
    """The firmware did NOT return a clean RES2/TRACE pair. Carries enough
    metadata for the pipeline to build a CrashFinding without raising --
    raising would abort whole campaigns on a single bad input.

    `kind` is one of:
        "timeout"      runner did not see a reply within timeout
        "panic"        firmware emitted PANIC <pc> <reason>
        "err_response" firmware emitted ERR <message>
        "memory"       firmware emitted MEMVIOL <kind> overrun=<n>
        "wdt_reset"    firmware reset itself (we saw a 'READY' mid-campaign)
        "framing"      a TRACE line didn't have the right shape
    """
    kind: str
    raw: str = ""
    panic_pc: str = ""
    panic_reason: str = ""
    memory_kind: str = ""
    memory_overrun_bytes: int = 0


# =============================================================================
# Free-function parsers (testable without a real serial port)
# =============================================================================

def parse_res2(line: str) -> Tuple[int, int, int, int, str]:
    """Parse a RES2 line into (cycles, micros, insns, branches, hex_output).

    Tolerates a missing trailing hex_output (the firmware emits no token
    when out_len == 0) by returning "" for it.

    Raises PodError on any other shape.
    """
    if not line.startswith("RES2 "):
        raise PodError(f"expected RES2, got {line!r}")
    parts = line.split(maxsplit=5)
    # parts[0]="RES2", then 4 numerics, optional hex.
    if len(parts) < 5:
        raise PodError(f"malformed RES2: {line!r}")
    try:
        cycles, micros, insns, branches = (int(parts[i]) for i in (1, 2, 3, 4))
    except ValueError as e:
        raise PodError(f"non-integer field in RES2: {line!r}") from e
    hex_output = parts[5] if len(parts) == 6 else ""
    return cycles, micros, insns, branches, hex_output


def parse_trace_line(line: str) -> np.ndarray:
    """Parse a 'TRACE s0,s1,...,s255' line into a uint16 numpy array.

    Raises PodError if the prefix is wrong or the sample count != TRACE_LEN.
    """
    if not line.startswith("TRACE "):
        raise PodError(f"expected TRACE, got {line!r}")
    body = line[len("TRACE "):]
    # np.fromstring is deprecated but still the fastest path for this format;
    # use the explicit loop fallback to silence the warning on newer numpy.
    try:
        samples = np.fromstring(body, sep=",", dtype=np.uint16)
    except Exception:
        samples = np.array(
            [int(t) for t in body.split(",") if t.strip()],
            dtype=np.uint16,
        )
    if samples.size != TRACE_LEN:
        raise PodError(
            f"expected {TRACE_LEN} samples, got {samples.size} in {line[:60]!r}"
        )
    return samples


# =============================================================================
# Pod handle
# =============================================================================

class Pod:
    """Thin wrapper around a pyserial connection to the Pico harness.

    Use `open_pod(port)` to construct one (it waits for the READY banner
    and tunes timeouts). Use `with Pod(...) as p:` to auto-close.
    """

    def __init__(self, ser: serial.Serial):
        self._ser = ser

    # -- lifecycle -------------------------------------------------------------

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass

    def __enter__(self) -> "Pod":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- low-level framing -----------------------------------------------------

    def _write_line(self, line: str) -> None:
        self._ser.write((line + "\n").encode("ascii"))
        self._ser.flush()

    def _read_line(self, timeout_s: float = 2.0) -> str:
        """Block until '\\n' or timeout. Strips CR and trailing whitespace.
        Raises PodError on timeout (so callers can convert to TraceFailure)."""
        deadline = time.monotonic() + timeout_s
        buf = bytearray()
        while time.monotonic() < deadline:
            b = self._ser.read(1)
            if not b:
                continue
            if b == b"\r":
                continue
            if b == b"\n":
                return buf.decode("utf-8", errors="replace").strip()
            buf.extend(b)
            if len(buf) > _MAX_LINE_BYTES:
                raise PodError(
                    f"line too long ({len(buf)} bytes); framing lost"
                )
        partial = buf.decode("utf-8", errors="replace")
        raise PodError(
            f"no '\\n' within {timeout_s}s; partial={partial!r}"
        )

    # -- high-level verbs ------------------------------------------------------

    def status(self, timeout_s: float = 2.0) -> str:
        """Send STATUS, return one of: 'running', 'quarantined', 'unknown'.

        Used by auto_flash.verify_post_flash and as a generic liveness check.
        Returns 'unknown' if the harness reply is unparseable rather than
        raising -- this method is meant to be cheap and noisy-tolerant.
        """
        try:
            self._write_line("STATUS")
            line = self._read_line(timeout_s=timeout_s)
        except PodError:
            return "unknown"
        if not line.startswith("STATUS "):
            return "unknown"
        state = line[len("STATUS "):].strip().lower()
        return state or "unknown"

    def request_trace(self, fn_id: int, input_bytes: bytes,
                      *, res_timeout_s: float = 2.0,
                      trace_timeout_s: float = 2.0) -> Trace:
        """Run FUNCTIONS[fn_id](input_bytes) on the ESP and return one Trace.

        Reads two lines back: RES2 (from the ESP) then TRACE (synthesized by
        the Pico from its INA169 samples during the trigger window).

        Raises PodError on any protocol fault. For campaigns that need to
        TOLERATE per-trace failures (fuzzing, large sweeps), use
        `request_trace_safe()` instead -- it converts faults into TraceFailure
        records so a single bad input doesn't kill an N-thousand-trace run.
        """
        hex_input = input_bytes.hex()
        self._write_line(f"RUN {fn_id} {hex_input}")
        res_line = self._read_line(timeout_s=res_timeout_s)

        # Hard-error replies -- raise so the caller knows the chip is sick.
        if res_line.startswith("ERR "):
            raise PodError(f"pod ERR: {res_line}")
        if res_line.startswith("PANIC "):
            raise PodError(f"pod PANIC: {res_line}")
        if res_line.startswith("MEMVIOL "):
            raise PodError(f"pod MEMVIOL: {res_line}")
        if res_line.startswith("READY"):
            raise PodError(f"chip reset mid-call (saw READY): {res_line}")

        cycles, micros, insns, branches, hex_output = parse_res2(res_line)
        trace_line = self._read_line(timeout_s=trace_timeout_s)
        power = parse_trace_line(trace_line)

        return Trace(
            fn_id=fn_id,
            input_hex=hex_input,
            cycles=cycles,
            micros=micros,
            insns=insns,
            branches=branches,
            hex_output=hex_output,
            power=power,
        )

    def request_trace_safe(self, fn_id: int, input_bytes: bytes,
                           *, res_timeout_s: float = 2.0,
                           trace_timeout_s: float = 2.0
                           ) -> Union[Trace, TraceFailure]:
        """Like `request_trace`, but firmware faults become TraceFailure
        records instead of exceptions. Used by sweep / fuzz campaigns.
        """
        hex_input = input_bytes.hex()
        try:
            self._write_line(f"RUN {fn_id} {hex_input}")
            res_line = self._read_line(timeout_s=res_timeout_s)
        except PodError as e:
            return TraceFailure(kind="timeout", raw=str(e))

        if res_line.startswith("ERR "):
            return TraceFailure(kind="err_response", raw=res_line)
        if res_line.startswith("PANIC "):
            parts = res_line.split(maxsplit=2)
            pc     = parts[1] if len(parts) > 1 else ""
            reason = parts[2] if len(parts) > 2 else ""
            return TraceFailure(kind="panic", raw=res_line,
                                panic_pc=pc, panic_reason=reason)
        if res_line.startswith("MEMVIOL "):
            rest = res_line[len("MEMVIOL "):].strip()
            kind, _, tail = rest.partition(" ")
            overrun = 0
            for tok in tail.split():
                if tok.startswith("overrun="):
                    try:
                        overrun = int(tok.split("=", 1)[1])
                    except ValueError:
                        pass
            return TraceFailure(kind="memory", raw=res_line,
                                memory_kind=kind, memory_overrun_bytes=overrun)
        if res_line.startswith("READY"):
            return TraceFailure(kind="wdt_reset", raw=res_line)

        try:
            cycles, micros, insns, branches, hex_output = parse_res2(res_line)
            trace_line = self._read_line(timeout_s=trace_timeout_s)
            power      = parse_trace_line(trace_line)
        except PodError as e:
            return TraceFailure(kind="framing", raw=str(e))

        return Trace(
            fn_id=fn_id,
            input_hex=hex_input,
            cycles=cycles,
            micros=micros,
            insns=insns,
            branches=branches,
            hex_output=hex_output,
            power=power,
        )


# =============================================================================
# Open / discovery
# =============================================================================

def open_pod(port: str,
             baud: int = 115200,
             ready_timeout_s: float = 8.0,
             *,
             quiet: bool = False) -> Pod:
    """Open the Pico's USB CDC port and wait for the 'READY' banner.

    Opening the port toggles DTR, which resets the Pico, which then prints
    'READY harness v1'. We accumulate bytes until we see that string, then
    drain any trailing chars and bump the read timeout to 2s for normal
    request/response use.

    If the Pico had already booted before we opened (no DTR reset, e.g. we
    re-ran the runner without unplugging anything), we won't see READY --
    after the timeout we assume it's alive and warn unless `quiet=True`.

    Args:
        port:             OS-level device path, e.g. "/dev/cu.usbmodem1101"
                          or "COM5".
        baud:             must match the Pico harness (115200 by default).
        ready_timeout_s:  how long to wait for the READY banner.
        quiet:            suppress the [pod] log lines.

    Returns:
        A `Pod` handle. Caller is responsible for `.close()` (or use `with`).
    """
    ser = serial.Serial(port, baud, timeout=0.2)   # short read for the drain loop

    deadline = time.monotonic() + ready_timeout_s
    buf = bytearray()
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf.extend(chunk)
            if b"READY" in buf:
                # Let the rest of the banner land, then drop everything.
                time.sleep(0.2)
                ser.reset_input_buffer()
                ser.timeout = 2
                if not quiet:
                    banner = buf.decode("utf-8", errors="replace").strip()
                    print(f"[pod] READY: {banner!r}")
                return Pod(ser)
        # If nothing has arrived yet, the Pico probably already booted before
        # we opened the port (so no DTR reset triggered the banner). Nudge
        # with a blank line; the Pico's command parser silently ignores it
        # but answering activity proves we have a live connection.
        if not chunk and len(buf) == 0:
            try:
                ser.write(b"\n")
            except Exception:
                pass

    if not quiet:
        print(
            f"[pod] WARN: no READY in {ready_timeout_s}s; "
            f"buf={bytes(buf)!r}; assuming pod alive"
        )
    ser.reset_input_buffer()
    ser.timeout = 2
    return Pod(ser)


# =============================================================================
# CLI smoketest -- `python -m runner.collect.pod /dev/cu.usbmodem1101`
# =============================================================================

def _smoketest(port: str) -> int:
    """Open the pod, ping STATUS, run a single noop trace. Exits 0 on
    success, non-zero with a printed reason on failure."""
    print(f"[pod-smoketest] opening {port}")
    try:
        pod = open_pod(port)
    except Exception as e:
        print(f"[pod-smoketest] FAIL: open: {e}")
        return 2

    try:
        state = pod.status()
        print(f"[pod-smoketest] STATUS -> {state}")
        if state not in ("running", "quarantined"):
            print("[pod-smoketest] FAIL: unexpected STATUS reply")
            return 3

        tr = pod.request_trace(FN_NOOP, b"")
        print(
            f"[pod-smoketest] noop OK: cycles={tr.cycles} micros={tr.micros} "
            f"power.shape={tr.power.shape} hex_out={tr.hex_output!r}"
        )
    except Exception as e:
        print(f"[pod-smoketest] FAIL: {type(e).__name__}: {e}")
        return 4
    finally:
        pod.close()

    print("[pod-smoketest] OK")
    return 0


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Pod connectivity smoketest.")
    p.add_argument("port", help="e.g. /dev/cu.usbmodem1101 or COM5")
    args = p.parse_args()
    return _smoketest(args.port)


if __name__ == "__main__":
    raise SystemExit(main())
