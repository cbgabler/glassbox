// demo_leaky_vault.cpp -- intentionally broken "password vault" for demo.
//
// This file is a showcase of every side-channel anti-pattern ct_lint catches.
// Every rule fires at least once:
//
//   CT001  branch on secret       -- early-exit loop + PIN comparison branch
//   CT002  variable-time compare  -- memcmp + strcmp on the secret buffer
//   CT003  secret-indexed table   -- AES-style S-box lookup via secret byte
//   CT004  variable-time arith    -- secret % N, secret / N, 1u << secret_bit
//   CT006  print/log secret       -- Serial.println(secret[i]) debug line
//   CT007  yield inside timed fn  -- delay() call inside gb_target_call
//
// DO NOT ship code like this. This exists purely so the demo shows a wall of
// findings and the hardware TVLA can confirm multiple leak shapes.

#include <Arduino.h>
#include "gb_target.h"
#include <string.h>

// "glassbox" in ASCII -- the reference the vault checks the PIN against.
static const uint8_t kVaultPin[]  = {'g','l','a','s','s','b','o','x'};
static const size_t  kVaultPinLen = sizeof(kVaultPin);

// Fake AES S-box (first 16 bytes only -- enough to demonstrate CT003).
static const uint8_t kSbox[256] = {
  0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,
  0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
  // ... rest zeroed
};

extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                              uint8_t* out, size_t* out_len) {

  // -------------------------------------------------------------------------
  // CT001 + CT002: early-exit PIN check (the classic timing leak).
  // Loop bails on first mismatching byte; then calls memcmp for good measure.
  // -------------------------------------------------------------------------
  size_t n = secret_len < kVaultPinLen ? secret_len : kVaultPinLen;
  for (size_t i = 0; i < n; ++i) {
    if (secret[i] != kVaultPin[i]) {   // CT001: branch on secret
      out[0] = 0;
      *out_len = 1;
      return 1;
    }
  }

  // CT002: call memcmp on the secret as a redundant "double-check".
  if (memcmp(secret, kVaultPin, n) != 0) {  // CT002: variable-time comparator
    out[0] = 0;
    *out_len = 1;
    return 1;
  }

  // CT002 again: strcmp variant just to get two CT002 hits.
  if (strcmp((const char*)secret, (const char*)kVaultPin) != 0) {  // CT002
    out[0] = 0;
    *out_len = 1;
    return 1;
  }

  // -------------------------------------------------------------------------
  // CT003: AES-style S-box lookup indexed by a secret byte.
  // Cache lines depend on which byte of the secret is used as the index,
  // so power and timing both leak the secret value.
  // -------------------------------------------------------------------------
  uint8_t sbox_out = kSbox[secret[0]];       // CT003: table indexed by secret

  // -------------------------------------------------------------------------
  // CT004: variable-time arithmetic on the secret.
  // Division and modulo are variable-cycle on most MCUs; shift by secret_bit
  // leaks the bit count.
  // -------------------------------------------------------------------------
  uint8_t secret_bit  = secret[1] & 0x07;
  uint8_t bucket      = secret[2] % 16;      // CT004: secret % N
  uint8_t slot        = secret[3] / 4;       // CT004: secret / N
  uint32_t mask       = 1u << secret_bit;    // CT004: shift by secret

  // -------------------------------------------------------------------------
  // CT006: debug print that leaks the secret value over serial.
  // -------------------------------------------------------------------------
  Serial.println(secret[0]);                 // CT006: print secret byte

  // -------------------------------------------------------------------------
  // CT007: delay() inside the timed measurement window.
  // Any yield corrupts timing; this also makes the function obviously slow.
  // -------------------------------------------------------------------------
  delay(1);                                  // CT007: yield inside timed fn

  // Pack outputs so the harness gets something plausible back.
  out[0] = sbox_out ^ bucket ^ slot ^ (uint8_t)mask;
  *out_len = 1;
  return 0;
}

extern "C" const char* gb_target_name(void) { return "demo_leaky_vault"; }
