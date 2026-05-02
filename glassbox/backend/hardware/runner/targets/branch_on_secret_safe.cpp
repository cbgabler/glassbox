// branch_on_secret_safe.cpp -- branchless conditional select.
//
// Computes BOTH paths every time and picks the answer with an arithmetic
// mask derived from the secret bit. Cycle count and power profile are
// identical regardless of the secret.

#include "gb_target.h"

extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                              uint8_t* out, size_t* out_len) {
  uint8_t  s    = (secret_len > 0) ? secret[0] : 0;
  uint32_t a    = 0;
  uint32_t b    = 0;
  for (int i = 0; i < 1024; ++i) {
    a = (a * 1103515245u + 12345u);
    b = (b + (uint32_t)i) ^ 0xdeadbeefu;
  }
  uint32_t mask = (uint32_t)0 - (uint32_t)(s & 1); // 0xFFFFFFFF if bit set, else 0
  uint32_t acc  = (a & mask) | (b & ~mask);
  out[0]   = (uint8_t)(acc & 0xff);
  *out_len = 1;
  return 0;
}

extern "C" const char* gb_target_name(void) { return "branch_on_secret_safe"; }
