"""auto_flash.py -- "plug in both devices, run one command, get a flash."

It handles three things, all of which are tedious to do correctly by hand:

1. Port discovery. Find which serial port is the ESP32 victim and which
    is the Pico monitor. Uses pyserial.tools.list_ports + USB VID:PID
    identification. Both can also be overridden with explicit args.
2. Toolchain detection + dispatch. We support TWO flashing toolchains:
      arduino-cli (cross-platform; C/C++/asm sketches; the default)
      platformio  (heavier; required for Rust/Zig FFI projects, since
                  Arduino can't link external static libs)
    We pick automatically based on the source-language metadata
    compile_target.py wrote out, but the user can force one.
3. Post-flash verification. After upload, ESP32 reboots and re-attaches
    to UART2. We open the Pico, send a STATUS byte, and wait for the
    harness to reply with its banner -- this confirms the new firmware
    is alive on the chip, not just that esptool didn't error.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
  from serial.tools import list_ports as _list_ports          # type: ignore
except Exception:                                                # pragma: no cover
  _list_ports = None


# -----------------------------------------------------------------------------
# Repo paths
# -----------------------------------------------------------------------------

# Sketch directory the Arduino IDE / arduino-cli expects.
# auto_flash.py lives at backend/hardware/, and the ESP harness sketch is
# its sibling at backend/hardware/esp/harness/.
_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SKETCH_DIR = os.path.normpath(os.path.join(_HERE, "esp", "harness"))

# Default board for the ESP32 victim
DEFAULT_FQBN = "esp32:esp32:esp32"

DEFAULT_PIO_PROJECT_DIR = DEFAULT_SKETCH_DIR

# -----------------------------------------------------------------------------
# USB VID:PID identification
# -----------------------------------------------------------------------------

# (vid, pid) pairs that identify each device. We err on the side of
# matching MORE rather than less -- the worst case is we ask the user
# to disambiguate with --esp-port / --pico-port.
_ESP32_USB_IDS: List[Tuple[int, int]] = [
  (0x10C4, 0xEA60),   # Silicon Labs CP210x (most DOIT / NodeMCU boards)
  (0x1A86, 0x7523),   # WCH CH340 (cheaper clones)
  (0x1A86, 0x55D4),   # WCH CH9102
  (0x303A, 0x1001),   # Espressif native USB on S2/S3/C3 (ESP32-S/C ROM)
  (0x303A, 0x4001),
]
_PICO_USB_IDS: List[Tuple[int, int]] = [
  (0x2E8A, 0x000A),   # Raspberry Pi Pico (CDC running our harness)
  (0x2E8A, 0x0005),   # Pico in BOOTSEL mode (we should NOT flash to this)
]


@dataclass
class DetectedPort:
  device: str             # "/dev/cu.usbmodemXYZ" or "COM5"
  vid: Optional[int]
  pid: Optional[int]
  description: str
  role: str               # "esp32" | "pico" | "unknown"

  def __str__(self) -> str:
    v = f"{self.vid:04X}" if self.vid is not None else "----"
    p = f"{self.pid:04X}" if self.pid is not None else "----"
    return f"{self.device}  [{v}:{p}]  {self.description}  ({self.role})"


def list_ports() -> List[DetectedPort]:
  """Return every visible serial port, tagged with its likely role."""
  if _list_ports is None:
    return []
  out: List[DetectedPort] = []
  for p in _list_ports.comports():
    vid = getattr(p, "vid", None)
    pid = getattr(p, "pid", None)
    role = "unknown"
    if vid is not None and pid is not None:
      if (vid, pid) in _ESP32_USB_IDS:
        role = "esp32"
      elif (vid, pid) in _PICO_USB_IDS:
        role = "pico"
    out.append(DetectedPort(
        device=p.device,
        vid=vid, pid=pid,
        description=str(p.description or ""),
        role=role,
    ))
  return out


def detect_esp_port() -> Optional[str]:
  """Return the ESP32 victim's port, or None if we can't identify it."""
  cands = [p for p in list_ports() if p.role == "esp32"]
  if len(cands) == 1:
    return cands[0].device
  return None


def detect_pico_port() -> Optional[str]:
  """Return the Pico monitor's port, or None if we can't identify it."""
  cands = [p for p in list_ports() if p.role == "pico"]
  if len(cands) == 1:
    return cands[0].device
  return None


# -----------------------------------------------------------------------------
# Toolchain detection
# -----------------------------------------------------------------------------

@dataclass
class Toolchains:
  arduino_cli: bool
  platformio:  bool

  def any(self) -> bool:
    return self.arduino_cli or self.platformio


def detect_toolchains() -> Toolchains:
  return Toolchains(
      arduino_cli=(shutil.which("arduino-cli") is not None),
      platformio=(shutil.which("pio") is not None or
                  shutil.which("platformio") is not None),
  )


def _pio_executable() -> Optional[str]:
  return shutil.which("pio") or shutil.which("platformio")


# -----------------------------------------------------------------------------
# Subprocess helper that streams output live (compile/upload can take 30+ s)
# -----------------------------------------------------------------------------

def _run_streaming(cmd: List[str], *, cwd: Optional[str] = None,
                  timeout: float = 300.0) -> int:
  """Run a command, mirror stdout/stderr to ours line-by-line, return rc."""
  print(f"    $ {' '.join(cmd)}")
  try:
    p = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
  except OSError as e:
    print(f"    {cmd[0]}: failed to spawn: {e}")
    return 127
  deadline = time.monotonic() + timeout
  assert p.stdout is not None
  try:
    for line in p.stdout:
      print("    | " + line.rstrip())
      if time.monotonic() > deadline:
        p.kill()
        print(f"    timed out after {timeout:.0f}s; killed.")
        return 124
  except KeyboardInterrupt:                                  # pragma: no cover
    p.kill()
    raise
  return p.wait()


# -----------------------------------------------------------------------------
# arduino-cli flow (the default for C/C++ harness sketches)
# -----------------------------------------------------------------------------

def _ensure_esp32_core_installed() -> bool:
  """Best-effort: check that the esp32 core is installed in arduino-cli.
  We don't auto-install it because that's a network operation that can
  take minutes -- we just print the exact command on miss."""
  try:
    r = subprocess.run(
        ["arduino-cli", "core", "list"],
        capture_output=True, text=True, timeout=20, check=False,
    )
  except (OSError, subprocess.TimeoutExpired):
    return False
  if "esp32:esp32" in r.stdout:
    return True
  print("    arduino-cli: esp32 core NOT installed.")
  print("    Run this once, then re-try the flash:")
  print('      arduino-cli config init')
  print('      arduino-cli config add board_manager.additional_urls '
        'https://espressif.github.io/arduino-esp32/package_esp32_index.json')
  print('      arduino-cli core update-index')
  print('      arduino-cli core install esp32:esp32')
  return False


def flash_arduino_cli(sketch_dir: str, fqbn: str, port: str,
                    *, timeout: float = 240.0) -> int:
  """Compile + upload an Arduino sketch via arduino-cli.

  Returns 0 on success, non-zero rc on failure.
  """
  if shutil.which("arduino-cli") is None:
    print("    arduino-cli not found in PATH.")
    print("    Install it: https://arduino.github.io/arduino-cli/latest/installation/")
    return 127
  if not _ensure_esp32_core_installed():
    return 1
  print(f"[auto_flash] arduino-cli: compile {sketch_dir} (fqbn={fqbn})")
  rc = _run_streaming(
      ["arduino-cli", "compile", "--fqbn", fqbn, sketch_dir],
      timeout=timeout,
  )
  if rc != 0:
    print(f"[auto_flash] arduino-cli compile FAILED (rc={rc}).")
    return rc
  print(f"[auto_flash] arduino-cli: upload to {port}")
  rc = _run_streaming(
      ["arduino-cli", "upload", "--fqbn", fqbn, "--port", port, sketch_dir],
      timeout=timeout,
  )
  if rc != 0:
    print(f"[auto_flash] arduino-cli upload FAILED (rc={rc}).")
    return rc
  print("[auto_flash] arduino-cli: flash OK.")
  return 0


# -----------------------------------------------------------------------------
# PlatformIO flow (used when an FFI project must be linked into the firmware)
# -----------------------------------------------------------------------------

def flash_platformio(project_dir: str, *, environment: Optional[str] = None,
                    timeout: float = 360.0) -> int:
  """Run `pio run -t upload` on a PlatformIO project.

  The PlatformIO project's `platformio.ini` MUST point at the ESP32 board
  and at the static lib produced by the Rust / Zig project that
  compile_target.py scaffolded. We don't try to inject those build_flags
  here -- that's compile_target.py's job at scaffolding time.
  """
  pio = _pio_executable()
  if pio is None:
    print("    platformio not found in PATH.")
    print("    Install it: pip install platformio")
    return 127
  if not os.path.isfile(os.path.join(project_dir, "platformio.ini")):
    print(f"[auto_flash] platformio: no platformio.ini in {project_dir}.")
    print("    Initialize one with `pio project init` or generate via "
          "compile_target.py's Rust/Zig scaffolding output.")
    return 1
  print(f"[auto_flash] platformio: run -t upload  (project={project_dir})")
  cmd = [pio, "run", "-t", "upload"]
  if environment:
    cmd += ["-e", environment]
  rc = _run_streaming(cmd, cwd=project_dir, timeout=timeout)
  if rc != 0:
    print(f"[auto_flash] platformio FAILED (rc={rc}).")
    return rc
  print("[auto_flash] platformio: flash OK.")
  return 0


# -----------------------------------------------------------------------------
# Bridged flashing: drive the ESP32's bootloader UART through the Pico
# -----------------------------------------------------------------------------
#
# Route A (the only path supported in v3 when the ESP32 USB isn't plugged
# in): the Pico sits on ESP32 GPIO1/3 (UART0, the bootloader UART) and
# already has GP3/GP4 wired to EN and GPIO0. We tell its harness firmware
# "BRIDGE <seconds>" over USB CDC, which switches it into transparent
# byte-forwarding + DTR/RTS->EN/GPIO0 translation. Then we hand the Pico's
# port name to arduino-cli, which thinks it's talking to a CP2102 chip.
# When arduino-cli closes the port, the Pico sees DTR drop, exits bridge
# mode, and is ready for the verification step.

_BRIDGE_DEFAULT_SECONDS = 90


def _send_bridge_command(pico_port: str, seconds: int = _BRIDGE_DEFAULT_SECONDS,
                        *, baud: int = 115200, timeout_s: float = 2.5) -> bool:
  """Open the Pico's USB CDC, send 'BRIDGE <seconds>\\n', wait for ACK, close.

  After this returns, the Pico is in BRIDGE mode and the SAME port can
  immediately be opened by arduino-cli / esptool to flash the ESP32.
  """
  try:
    import serial as _serial                            # type: ignore
  except Exception as e:
    print(f"[auto_flash] cannot enter bridge mode: pyserial missing ({e})")
    return False

  print(f"[auto_flash] bridge: requesting {seconds}s passthrough on {pico_port}")
  try:
    ser = _serial.Serial(pico_port, baudrate=baud, timeout=0.5)
  except Exception as e:
    print(f"[auto_flash] bridge: open({pico_port}) failed: {e}")
    return False
  try:
    try:
      ser.reset_input_buffer()
    except Exception:
      pass
    ser.write(f"BRIDGE {seconds}\n".encode("ascii"))
    ser.flush()
    deadline = time.monotonic() + timeout_s
    buf = b""
    while time.monotonic() < deadline:
      chunk = ser.read(64)
      if chunk:
        buf += chunk
        if b"ACK bridge" in buf:
          print(f"[auto_flash] bridge: Pico ACK ('{buf.strip().decode('ascii', 'replace')}')")
          return True
        if b"\n" in buf and b"ACK bridge" not in buf:
          print(f"[auto_flash] bridge: Pico did NOT ACK -- replied: "
                f"{buf.strip().decode('ascii', 'replace')!r}")
          return False
      else:
        time.sleep(0.05)
    print(f"[auto_flash] bridge: timed out waiting for ACK after {timeout_s:.1f}s")
    return False
  finally:
    try:
      ser.close()
    except Exception:
      pass


# -----------------------------------------------------------------------------
# Post-flash verification through the Pico
# -----------------------------------------------------------------------------

def verify_post_flash(pico_port: str, *, baud: int = 115200,
                      timeout_s: float = 12.0) -> bool:
  """Confirm the freshly-flashed ESP32 harness is alive end-to-end.

  Inline STATUS probe -- no dependency on the (not-yet-implemented)
  `runner.open_pod`. We open the Pico's USB CDC and write `STATUS\\n`.
  The Pico's command parser proxies that onto its UART link to the
  ESP32, waits for the ESP32's reply, and forwards it back. So a single
  round-trip exercises the entire chain:

      host  --(USB CDC)-->  Pico  --(UART0)-->  ESP32
                                        \\---(reply)--/

  The Pico's reply (per `raspberry/harness/harness.ino`) is one of:
    `STATUS running`             -- ESP32 alive, RUN commands accepted
    `STATUS quarantined`         -- ESP32 alive, RUN commands refused
    `STATUS unresponsive (...)`  -- ESP32 not replying over UART

  Returns True for the first two, False for the third or for an overall
  timeout. We accept "quarantined" as success because the chip is
  demonstrably running our firmware -- "verified alive" is the criterion
  here, not "willing to accept work".
  """
  print(f"[auto_flash] verifying via Pico {pico_port} (timeout {timeout_s:.0f}s)")
  try:
    import serial as _serial                              # type: ignore
  except Exception as e:
    print(f"    pyserial missing ({e}); cannot verify.")
    return False

  try:
    ser = _serial.Serial(pico_port, baudrate=baud, timeout=0.25)
  except Exception as e:
    print(f"    open({pico_port}) failed: {e}")
    return False

  try:
    # Settle: the Pico's USB CDC stack just had its DTR toggled by our
    # open(); give it a beat, then drop any stale RX (e.g. a trailing
    # ACK from a previous BRIDGE session that hasn't been consumed).
    time.sleep(0.3)
    try:
      ser.reset_input_buffer()
    except Exception:
      pass

    ser.write(b"STATUS\n")
    ser.flush()

    deadline = time.monotonic() + timeout_s
    buf = b""
    while time.monotonic() < deadline:
      chunk = ser.read(128)
      if not chunk:
        continue
      buf += chunk
      while b"\n" in buf:
        line, _, buf = buf.partition(b"\n")
        text = line.decode("ascii", "replace").strip()
        if not text:
          continue
        print(f"    pico: {text}")
        if text.startswith("STATUS running"):
          print("[auto_flash] post-flash verification OK -- harness running.")
          return True
        if text.startswith("STATUS quarantined"):
          print("[auto_flash] post-flash verification OK -- harness alive "
                "(quarantined; clear with 'UNQUARANTINE').")
          return True
        if text.startswith("STATUS unresponsive"):
          print("[auto_flash] post-flash verification FAILED -- "
                "Pico reachable but ESP32 not responding over UART.")
          return False
        # Anything else -- ignore and keep listening until deadline.

    print(f"[auto_flash] post-flash verification TIMEOUT after {timeout_s:.1f}s "
          f"(no STATUS reply from Pico on {pico_port}).")
    return False
  finally:
    try:
      ser.close()
    except Exception:
      pass


# -----------------------------------------------------------------------------
# High-level: pick the right flow and run it
# -----------------------------------------------------------------------------

# Languages that arduino-cli can flash directly (the source becomes part of
# the harness sketch via gb_target.cpp). Rust / Zig need PlatformIO because
# Arduino can't link external static libraries.
_LANGS_FOR_ARDUINO = {"c", "cpp", "asm"}
_LANGS_FOR_PIO     = {"rust", "zig"}


def choose_toolchain(language: str, override: Optional[str] = None,
                    toolchains: Optional[Toolchains] = None
                    ) -> Tuple[str, Optional[str]]:
  """Pick which flow to run for a given source language.

  Returns (toolchain, reason). If we can't find a usable toolchain, returns
  (toolchain="", reason="...explanation...").
  """
  tc = toolchains or detect_toolchains()
  if override:
    if override == "arduino-cli":
      if not tc.arduino_cli:
        return ("", "arduino-cli requested but not installed")
      return ("arduino-cli", "user override")
    if override == "platformio":
      if not tc.platformio:
        return ("", "platformio requested but not installed")
      return ("platformio", "user override")
    return ("", f"unknown toolchain override: {override!r}")
  # No override -- pick by language.
  if language in _LANGS_FOR_ARDUINO and tc.arduino_cli:
    return ("arduino-cli", f"{language} -> arduino-cli (default for compiled C/C++/asm)")
  if language in _LANGS_FOR_PIO and tc.platformio:
    return ("platformio", f"{language} -> platformio (Arduino cannot link external .a)")
  # Fallback: prefer arduino-cli because the harness sketch already builds.
  if tc.arduino_cli:
    return ("arduino-cli", f"fallback: arduino-cli (no native flow for {language})")
  if tc.platformio:
    return ("platformio", f"fallback: platformio")
  return ("", "no flashing toolchain installed (need arduino-cli or platformio)")


def flash_target(*, language: str,
                esp_port: Optional[str] = None,
                pico_port: Optional[str] = None,
                fqbn: str = DEFAULT_FQBN,
                sketch_dir: str = DEFAULT_SKETCH_DIR,
                pio_project_dir: str = DEFAULT_PIO_PROJECT_DIR,
                toolchain_override: Optional[str] = None,
                verify: bool = True,
                via_pico: Optional[bool] = None,
                bridge_seconds: int = _BRIDGE_DEFAULT_SECONDS) -> int:
  """One-call entry point. Returns 0 on success, non-zero rc on failure.

  Caller is responsible for already having dropped the user source into
  `<sketch_dir>/gb_target.cpp` (compile_target.py / glassbox_check.py
  already do that). This function only handles compile + upload + verify.

  Port selection logic (v3):
    * via_pico=True  : ALWAYS bridge through the Pico (ignore any ESP32
                        port that happens to be visible).
    * via_pico=False : ALWAYS use a directly-connected ESP32 port; fail
                        if there isn't one.
    * via_pico=None  : auto -- bridge through the Pico iff that's the
                        only board we can see (the typical Route A
                        operator workflow).
  """
  tc = detect_toolchains()
  if not tc.any():
    print("[auto_flash] No flashing toolchain installed.")
    print("  Install ONE of:")
    print("    arduino-cli (https://arduino.github.io/arduino-cli/latest/installation/)")
    print("    platformio  (pip install platformio)")
    return 127

  chosen, reason = choose_toolchain(language, toolchain_override, tc)
  if not chosen:
    print(f"[auto_flash] cannot pick a toolchain: {reason}")
    return 1
  print(f"[auto_flash] toolchain: {chosen}  ({reason})")

  # ---- Port discovery + bridged-mode decision ----------------------------
  detected_esp  = esp_port  if esp_port  else detect_esp_port()
  detected_pico = pico_port if pico_port else detect_pico_port()

  if via_pico is None:
    if detected_esp is None and detected_pico is not None:
      via_pico = True
      print("[auto_flash] only a Pico is connected -> bridged-flash mode (Route A).")
    elif detected_esp is not None:
      via_pico = False
    else:
      via_pico = False  # let the "no ESP32 port" branch print a useful error

  if via_pico:
    if detected_pico is None:
      print("[auto_flash] bridged mode requested but no Pico detected.")
      print("  Visible ports:")
      for p in list_ports():
        print(f"    {p}")
      print("  Pass --pico-port /dev/<...> to disambiguate.")
      return 1
    if chosen != "arduino-cli":
      print(f"[auto_flash] bridged mode currently only supports arduino-cli "
            f"(got '{chosen}'). Plug the ESP32 USB in directly for "
            f"{chosen}-based flashing, or pass --toolchain arduino-cli.")
      return 1
    flash_port = detected_pico
    print(f"[auto_flash] flashing through Pico CDC: {flash_port}")
    if not _send_bridge_command(flash_port, seconds=bridge_seconds):
      print("[auto_flash] aborting -- Pico did not enter bridge mode.")
      return 1
    # Small grace period so the Pico's USB stack is fully idle before
    # arduino-cli reopens the same port.
    time.sleep(0.4)
  else:
    if detected_esp is None:
      print("[auto_flash] could not auto-detect the ESP32 serial port.")
      print("  Visible ports:")
      for p in list_ports():
        print(f"    {p}")
      print("  Pass --esp-port /dev/<...>, or use --via-pico to flash via the Pico.")
      return 1
    flash_port = detected_esp
    print(f"[auto_flash] ESP32 port: {flash_port}")

  # ---- Compile + upload --------------------------------------------------
  if chosen == "arduino-cli":
    rc = flash_arduino_cli(sketch_dir, fqbn, flash_port)
  else:
    rc = flash_platformio(pio_project_dir)
  if rc != 0:
    return rc

  if via_pico:
    # Pico exits BRIDGE on its own (DTR drop after upload), but give it a
    # moment to settle before we try to talk to it as a harness again.
    time.sleep(1.0)

  if not verify:
    return 0

  pico_for_verify = detected_pico
  if pico_for_verify is None:
    print("[auto_flash] flash succeeded but Pico port not auto-detected; "
          "skipping post-flash verification. Pass --pico-port to verify.")
    return 0

  ok = verify_post_flash(pico_for_verify)
  return 0 if ok else 2


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main():
  ap = argparse.ArgumentParser(
      description="Compile, flash, and verify the ESP32 harness sketch.",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  ap.add_argument("--language", default="cpp",
                  choices=sorted(_LANGS_FOR_ARDUINO | _LANGS_FOR_PIO),
                  help="Source language of the function-under-test "
                        "(determines which flashing flow to use; "
                        "default: cpp).")
  ap.add_argument("--esp-port", default=None,
                  help="Override ESP32 serial port (auto-detected by VID:PID).")
  ap.add_argument("--pico-port", default=None,
                  help="Override Pico serial port (auto-detected by VID:PID).")
  ap.add_argument("--fqbn", default=DEFAULT_FQBN,
                  help="ESP32 board FQBN for arduino-cli (default: %(default)s).")
  ap.add_argument("--sketch-dir", default=DEFAULT_SKETCH_DIR,
                  help="Path to the Arduino sketch (default: %(default)s).")
  ap.add_argument("--toolchain", default=None,
                  choices=["arduino-cli", "platformio"],
                  help="Force a specific toolchain (otherwise picked by --language).")
  ap.add_argument("--no-verify", action="store_true",
                  help="Skip the post-flash 'is harness alive?' check.")
  ap.add_argument("--via-pico", dest="via_pico", action="store_true",
                  default=None,
                  help="Force bridged flashing through the Pico (Route A). "
                        "Default: auto -- bridged when only the Pico is "
                        "connected, direct otherwise.")
  ap.add_argument("--no-via-pico", dest="via_pico", action="store_false",
                  help="Force direct flashing of an ESP32 USB port (refuse "
                        "to bridge through the Pico).")
  ap.add_argument("--bridge-seconds", type=int,
                  default=_BRIDGE_DEFAULT_SECONDS,
                  help="Hard-cap on how long the Pico stays in bridge mode "
                        "(default: %(default)s).")
  ap.add_argument("--status", action="store_true",
                  help="Just print toolchain + port detection results and exit.")
  args = ap.parse_args()

  if args.status:
    tc = detect_toolchains()
    print("Toolchains:")
    print(f"  arduino-cli  {'INSTALLED' if tc.arduino_cli else 'missing'}")
    print(f"  platformio   {'INSTALLED' if tc.platformio else 'missing'}")
    print()
    print("Serial ports:")
    ports = list_ports()
    if not ports:
      print("  (none visible -- pyserial not installed?)")
    for p in ports:
      print(f"  {p}")
    sys.exit(0)

  rc = flash_target(
      language=args.language,
      esp_port=args.esp_port,
      pico_port=args.pico_port,
      fqbn=args.fqbn,
      sketch_dir=args.sketch_dir,
      toolchain_override=args.toolchain,
      verify=not args.no_verify,
      via_pico=args.via_pico,
      bridge_seconds=args.bridge_seconds,
  )
  sys.exit(rc)


if __name__ == "__main__":
  main()