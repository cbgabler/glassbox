// gb_target.h -- standardized interface for user-supplied functions under test.
//
// To test your own C++ function with GlassBox:
//   1. Edit gb_target.cpp in this same sketch folder.
//   2. Implement gb_target_call() and gb_target_name().
//   3. Re-flash this sketch to the ESP32.
//   4. Run `python eval.py --port <pico-port> --target-name <yourname>` on the laptop.
//
// The harness will dispatch RUN commands with fn_id=3 to your function and
// measure cycles + power exactly the way it measures the built-in primitives.
//
// Constraints (read me):
//   * Your function MUST be self-contained -- no Serial calls, no delay(),
//     no wifi, no flash writes. Anything that yields will corrupt the trace.
//   * Maximum input size: 64 bytes. Maximum output size: 64 bytes.
//   * The function must complete in < ~100 ms or the runner will time out.
//   * Whatever data you treat as the secret should come from `secret`/`secret_len`.
//     The runner will sweep that input under different distributions
//     (random vs zero, byte sweep, etc.) to expose data-dependent behavior.

#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Implement this in gb_target.cpp.
// Return 0 on success, non-zero on error.
int gb_target_call(const uint8_t* secret, size_t secret_len,
                   uint8_t* out, size_t* out_len);

// Implement this in gb_target.cpp. Return a short, descriptive name
// (printed at boot, used by the runner for log lines and parquet labels).
const char* gb_target_name(void);

#ifdef __cplusplus
}
#endif
