// lookup_table_leaky.cpp -- secret-indexed table access.
//
// AES-style S-box lookup driven by the secret byte. Even on a flat
// register file this leaks via data-dependent memory access patterns
// (cache lines on bigger MCUs, bus activity / EM elsewhere).

#include "gb_target.h"

static const uint8_t kSbox[256] = {
  0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
  0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
  // ... rest left as zero for the stub; fill in real S-box for production runs.
};

extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                              uint8_t* out, size_t* out_len) {
  uint8_t b = (secret_len > 0) ? secret[0] : 0;
  out[0]    = kSbox[b]; // <-- secret-indexed access
  *out_len  = 1;
  return 0;
}

extern "C" const char* gb_target_name(void) { return "lookup_table_leaky"; }
