// This default implementation just copies the secret to the output and
// returns 0. It is not leaky, not interesting, and not a useful test --
// it exists so the harness firmware always builds even when the user
// hasn't dropped in a real target yet.
//
// To test your own function:
//   * Either overwrite this file directly,
//   * Or copy one of glassbox/runner/targets/*.cpp on top of it.
//
// Then re-flash the harness sketch.

#include "gb_target.h"
#include <string.h>

extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                              uint8_t* out, size_t* out_len) {
  size_t n = secret_len < 64 ? secret_len : 64;
  memcpy(out, secret, n);
  *out_len = n;
  return 0;
}

extern "C" const char* gb_target_name(void) {
  return "default_stub";
}
