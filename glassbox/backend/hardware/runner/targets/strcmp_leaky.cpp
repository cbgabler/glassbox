// strcmp_leaky.cpp -- classic early-return byte compare.
//
// Treats `secret` as the candidate string and compares it against a fixed
// reference. Returns on the first mismatching byte, so total runtime
// scales with the length of the matching prefix -- a textbook timing leak.

#include "gb_target.h"
#include <string.h>

static const uint8_t kReference[] = {'g', 'l', 'a', 's', 's', 'b', 'o', 'x'};
static const size_t  kReferenceLen = sizeof(kReference);

extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                              uint8_t* out, size_t* out_len) {
  uint8_t match = 1;
  size_t  n     = secret_len < kReferenceLen ? secret_len : kReferenceLen;
  for (size_t i = 0; i < n; ++i) {
    if (secret[i] != kReference[i]) {
      match = 0;
      break; // <-- the leak
    }
  }
  out[0]   = match;
  *out_len = 1;
  return 0;
}

extern "C" const char* gb_target_name(void) { return "strcmp_leaky"; }
