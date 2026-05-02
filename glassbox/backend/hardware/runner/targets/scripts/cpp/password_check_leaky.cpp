// password_check_leaky.cpp -- auth-shaped wrapper around a leaky compare.
//
// Same early-return pattern as strcmp_leaky but framed as a password
// verifier, with a fast-path "wrong length" reject that itself leaks
// information about the expected password length.

#include "gb_target.h"
#include <string.h>

static const uint8_t kPassword[] = {'h', 'u', 'n', 't', 'e', 'r', '2'};
static const size_t  kPasswordLen = sizeof(kPassword);

extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                              uint8_t* out, size_t* out_len) {
  if (secret_len != kPasswordLen) {
    out[0]   = 0;
    *out_len = 1;
    return 0; // <-- length oracle
  }
  for (size_t i = 0; i < kPasswordLen; ++i) {
    if (secret[i] != kPassword[i]) {
      out[0]   = 0;
      *out_len = 1;
      return 0; // <-- prefix-length oracle
    }
  }
  out[0]   = 1;
  *out_len = 1;
  return 0;
}

extern "C" const char* gb_target_name(void) { return "password_check_leaky"; }
