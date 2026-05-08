// ESP32 victim harness for GlassBox.
//
// Protocol over UART0 (Pico <-> ESP32, GPIO1/3):
//   Pico  -> ESP32:  "RUN <fn_id> <hex_input>\n"
//   ESP32 -> Pico:   "RES2 <cycles> <micros> <insns> <branches> <hex_output>\n"
//                    "ERR  <reason>\n"                    (bad command / hex)
//                    "MEMVIOL <kind> overrun=<n>\n"        (v2 memory-safety guard tripped)
//                    "PANIC <pc> <reason>\n"               (v2 shutdown handler best-effort)
//
// IMPORTANT (v3 -- single-USB flashing via the Pico):
//   The protocol now runs on UART0 (the same UART the ROM bootloader uses) so
//   the Pico can FLASH this firmware over the existing wires when only its
//   own USB cable is plugged in. The Pico harness exposes a "BRIDGE" mode
//   that transparently forwards bytes between its USB CDC and UART0, plus
//   drives EN / GPIO0 from the host's DTR / RTS line state -- exactly what
//   esptool expects from a CP2102-style USB-to-serial chip.
//
//   Consequence: GPIO1/3 are also tapped by the dev-board's onboard USB-to-
//   serial chip. DO NOT plug the ESP32's own USB cable in WHILE the Pico is
//   driving the same UART -- they will fight on the TX line. Use one OR the
//   other:
//     Pico USB connected   -> GlassBox harness + flash (this file)
//     ESP32 USB connected  -> direct serial-monitor debug, no Pico
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
// Wiring (v3 -- updated for Route A):
//   ESP32 GPIO3  (U0RXD)  <- Pico GP0 (TX)        ◀── moved from GPIO16
//   ESP32 GPIO1  (U0TXD)  -> Pico GP1 (RX)        ◀── moved from GPIO17
//   ESP32 GPIO5           -> Pico GP2  (trigger OUTPUT, ESP32 drives)
//   ESP32 EN              <- Pico GP3  (kill / reset for esptool)
//   ESP32 GPIO0           <- Pico GP4  (kill / boot-mode for esptool)
//   ESP32 GND             <-> Pico GND  (REQUIRED)
//
// Verbose debug prints (the "[esp32] ..." lines) are gated behind GB_DEBUG
// so they don't pollute the protocol stream. Set to 1 only when wiring up
// a fresh board with the ESP32 USB cable; production builds leave it off.

#define GB_DEBUG 0

#include <Arduino.h>
#include <Preferences.h>
#include <esp_debug_helpers.h>
#include "gb_target.h"

static const int PIN_TRIGGER_OUT = 5;

// =============================================================================
// v2 memory-safety guards
// =============================================================================
//
// We can't easily turn on AddressSanitizer on this Xtensa target (no
// gcc-asan in the stock Arduino-ESP32 toolchain). Instead we install three
// _source-level_ guards that catch the most common memory-safety failures
// in user-supplied gb_target_call() implementations:
//
//   1. Shadow sentinel regions immediately before AND after the input and
//      output buffers. We fill them with a known pattern, run the user
//      function, then check the pattern is intact. Any byte that changed
//      means the function wrote past a buffer end.
//
//   2. Stack canary: a sentinel uint32_t on the stack, captured BEFORE the
//      call and rechecked AFTER. The compiler's own -fstack-protector-strong
//      is the right tool for this if you're using PlatformIO -- add it to
//      build_flags in platformio.ini. The source-level canary below catches
//      a subset (overflows that hit the local frame) without any flags.
//
//   3. Panic handler: when ANY of the guards trip, we emit "MEMVIOL ..."
//      over the protocol UART instead of the usual RES2 line. The Pico's
//      runner classifies that as a memory_corruption finding.
//
// To also enable ESP-IDF heap poisoning + stack canaries (recommended for
// PlatformIO users), add the following to platformio.ini:
//
//     build_flags =
//       -fstack-protector-strong
//       -DCONFIG_HEAP_POISONING_COMPREHENSIVE=1
//       -DCONFIG_HEAP_USE_HOOKS=1
//
// On the stock Arduino IDE these are off by default and there's no clean
// project-local override. The source-level guards below work regardless.

// 32-byte sentinel pattern. Pick something distinctive so a partial memcpy
// of the input doesn't accidentally regenerate it.
static const uint8_t MEMGUARD_PATTERN[32] = {
  0xDE, 0xAD, 0xC0, 0xDE, 0xCA, 0xFE, 0xBA, 0xBE,
  0xFE, 0xED, 0xFA, 0xCE, 0xB1, 0x6B, 0x00, 0xB5,
  0x8B, 0xAD, 0xF0, 0x0D, 0x0D, 0xEF, 0xAC, 0xED,
  0xFA, 0xCE, 0xC0, 0x1A, 0xCA, 0xFE, 0xD0, 0x0D,
};

