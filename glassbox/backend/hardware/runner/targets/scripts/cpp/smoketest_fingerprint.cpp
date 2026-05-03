// smoketest_fingerprint.cpp -- end-to-end "is the right binary running?" test.
//
// Drop this on top of esp/harness/gb_target.cpp, reflash, then send
// `RUN 3 <hex_input>` via the Pico. The reply's RES2 hex_output should be:
//
//   DE AD BE EF  XX XX XX XX  47 42 53 21
//   `--magic--`  `-input echo-` `-"GBS!"-`
//
// Where the input echo is:
//   byte[4] = secret_len & 0xFF
//   byte[5] = secret[0]   if secret_len >= 1, else 0x00
//   byte[6] = secret[1]   if secret_len >= 2, else 0x00
//   byte[7] = secret[2]   if secret_len >= 3, else 0x00
//
// So `RUN 3 00`        --> RES2 ... deadbeef0000000047425321
//    `RUN 3 11223344`  --> RES2 ... deadbeef0411223347425321
//
// Anything else means either the flash didn't take (stale binary) or the
// runner reached a different fn_id slot than expected.
//
// This is intentionally NOT a side-channel test target -- runtime is constant
// and there are no secret-dependent branches. It exists purely to validate
// the flash + dispatch path.

#include "gb_target.h"

extern "C" int gb_target_call(const uint8_t* secret, size_t secret_len,
                              uint8_t* out, size_t* out_len) {
  // Magic prefix.
  out[0] = 0xDE;
  out[1] = 0xAD;
  out[2] = 0xBE;
  out[3] = 0xEF;

  // Input echo (proves we actually saw the bytes the runner sent).
  out[4] = (uint8_t)(secret_len & 0xFF);
  out[5] = (secret_len >= 1) ? secret[0] : 0x00;
  out[6] = (secret_len >= 2) ? secret[1] : 0x00;
  out[7] = (secret_len >= 3) ? secret[2] : 0x00;

  // ASCII trailer "GBS!" -- visually distinct in RES2 hex output.
  out[8]  = 'G';
  out[9]  = 'B';
  out[10] = 'S';
  out[11] = '!';

  *out_len = 12;
  return 0;
}

extern "C" const char* gb_target_name(void) {
  return "smoketest_fingerprint_v1";
}
