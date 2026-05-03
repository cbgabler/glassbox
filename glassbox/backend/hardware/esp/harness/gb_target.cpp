// strcmp_safe.cpp -- constant-time equality.
//
// XOR every byte of `secret` against the reference and OR the differences
// into a single accumulator. No early exit, no secret-dependent branches.

#include "gb_target.h"
#include <string.h>

static const uint8_t kReference[] = {'g', 'l', 'a', 's', 's', 'b', 'o', 'x'};
static const size_t  kReferenceLen = sizeof(kReference);

extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                              uint8_t* out, size_t* out_len) {
  uint8_t diff = (uint8_t)(secret_len ^ kReferenceLen);
  size_t  n    = secret_len < kReferenceLen ? secret_len : kReferenceLen;
  for (size_t i = 0; i < kReferenceLen; ++i) {
    uint8_t s = (i < n) ? secret[i] : 0;
    diff |= (uint8_t)(s ^ kReference[i]);
  }
  out[0]   = (uint8_t)(diff == 0);
  *out_len = 1;
  return 0;
}

extern "C" const char* gb_target_name(void) { return "strcmp_safe"; }
