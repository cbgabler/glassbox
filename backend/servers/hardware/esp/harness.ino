// ESP32 victim harness for GlassBox.
//
// Protocol over UART2 (Pico <-> ESP32):
//   Pico  -> ESP32:  "RUN <fn_id> <hex_input>\n"
//   ESP32 -> Pico:   "RES2 <cycles> <micros> <insns> <branches> <hex_output>\n"
//
// The RES2 protocol replaces the older RES line and ships four timing/HPC
// channels per call instead of one:
//   cycles    Xtensa CCOUNT delta (fine-grained, deterministic)
//   micros    esp_timer_get_time() delta (catches interrupt / system noise)
//   insns     instructions retired delta from PMU counter PM0 (best-effort)
//   branches  branches taken delta from PMU counter PM1 (best-effort)
//
// PMU values may read 0 if the LX6 event codes below don't match this exact
// silicon revision; the runner treats a flat-line PMU channel as "no signal"
// and TVLA on it correctly reports no leak. Cycles + micros always work.
//
// Trigger pin GPIO5 is driven HIGH for the duration of the function under test
// so the Pico can sample the INA169 ADC over exactly that window.
//
// Wiring (per glassbox/README.md, with trigger direction reversed for sampling):
//   ESP32 GPIO16 (RX2) <- Pico GP0 (TX)
//   ESP32 GPIO17 (TX2) -> Pico GP1 (RX)
//   ESP32 GPIO5        -> Pico GP2  (trigger OUTPUT, ESP32 drives)
//   ESP32 GND          <-> Pico GND

#include <Arduino.h>
#include <Preferences.h>

static const int PIN_TRIGGER_OUT=5;

// State preserve in NVS (flash-backed key/val) under"gb"
static Preferences nvs;
static bool g_quarantined = false;

// 8-byte secret used by the strcmp primitives.
static const char    SECRET[]    = "hunter2!";
static const uint8_t SECRET_LEN  = 8;


// =============================================================================
default functions
// =============================================================================

// fn_id = 0: noop (calibration baseline)
int fn_noop(const uint8_t* in, size_t n, uint8_t* out, size_t* out_n) {
    (void)in; (void)n; (void)out;
    *out_n = 0;
    return 0;
  }
  
  // fn_id = 1: naive strcmp -- early-return on first mismatched byte (TIMING LEAK).
  int fn_strcmp_naive(const uint8_t* in, size_t n, uint8_t* out, size_t* out_n) {
    *out_n = 1;
    for (uint8_t i = 0; i < SECRET_LEN; i++) {
      if (i >= n || in[i] != (uint8_t)SECRET[i]) {
        out[0] = 0;
        return 0;
      }
    }
    out[0] = 1;
    return 1;
  }
  
  // fn_id = 2: constant-time compare -- always touches every byte (SAFE).
  int fn_strcmp_safe(const uint8_t* in, size_t n, uint8_t* out, size_t* out_n) {
    uint8_t diff = 0;
    for (uint8_t i = 0; i < SECRET_LEN; i++) {
      uint8_t a = (i < n) ? in[i] : 0;
      diff |= (a ^ (uint8_t)SECRET[i]);
    }
    out[0] = (diff == 0) ? 1 : 0;
    *out_n = 1;
    return out[0];
  }
  
  // fn_id = 3: user-supplied target. Forwards to gb_target_call() defined in
  // gb_target.cpp (which is just a sibling source file in this sketch folder).
  // The default stub is a no-op so the firmware always builds; users replace
  // gb_target.cpp with their own function-under-test before flashing.
  int fn_user_target(const uint8_t* in, size_t n, uint8_t* out, size_t* out_n) {
    return gb_target_call(in, n, out, out_n);
  }
  
  typedef int (*fn_t)(const uint8_t*, size_t, uint8_t*, size_t*);
  static fn_t       FUNCTIONS[]  = { fn_noop, fn_strcmp_naive, fn_strcmp_safe, fn_user_target };
  static const int  N_FUNCTIONS  = sizeof(FUNCTIONS) / sizeof(FUNCTIONS[0]);
  static const char* FN_NAMES[]  = { "noop", "strcmp_naive", "strcmp_safe", "user_target" };

// =============================================================================
// Cycle counter (Xtensa CCOUNT register) + Hardware Performance Counters (PMU)
// =============================================================================

// We program PM0 = INSN_RETIRED, PM1 = BRANCH_TAKEN at boot. PM2/PM3 are left
// idle but easy to add more events to.

// Event encoding for PMCTRLn (Xtensa LX6 PMU spec):
//   bit  [0]    : counter enable
//   bits [3:1]  : interrupt-level mask (0x7 = count at all levels)
//   bits [7:4]  : kernel/user mask (0x3 = count in both modes)
//   bits [23:16]: event select (TRACELEVEL)