static inline void memguard_fill(uint8_t* dst) {
  memcpy(dst, MEMGUARD_PATTERN, sizeof(MEMGUARD_PATTERN));
}

// Returns the number of bytes that differ from the pattern. 0 = clean.
static inline size_t memguard_check(const uint8_t* p) {
  size_t bad = 0;
  for (size_t i = 0; i < sizeof(MEMGUARD_PATTERN); i++) {
    if (p[i] != MEMGUARD_PATTERN[i]) bad++;
  }
  return bad;
}

// Stack canary: a uint32_t on the local frame; we sample its address before
// the call and check it didn't get clobbered.
static const uint32_t STACK_CANARY = 0xCAFEBABEu;

// -----------------------------------------------------------------------------
// Quarantine state.
// -----------------------------------------------------------------------------
// The Pico can send "QUARANTINE\n" over UART to flip the chip into a software-
// enforced lockout that survives reboots. While quarantined we refuse every
// RUN command with `ERR quarantined ...`, so even if the kill-line wires
// aren't physically connected the victim still stops doing useful work.
//
// `UNQUARANTINE\n` clears the flag (operator-controlled escape hatch).
// `STATUS\n` reports the current state.
//
// State is persisted in NVS (flash-backed key/value) under namespace "gb"
// so the lockout outlives a power cycle.
static Preferences nvs;
static bool        g_quarantined = false;

// 8-byte secret used by the strcmp primitives.
static const char    SECRET[]    = "hunter2!";
static const uint8_t SECRET_LEN  = 8;

// =============================================================================
// Functions under test
// Signature: int fn(const uint8_t* in, size_t n, uint8_t* out, size_t* out_n)
// Each must be self-contained -- no Serial calls inside, nothing that yields.
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
// Hex helpers
// =============================================================================

