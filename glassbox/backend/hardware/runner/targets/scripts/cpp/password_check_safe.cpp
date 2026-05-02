// password_check_safe.cpp -- constant-time password verifier.
//
// Always processes `kPasswordLen` bytes regardless of input length, and
// folds the length mismatch into the same accumulator as the byte diffs
// so success/failure paths are indistinguishable.

#include "gb_target.h"
#include <string.h>

static const uint8_t kPassword[] = {'h', 'u', 'n', 't', 'e', 'r', '2'};
static const size_t  kPasswordLen = sizeof(kPassword);

extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                              uint8_t* out, size_t* out_len) {
  uint8_t diff = (uint8_t)(secret_len ^ kPasswordLen);
  for (size_t i = 0; i < kPasswordLen; ++i) {
    uint8_t s = (i < secret_len) ? secret[i] : 0;
    diff |= (uint8_t)(s ^ kPassword[i]);
  }
  out[0]   = (uint8_t)(diff == 0);
  *out_len = 1;
  return 0;
}

extern "C" const char* gb_target_name(void) { return "password_check_safe"; }
