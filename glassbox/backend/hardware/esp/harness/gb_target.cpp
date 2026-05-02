// gb_target.cpp -- default no-op stub for the user-supplied function slot.
//
// *    Replace the body of gb_target_call() with the function you want GlassBox
// to side-channel-analyze, OR run:
//
//  *   python glassbox/runner/glassbox_check.py path/to/myfunc.cpp --install-target
//
// to drop a target file in here automatically. See targets/ for example
// implementations (strcmp_safe.cpp, branch_on_secret_leaky.cpp, ...).

#include "gb_target.h"

extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                              uint8_t* out, size_t* out_len) {
  // Default: copy input straight through. Replace with your function.
  size_t n = (secret_len > 64) ? 64 : secret_len;
  for (size_t i = 0; i < n; i++) {
    out[i] = secret[i];
  }
  *out_len = n;
  return 0;
}

extern "C" const char* gb_target_name(void) {
  return "user_target_default";
}
