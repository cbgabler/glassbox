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
_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SKETCH_DIR = os.path.normpath(os.path.join(_HERE, "..", "esp", "harness"))

# Default board for the ESP32 victim
DEFAULT_FQBN = "esp32:esp32:esp32"

DEFAULT_PIO_PROJECT_DIR = DEFAULT_SKETCH_DIR