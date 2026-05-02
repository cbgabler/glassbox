// lookup_table_safe.cpp -- scan-all constant-time table lookup.
//
// Visits every entry of the table on every call and uses a branchless
// mask to select the one matching the secret index. Runtime and access
// pattern are independent of the secret.

#include "gb_target.h"

static const uint8_t kSbox[256] = {
  0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
  0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
  // ... rest zero in stub.
};

static inline uint8_t ct_eq_u8(uint8_t a, uint8_t b) {
  // Returns 0xFF if a == b, else 0x00 -- no branches.
  uint8_t x = (uint8_t)(a ^ b);
  x = (uint8_t)(x | (uint8_t)(-x));
  return (uint8_t)((x >> 7) - 1);
}

extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                              uint8_t* out, size_t* out_len) {
  uint8_t idx = (secret_len > 0) ? secret[0] : 0;
  uint8_t acc = 0;
  for (int i = 0; i < 256; ++i) {
    acc |= (uint8_t)(kSbox[i] & ct_eq_u8((uint8_t)i, idx));
  }
  out[0]   = acc;
  *out_len = 1;
  return 0;
}

extern "C" const char* gb_target_name(void) { return "lookup_table_safe"; }
