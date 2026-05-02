// branch_on_secret_leaky.cpp -- explicit secret-dependent control flow.
//
// Picks one of two arithmetic paths based on the LSB of the secret. The
// two paths take measurably different cycle counts and draw a different
// power signature -- the canonical "if (secret) { ... }" leak.

#include "gb_target.h"

extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                              uint8_t* out, size_t* out_len) {
  uint8_t  s   = (secret_len > 0) ? secret[0] : 0;
  uint32_t acc = 0;
  if (s & 1) {
    for (int i = 0; i < 1024; ++i) acc = (acc * 1103515245u + 12345u);
  } else {
    for (int i = 0; i < 64;   ++i) acc = (acc + i) ^ 0xdeadbeefu;
  }
  out[0]   = (uint8_t)(acc & 0xff);
  *out_len = 1;
  return 0;
}

extern "C" const char* gb_target_name(void) { return "branch_on_secret_leaky"; }
