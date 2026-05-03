"""compile_target.py -- prepare an arbitrary compiled-language file for the
ESP32 hardware harness.

The ESP32's harness expects a `gb_target.cpp` that exposes two C-ABI symbols:

    int  gb_target_call(const uint8_t* secret, size_t secret_len,
                        uint8_t* out, size_t* out_len);
    const char* gb_target_name(void);

This module accepts source files in *any compiled language we support* and
either drops them in directly (for C/C++) or generates the FFI shim and the
exact toolchain commands the user needs (for Rust / Zig / asm). After this
script runs, the user can re-flash the harness sketch and the hardware tests
the new function exactly the same way it tests the built-in primitives.

Languages supported today:

  C / C++       Drop-in replacement for esp/harness/gb_target.cpp.
  Asm (.S/.s)   Wrap in a tiny C++ shim that calls into the asm symbol.
  Rust (.rs)    Generate Cargo project skeleton + a C++ shim that calls
                into it via `extern "C"`. Requires PlatformIO to actually
                build (Arduino IDE can't link external static libs).
  Zig (.zig)    Same idea: generate the C++ shim + a `build.zig` target.
                Requires PlatformIO + a manual build step.

For Rust and Zig we DO NOT try to invoke the Rust/Zig toolchain ourselves --
we generate the project, print the exact `cargo` / `zig` commands the user
needs to run, and tell them where to wire the resulting static lib into
platformio.ini. This is intentional: handling toolchain installation /
sysroot / target triple management for every host OS is out of scope, and
silently failing in subtle ways helps no one.

Usage:

    # C/C++ (the easy path -- already worked before this script existed)
    python compile_target.py path/to/myfunc.cpp

    # Rust:
    python compile_target.py path/to/myfunc.rs --name my_target
    # ... follow the printed instructions ...

    # Just lint, don't install:
    python compile_target.py path/to/myfunc.cpp --check

The default target install path is glassbox/esp/harness/gb_target.cpp, which
is the slot the harness sketch's fn_id=3 (user_target) dispatches to.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import textwrap
from typing import List, Optional


# -- Path conventions ----------------------------------------------------------

# Where the harness sketch expects its user-target source.
DEFAULT_HARNESS_TARGET = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "esp", "harness", "gb_target.cpp")
)

# Where we drop generated FFI projects (Rust/Zig).
DEFAULT_FFI_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "esp", "harness", "user_target_ffi")
)


# =============================================================================
# Language detection
# =============================================================================

LANG_BY_EXT = {
    ".c":   "c",
    ".h":   "c",
    ".cpp": "cpp",
    ".cc":  "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".rs":  "rust",
    ".zig": "zig",
    ".s":   "asm",
    ".S":   "asm",
}


def detect_language(path: str) -> str:
    ext = os.path.splitext(path)[1]
    lang = LANG_BY_EXT.get(ext)
    if lang is None:
        raise SystemExit(
            f"compile_target: unrecognized extension {ext!r}. "
            f"Supported: {', '.join(sorted(LANG_BY_EXT))}"
        )
    return lang


# =============================================================================
# C / C++ ABI sanity check
# =============================================================================

_REQUIRED_C_SYMS = ("gb_target_call", "gb_target_name")

def check_c_abi(src: str) -> List[str]:
    """Return a list of human-readable problems with a C/C++ source.

    We check (a) the two required symbols are defined, (b) `extern "C"`
    appears (so the harness can see them by un-mangled name).
    """
    problems: List[str] = []
    for sym in _REQUIRED_C_SYMS:
        if not re.search(rf"\b{sym}\b\s*\(", src):
            problems.append(f"missing required symbol `{sym}` (function definition not found)")
    # Allow plain C files (extern "C" not legal there); only complain on .cpp.
    if "extern \"C\"" not in src and "extern\"C\"" not in src:
        problems.append(
            "no `extern \"C\"` block found -- the harness links by C name, "
            "so symbols MUST be unmangled. Wrap the two functions in "
            "`extern \"C\" { ... }` or annotate each with `extern \"C\"`."
        )
    return problems


# =============================================================================
# Per-language installers
# =============================================================================

def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def install_c_or_cpp(src_path: str, dst_path: str, *, force: bool) -> int:
    """Drop-in replacement for gb_target.cpp."""
    src = _read(src_path)
    problems = check_c_abi(src)
    if problems:
        print("compile_target: C/C++ ABI check FAILED:")
        for p in problems:
            print(f"  - {p}")
        print("\nFix the source so it exposes both symbols with C linkage, then re-run.")
        return 1
    if os.path.abspath(src_path) == os.path.abspath(dst_path):
        print(f"compile_target: src and dst are the same file ({dst_path}); nothing to do.")
        return 0
    if os.path.exists(dst_path) and not force:
        # Compare; only nag if they actually differ.
        if _read(dst_path) == src:
            print(f"compile_target: {dst_path} already matches {src_path}; nothing to do.")
            return 0
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    shutil.copyfile(src_path, dst_path)
    print(f"compile_target: installed {src_path} -> {dst_path}")
    print("Next steps:")
    print(f"  1. Open {os.path.relpath(os.path.dirname(dst_path))}/harness.ino in Arduino IDE.")
    print(f"  2. Flash to the ESP32.")
    print(f"  3. Run sweep_target.py / eval.py against the new target.")
    return 0


def install_asm(src_path: str, dst_path: str, *, name: str, force: bool) -> int:
    """Wrap a .s/.S file in a tiny C++ shim that calls into it.

    The user's asm file must define a callable with the same signature as
    `gb_target_call` and a name we can reference. We write `gb_target.cpp`
    with `extern "C"` declarations + a `gb_target_name()`, plus copy the
    asm source next to it as `gb_target_asm.S` so the Arduino sketch picks
    it up automatically (Arduino includes all .S files in the sketch dir).
    """
    src_text = _read(src_path)
    sym = name
    if not re.search(rf"\b{sym}\b", src_text):
        print(f"compile_target: WARNING: symbol `{sym}` not found in {src_path}. "
              f"Pass --name to specify the exported function name.")
    asm_dst = os.path.join(os.path.dirname(dst_path), "gb_target_asm.S")
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    if os.path.exists(asm_dst) and not force and _read(asm_dst) == src_text:
        pass
    else:
        shutil.copyfile(src_path, asm_dst)

    shim = textwrap.dedent(f"""\
        // gb_target.cpp -- AUTO-GENERATED by compile_target.py for an asm target.
        // The actual implementation lives in gb_target_asm.S; we just declare
        // and forward to it. Do NOT hand-edit -- regenerate via compile_target.py.

        #include "gb_target.h"

        extern "C" int {sym}(const uint8_t* secret, size_t secret_len,
                             uint8_t* out, size_t* out_len);

        extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                                      uint8_t* out, size_t* out_len) {{
            return {sym}(secret, secret_len, out, out_len);
        }}

        extern "C" const char* gb_target_name(void) {{
            return "{sym}";
        }}
    """)
    with open(dst_path, "w") as f:
        f.write(shim)
    print(f"compile_target: installed asm + C++ shim:")
    print(f"  {asm_dst}  (your asm)")
    print(f"  {dst_path}  (generated forwarding shim)")
    print("Next steps:")
    print(f"  1. Make sure your asm exports `{sym}` with the standard ABI.")
    print(f"  2. Open the harness sketch in Arduino IDE; it will auto-include the .S.")
    print(f"  3. Flash, then test as usual.")
    return 0


def install_rust(src_path: str, dst_path: str, *, name: str, force: bool) -> int:
    """Generate a Cargo project + a C++ shim that calls into it.

    We do NOT cargo-build for the user -- that requires the `esp-rs` toolchain
    (`rustup target add xtensa-esp32-none-elf`, plus `espup`). We generate the
    project tree, print the exact build steps, and stop.
    """
    rust_proj = DEFAULT_FFI_DIR + "_rust"
    os.makedirs(os.path.join(rust_proj, "src"), exist_ok=True)

    cargo_toml = textwrap.dedent(f"""\
        # AUTO-GENERATED by compile_target.py
        # Build the static library that links into the harness:
        #   rustup target add xtensa-esp32-none-elf
        #   cargo +esp build --release --target xtensa-esp32-none-elf
        # Then point platformio.ini at:
        #   target/xtensa-esp32-none-elf/release/libgb_target_user.a

        [package]
        name = "gb_target_user"
        version = "0.1.0"
        edition = "2021"

        [lib]
        name = "gb_target_user"
        crate-type = ["staticlib"]

        [dependencies]
        # Add yours here. The default has no deps.
    """)
    with open(os.path.join(rust_proj, "Cargo.toml"), "w") as f:
        f.write(cargo_toml)

    user_src = _read(src_path)
    has_extern = "extern \"C\"" in user_src
    if not has_extern:
        print("compile_target: WARNING: your Rust source does not contain "
              "`extern \"C\"` -- the C++ shim will not be able to find the symbol. "
              "Wrap your function with `#[no_mangle] pub extern \"C\" fn ...`.")

    rs_dst = os.path.join(rust_proj, "src", "lib.rs")
    if os.path.exists(rs_dst) and not force and _read(rs_dst) == user_src:
        pass
    else:
        shutil.copyfile(src_path, rs_dst)

    shim = textwrap.dedent(f"""\
        // gb_target.cpp -- AUTO-GENERATED by compile_target.py for a Rust target.
        // The actual implementation is in {os.path.relpath(rs_dst, os.path.dirname(dst_path))}.
        // To build, see the printed instructions; then link the resulting
        // libgb_target_user.a into the firmware via platformio.ini's
        // build_flags (cannot be done from stock Arduino IDE).

        #include "gb_target.h"

        extern "C" int {name}(const uint8_t* secret, size_t secret_len,
                              uint8_t* out, size_t* out_len);

        extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                                      uint8_t* out, size_t* out_len) {{
            return {name}(secret, secret_len, out, out_len);
        }}

        extern "C" const char* gb_target_name(void) {{
            return "{name}";
        }}
    """)
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    with open(dst_path, "w") as f:
        f.write(shim)

    print(f"compile_target: generated Rust FFI scaffolding:")
    print(f"  {rust_proj}/Cargo.toml")
    print(f"  {rust_proj}/src/lib.rs   (your source)")
    print(f"  {dst_path}                (generated C++ shim)")
    print()
    print("Build steps (one-time toolchain setup, then per change):")
    print("  # 1. Install the Espressif Rust toolchain (one-time):")
    print("  cargo install espup && espup install")
    print("  # 2. Build the static library:")
    print(f"  cd {rust_proj} && cargo +esp build --release --target xtensa-esp32-none-elf")
    print("  # 3. Move to PlatformIO (Arduino IDE cannot link external .a files):")
    print("     Add to platformio.ini under [env:esp32]:")
    print("       build_flags = -L${PROJECT_DIR}/path/to/target/xtensa-esp32-none-elf/release")
    print("       build_flags = -lgb_target_user")
    print("  # 4. pio run -t upload")
    return 0


def install_zig(src_path: str, dst_path: str, *, name: str, force: bool) -> int:
    """Generate the C++ shim + a build.zig that produces a static library."""
    zig_proj = DEFAULT_FFI_DIR + "_zig"
    os.makedirs(zig_proj, exist_ok=True)
    user_src = _read(src_path)
    if "export fn" not in user_src and "extern \"C\"" not in user_src:
        print("compile_target: WARNING: your Zig source does not appear to "
              "export a C-ABI symbol. Use `export fn ...` so the shim can link.")

    zig_dst = os.path.join(zig_proj, "src.zig")
    if os.path.exists(zig_dst) and not force and _read(zig_dst) == user_src:
        pass
    else:
        shutil.copyfile(src_path, zig_dst)

    build_zig = textwrap.dedent("""\
        // AUTO-GENERATED by compile_target.py
        // Build with: zig build -Dtarget=xtensa-freestanding-none -Drelease-fast
        const std = @import("std");
        pub fn build(b: *std.Build) void {
            const target = b.standardTargetOptions(.{});
            const optimize = b.standardOptimizeOption(.{});
            const lib = b.addStaticLibrary(.{
                .name = "gb_target_user",
                .root_source_file = .{ .path = "src.zig" },
                .target = target,
                .optimize = optimize,
            });
            b.installArtifact(lib);
        }
    """)
    with open(os.path.join(zig_proj, "build.zig"), "w") as f:
        f.write(build_zig)

    shim = textwrap.dedent(f"""\
        // gb_target.cpp -- AUTO-GENERATED by compile_target.py for a Zig target.
        #include "gb_target.h"

        extern "C" int {name}(const uint8_t* secret, size_t secret_len,
                              uint8_t* out, size_t* out_len);

        extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                                      uint8_t* out, size_t* out_len) {{
            return {name}(secret, secret_len, out, out_len);
        }}

        extern "C" const char* gb_target_name(void) {{
            return "{name}";
        }}
    """)
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    with open(dst_path, "w") as f:
        f.write(shim)

    print(f"compile_target: generated Zig FFI scaffolding:")
    print(f"  {zig_proj}/build.zig")
    print(f"  {zig_proj}/src.zig         (your source)")
    print(f"  {dst_path}                  (generated C++ shim)")
    print()
    print("Build steps:")
    print(f"  cd {zig_proj} && zig build -Dtarget=xtensa-freestanding-none -Doptimize=ReleaseFast")
    print("  Add the resulting libgb_target_user.a to platformio.ini's build_flags.")
    return 0


# =============================================================================
# CLI
# =============================================================================

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("source", help="Source file: .c / .cpp / .rs / .zig / .s")
    ap.add_argument("--target", default=DEFAULT_HARNESS_TARGET,
                    help="Where to write the harness's gb_target.cpp "
                         "(default: %(default)s)")
    ap.add_argument("--name", default="my_target",
                    help="Exported symbol name (rust/zig/asm); also used as "
                         "the gb_target_name() return value.")
    ap.add_argument("-f", "--force", action="store_true",
                    help="Overwrite the target file even if it differs from source.")
    ap.add_argument("--check", action="store_true",
                    help="Only lint the source for ABI compliance; do not install.")
    args = ap.parse_args()

    if not os.path.isfile(args.source):
        print(f"compile_target: {args.source}: not a file", file=sys.stderr)
        sys.exit(2)

    lang = detect_language(args.source)
    print(f"compile_target: detected language = {lang}")

    if args.check:
        if lang in ("c", "cpp"):
            problems = check_c_abi(_read(args.source))
            if problems:
                print("ABI check FAILED:")
                for p in problems:
                    print(f"  - {p}")
                sys.exit(1)
            print("ABI check OK.")
        else:
            print("--check only meaningful for C/C++.")
        sys.exit(0)

    if lang in ("c", "cpp"):
        sys.exit(install_c_or_cpp(args.source, args.target, force=args.force))
    if lang == "asm":
        sys.exit(install_asm(args.source, args.target,
                             name=args.name, force=args.force))
    if lang == "rust":
        sys.exit(install_rust(args.source, args.target,
                              name=args.name, force=args.force))
    if lang == "zig":
        sys.exit(install_zig(args.source, args.target,
                             name=args.name, force=args.force))
    print(f"compile_target: language {lang} is unhandled (bug)")
    sys.exit(2)


if __name__ == "__main__":
    main()