static int hexval(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

static int hex_decode(const char* s, size_t s_len, uint8_t* out, size_t out_max) {
  size_t n = s_len / 2;
  if (n > out_max) n = out_max;
  for (size_t i = 0; i < n; i++) {
    int hi = hexval(s[2*i]);
    int lo = hexval(s[2*i + 1]);
    if (hi < 0 || lo < 0) return -1;
    out[i] = (uint8_t)((hi << 4) | lo);
  }
  return (int)n;
}

static void hex_encode(const uint8_t* bytes, size_t n, char* out) {
  static const char* kHexChars = "0123456789abcdef";
  for (size_t i = 0; i < n; i++) {
    out[2*i]     = kHexChars[bytes[i] >> 4];
    out[2*i + 1] = kHexChars[bytes[i] & 0xf];
  }
  out[2*n] = '\0';
}

// =============================================================================
// Cycle counter (Xtensa CCOUNT register) + Hardware Performance Counters (PMU)
// =============================================================================
//
// CCOUNT is the always-on cycle counter. The Xtensa LX6 also exposes 4 software
// programmable performance counters (PM0..PM3) controlled by PMG (global) and
// PMCTRL0..3 (per-counter event select).
//
// We program PM0 = INSN_RETIRED, PM1 = BRANCH_TAKEN at boot. PM2/PM3 are left
// idle but easy to add more events to.
//
// Event encoding for PMCTRLn (Xtensa LX6 PMU spec):
//   bit  [0]    : counter enable
//   bits [3:1]  : interrupt-level mask (0x7 = count at all levels)
//   bits [7:4]  : kernel/user mask (0x3 = count in both modes)
//   bits [23:16]: event select (TRACELEVEL)

static inline uint32_t get_ccount() {
  uint32_t cc;
  asm volatile("rsr.ccount %0" : "=r"(cc));
  return cc;
}

static inline uint32_t read_pm0() { return 0; }   // insns retired (LX6 has no PMU)
static inline uint32_t read_pm1() { return 0; }   // branches taken (LX6 has no PMU)

static inline void setup_hpc() {
  // No-op on LX6. Function exists so the call site doesn't need an #ifdef
  // and so an S3 port only needs to swap these three functions.
}

static void hpc_selftest() {
#if GB_DEBUG
  Serial.println("[esp32] hpc selftest  insns=0  branches=0  "
                 "(LX6 has no PMU -- 3 channels live: cycles, micros, power)");
#endif
}

// =============================================================================
// Setup + main loop
// =============================================================================

static uint32_t last_heartbeat_ms = 0;
static uint32_t pings_seen = 0;

// =============================================================================
// Panic handler: emit a PANIC line over the protocol UART if the user's
// function under test crashes. esp_register_shutdown_handler() runs late
// enough that the UART is still alive but early enough to fire before the
// chip resets. We only have a coarse "the chip is going down" signal -- not
// the actual PC + reason -- but the runner classifies any PANIC line as a
// crash finding, so the operator at least sees that this run died.
// =============================================================================

static void gb_panic_emit() {
  Serial.println("PANIC 0x0 shutdown_handler_fired");
  Serial.flush();
}

void setup() {
  // UART0 is BOTH the bootloader UART (so the Pico can flash us in BRIDGE
  // mode) AND our runtime protocol UART. We do NOT call Serial2.begin()
  // anymore; the Pico now sits on GPIO1/3 directly.
  Serial.begin(115200);

  pinMode(PIN_TRIGGER_OUT, OUTPUT);
  digitalWrite(PIN_TRIGGER_OUT, LOW);

  // Best-effort panic announcement. esp_register_shutdown_handler() only
  // covers clean shutdowns; an actual hard panic on the ESP32 jumps through
  // the IDF's panic_handler() before this fires. To emit on a true panic
  // path you'd need to override esp_panic_handler() -- left as future work.
  esp_register_shutdown_handler(gb_panic_emit);

  setup_hpc();

  // Restore persistent quarantine state from NVS. If the chip was locked out
  // before reboot, it stays locked out until an operator sends UNQUARANTINE.
  nvs.begin("gb", false);
  g_quarantined = nvs.getBool("quarantine", false);

  delay(200);
#if GB_DEBUG
  Serial.println();
  Serial.println("[esp32] harness ready (3 channels live: cycles+micros on ESP32, power on Pico)");
  Serial.print  ("[esp32] functions: ");
  for (int i = 0; i < N_FUNCTIONS; i++) {
    Serial.printf("%d=%s%s", i, FN_NAMES[i], (i == N_FUNCTIONS - 1) ? "\n" : ", ");
  }
  Serial.printf("[esp32] user_target name: %s\n", gb_target_name());
  if (g_quarantined) {
    Serial.println("[esp32] !!! BOOTED IN QUARANTINE !!!  RUN commands will be refused.");
    Serial.println("[esp32] send 'UNQUARANTINE' to clear the flag.");
  }
#endif
  hpc_selftest();
  (void)last_heartbeat_ms;     // retained so existing telemetry hooks compile
  (void)pings_seen;
}

void loop() {
  // No periodic debug heartbeat here -- everything that hits Serial now
  // shares a wire with the Pico's protocol parser, so unsolicited prints
  // would corrupt the RES2/TRACE stream. Set GB_DEBUG=1 only when you've
  // unplugged the Pico and are debugging via the ESP32 USB cable.

  if (!Serial.available()) return;

  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) return;

  // ---- Control plane ----------------------------------------------------
  if (line == "STATUS") {
    Serial.printf("STATUS %s\n", g_quarantined ? "quarantined" : "running");
    return;
  }
  if (line == "QUARANTINE") {
    g_quarantined = true;
    nvs.putBool("quarantine", true);
    Serial.println("ACK quarantined");
    return;
  }
  if (line == "UNQUARANTINE") {
    g_quarantined = false;
    nvs.putBool("quarantine", false);
    Serial.println("ACK unquarantined");
    return;
  }
  // ---- End control plane ------------------------------------------------

  if (!line.startsWith("RUN ")) {
    Serial.printf("ERR bad command: %s\n", line.c_str());
    return;
  }

  // Software-enforced quarantine check: refuse to dispatch any user code
  // while the lockout is active. The hardware kill lines (driven by the
  // Pico) provide the "physical" version of the same intervention.
  if (g_quarantined) {
    Serial.println("ERR quarantined -- pod refuses to execute (send UNQUARANTINE to clear)");
    return;
  }

  int s1 = line.indexOf(' ');
  int s2 = line.indexOf(' ', s1 + 1);
  if (s1 < 0 || s2 < 0) {
    Serial.println("ERR malformed RUN");
    return;
  }
  int    fn_id     = line.substring(s1 + 1, s2).toInt();
  String hex_input = line.substring(s2 + 1);

  if (fn_id < 0 || fn_id >= N_FUNCTIONS) {
    Serial.printf("ERR unknown fn_id %d\n", fn_id);
    return;
  }

  // Layout:
  //   [pre_input_guard 32B] [input 64B] [post_input_guard 32B]
  //   [pre_output_guard 32B] [output 64B] [post_output_guard 32B]
  // The guards bracket each buffer so an overrun in EITHER direction is
  // detected. We compose them into a single struct so the compiler keeps
  // them adjacent in stack memory (no padding between fields with uint8_t).
  struct {
    uint8_t pre_in[32];
    uint8_t input[64];
    uint8_t post_in[32];
    uint8_t pre_out[32];
    uint8_t output[64];
    uint8_t post_out[32];
  } gb;
  memguard_fill(gb.pre_in);
  memguard_fill(gb.post_in);
  memguard_fill(gb.pre_out);
  memguard_fill(gb.post_out);

  uint8_t* input  = gb.input;
  uint8_t* output = gb.output;

  int in_len = hex_decode(hex_input.c_str(), hex_input.length(), input, sizeof(gb.input));
  if (in_len < 0) {
    Serial.println("ERR bad hex");
    return;
  }

  size_t  out_len = 0;
  volatile uint32_t stack_canary_before = STACK_CANARY;

  // ===== Timed region -- everything between trigger HIGH and trigger LOW =====
  // We snapshot all four timing-class channels back-to-back around the call:
  //   cycles   : Xtensa CCOUNT             (always works)
  //   micros   : esp_timer_get_time()      (always works, includes IRQ noise)
  //   insns    : PMU PM0 (insns retired)   (best-effort, may flat-line)
  //   branches : PMU PM1 (branches taken)  (best-effort, may flat-line)
  // We hold the trigger HIGH for at least MIN_TRIGGER_US after the function
  // returns so the Pico's ~1 us polling loop can always catch the rising
  // edge even when the function under test executes in sub-microsecond time
  // (e.g. strcmp_naive returning on a first-byte mismatch).
  static const uint32_t MIN_TRIGGER_US = 200;
  digitalWrite(PIN_TRIGGER_OUT, HIGH);
  uint32_t trig_t0 = micros();
  int64_t  us0 = esp_timer_get_time();
  uint32_t i0  = read_pm0();
  uint32_t b0  = read_pm1();
  uint32_t t0  = get_ccount();
  FUNCTIONS[fn_id](input, in_len, output, &out_len);
  uint32_t t1  = get_ccount();
  uint32_t b1  = read_pm1();
  uint32_t i1  = read_pm0();
  int64_t  us1 = esp_timer_get_time();
  while ((micros() - trig_t0) < MIN_TRIGGER_US) { /* hold HIGH */ }
  digitalWrite(PIN_TRIGGER_OUT, LOW);
  // ===========================================================================
  // Memory-safety guards: check each shadow sentinel and the stack canary.
  // If any tripped, emit MEMVIOL instead of RES2 -- the Pico runner will
  // classify this as a memory_corruption finding (not just "the run failed").
  size_t bad_pre_in   = memguard_check(gb.pre_in);
  size_t bad_post_in  = memguard_check(gb.post_in);
  size_t bad_pre_out  = memguard_check(gb.pre_out);
  size_t bad_post_out = memguard_check(gb.post_out);
  bool   canary_ok    = (stack_canary_before == STACK_CANARY);
  if (bad_pre_in || bad_post_in || bad_pre_out || bad_post_out || !canary_ok) {
    const char* kind = "stack_canary";
    size_t overrun = 0;
    if (bad_post_in) {
      kind = "input_shadow_overflow";    overrun = bad_post_in;
    } else if (bad_pre_in) {
      kind = "input_shadow_overflow";    overrun = bad_pre_in;
    } else if (bad_post_out) {
      kind = "output_shadow_overflow";   overrun = bad_post_out;
    } else if (bad_pre_out) {
      kind = "output_shadow_overflow";   overrun = bad_pre_out;
    }
    Serial.printf("MEMVIOL %s overrun=%u\n", kind, (unsigned)overrun);
    pings_seen++;
    (void)canary_ok; (void)bad_pre_in; (void)bad_post_in;
    (void)bad_pre_out; (void)bad_post_out;
    return;
  }

  uint32_t cycles   = t1 - t0;
  uint32_t insns    = i1 - i0;
  uint32_t branches = b1 - b0;
  uint32_t micros_  = (uint32_t)(us1 - us0);

  // hex_output is sized for sizeof(gb.output) bytes (= 64). If user code
  // sets *out_len greater than that, hex_encode would write past the end
  // of the on-stack buffer and corrupt the return address. Cap and warn.
  if (out_len > sizeof(gb.output)) {
    Serial.printf("ERR out_len=%u exceeds output buffer (%u)\n",
                  (unsigned)out_len, (unsigned)sizeof(gb.output));
    return;
  }

  char hex_output[2 * sizeof(gb.output) + 1];
  hex_encode(output, out_len, hex_output);

  Serial.printf("RES2 %u %u %u %u %s\n",
                (unsigned)cycles, (unsigned)micros_,
                (unsigned)insns,  (unsigned)branches,
                hex_output);
  pings_seen++;
}
