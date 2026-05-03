"""smoketest_probe.py -- prove the freshly-flashed user target is OURS.

Pairs with `targets/scripts/cpp/smoketest_fingerprint.cpp`. Sends a few
`RUN 3 <hex>` commands to the Pico, parses the `RES2 ... <hex_output>`
reply, and asserts the output matches the fingerprint:

    DEADBEEF  <input echo, 4 bytes>  47425321  ("GBS!")

If you see PASS, the entire chain is verified end-to-end:
  host CDC  ->  Pico parser  ->  UART0  ->  ESP32 firmware  ->
  fn_user_target dispatch  ->  gb_target_call (smoketest_fingerprint)
  ->  RES2 reply  ->  Pico forward  ->  host.
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

import serial  # type: ignore


# Test cases. Each entry is (input_hex, expected_output_hex).
# Expected output = DEADBEEF + (len, b0, b1, b2 padded with 0x00) + 47425321
CASES = [
  ("",           "deadbeef0000000047425321"),
  ("00",         "deadbeef0100000047425321"),
  ("11223344",   "deadbeef0411223347425321"),
  ("aabbccddee", "deadbeef05aabbcc47425321"),
]


def _send_run(ser: serial.Serial, hex_input: str, timeout_s: float = 4.0
              ) -> Optional[str]:
  """Send `RUN 3 <hex>` and return the hex_output field of the RES2 reply.

  Returns None on timeout or malformed reply.
  """
  ser.reset_input_buffer()
  ser.write(f"RUN 3 {hex_input}\n".encode("ascii"))
  ser.flush()
  deadline = time.monotonic() + timeout_s
  buf = b""
  while time.monotonic() < deadline:
    chunk = ser.read(256)
    if not chunk:
      continue
    buf += chunk
    while b"\n" in buf:
      line, _, buf = buf.partition(b"\n")
      text = line.decode("ascii", "replace").strip()
      if not text:
        continue
      print(f"    pico: {text}")
      if text.startswith("RES2 "):
        # Format: RES2 <cycles> <us> <insns> <branches> <hex_output>
        parts = text.split()
        if len(parts) >= 6:
          return parts[-1].lower()
        return None
      if text.startswith("ERR"):
        return None
  return None


def main():
  ap = argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("--pico-port", default="/dev/cu.usbmodem1101",
                  help="Pico CDC serial port (default: %(default)s).")
  ap.add_argument("--baud", type=int, default=115200)
  args = ap.parse_args()

  print(f"[smoketest] opening {args.pico_port}")
  try:
    ser = serial.Serial(args.pico_port, baudrate=args.baud, timeout=0.25)
  except Exception as e:
    print(f"[smoketest] FAIL: cannot open {args.pico_port}: {e}")
    sys.exit(2)

  passed = 0
  failed = 0
  try:
    # Settle after open() toggled DTR; drain any boot/READY line.
    time.sleep(0.4)
    try:
      ser.reset_input_buffer()
    except Exception:
      pass

    for hex_in, expected in CASES:
      print(f"\n[smoketest] RUN 3 {hex_in!r:<14}  expect={expected}")
      got = _send_run(ser, hex_in)
      if got is None:
        print(f"[smoketest]   FAIL -- no RES2 reply")
        failed += 1
        continue
      if got == expected:
        print(f"[smoketest]   PASS -- got {got}")
        passed += 1
      else:
        print(f"[smoketest]   FAIL -- got {got}")
        failed += 1
  finally:
    try:
      ser.close()
    except Exception:
      pass

  total = passed + failed
  print(f"\n[smoketest] result: {passed}/{total} cases PASSED")
  if failed:
    print("[smoketest] something is off:")
    print("  - if every case returned None: the harness isn't responding to RUN")
    print("    (maybe the target slot is quarantined -- send 'UNQUARANTINE' to clear)")
    print("  - if the magic prefix/trailer is wrong: stale firmware on the ESP")
    print("    (re-run auto_flash.py to push smoketest_fingerprint.cpp)")
    print("  - if the magic is right but the input echo is wrong: dispatch routing")
    print("    bug in fn_user_target")
    sys.exit(1)

  print("[smoketest] full chain verified: host -> Pico -> UART0 -> ESP -> gb_target_call")
  sys.exit(0)


if __name__ == "__main__":
  main()
