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
// default functions
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

static inline uint32_t get_ccount() {
  uint32_t cc;
  asm volatile("rsr.ccount %0" : "=r"(cc));
  return cc;
}

static inline uint32_t read_pm0() { return 0; }   // insns retired
static inline uint32_t read_pm1() { return 0; }   // branches taken

static inline void setup_hpc() {
  // No-op on LX6. Function exists so the call site doesn't need an #ifdef
  // and so an S3 port only needs to swap these three functions.
}

static void hpc_selftest() {
  Serial.println("[esp32] hpc selftest  insns=0  branches=0  "
                 "(LX6 has no PMU -- 3 channels live: cycles, micros, power)");
}

// =============================================================================
// Setup + main loop
// =============================================================================

static uint32_t last_heartbeat_ms = 0;
static uint32_t pings_seen = 0;

void setup() {
  Serial.begin(115200);                             // USB debug 
  Serial2.begin(115200, SERIAL_8N1, 16, 17);        // baud, cfg, rx, tx

  pinMode(PIN_TRIGGER_OUT, OUTPUT);
  digitalWrite(PIN_TRIGGER_OUT, LOW);

  setup_hpc();

  // restore state from nvs + quarantine
  nvs.begin("gb", false);
  g_quarantined = nvs.getBool("quarantine", false);

  delay(200);
  Serial.println();
  Serial.println("[esp32] harness ready (3 channels live: cycles+micros on ESP32, power on Pico)");
  Serial.print  ("[esp32] functions: ");
  for (int i = 0; i < N_FUNCTIONS; i++) {
    Serial.printf("%d=%s%s", i, FN_NAMES[i], (i == N_FUNCTIONS - 1) ? "\n" : ", ");
  }
  Serial.printf("[esp32] user_target name: %s\n", gb_target_name());
  if (g_quarantined) {
    Serial.println("[esp32] !!! BOOTED IN QUARANTINE !!!  RUN commands will be refused.");
    Serial.println("[esp32] send 'UNQUARANTINE' over UART2 to clear the flag.");
  }
  hpc_selftest();
}

void loop() {
  uint32_t now = millis();
  if (now - last_heartbeat_ms >= 5000) {
    last_heartbeat_ms = now;
    Serial.printf("[esp32] alive  ms=%lu  runs_seen=%lu\n",
                  (unsigned long)now, (unsigned long)pings_seen);
  }

  if (!Serial2.available()) return;

  String line = Serial2.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) return;

  // ---- Control plane ----------------------------------------------------
  if (line == "STATUS") {
    Serial2.printf("STATUS %s\n", g_quarantined ? "quarantined" : "running");
    return;
  }
  if (line == "QUARANTINE") {
    g_quarantined = true;
    nvs.putBool("quarantine", true);
    Serial2.println("ACK quarantined");
    Serial.println("[esp32] !!! QUARANTINED via UART -- refusing further RUN commands !!!");
    return;
  }
  if (line == "UNQUARANTINE") {
    g_quarantined = false;
    nvs.putBool("quarantine", false);
    Serial2.println("ACK unquarantined");
    Serial.println("[esp32] quarantine flag cleared; resuming normal operation");
    return;
  }
  // ---- End control plane ------------------------------------------------

  if (!line.startsWith("RUN ")) {
    Serial2.printf("ERR bad command: %s\n", line.c_str());
    return;
  }

  // Software-enforced quarantine check: refuse to dispatch any user code
  // while the lockout is active. The hardware kill lines (driven by the
  // Pico) provide the "physical" version of the same intervention.
  if (g_quarantined) {
    Serial2.println("ERR quarantined -- pod refuses to execute (send UNQUARANTINE to clear)");
    return;
  }

  int s1 = line.indexOf(' ');
  int s2 = line.indexOf(' ', s1 + 1);
  if (s1 < 0 || s2 < 0) {
    Serial2.println("ERR malformed RUN");
    return;
  }
  int    fn_id     = line.substring(s1 + 1, s2).toInt();
  String hex_input = line.substring(s2 + 1);

  if (fn_id < 0 || fn_id >= N_FUNCTIONS) {
    Serial2.printf("ERR unknown fn_id %d\n", fn_id);
    return;
  }

  uint8_t input[64];
  int in_len = hex_decode(hex_input.c_str(), hex_input.length(), input, sizeof(input));
  if (in_len < 0) {
    Serial2.println("ERR bad hex");
    return;
  }

  uint8_t output[64];
  size_t  out_len = 0;

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

  uint32_t cycles   = t1 - t0;
  uint32_t insns    = i1 - i0;
  uint32_t branches = b1 - b0;
  uint32_t micros_  = (uint32_t)(us1 - us0);

  char hex_output[2 * sizeof(output) + 1];
  hex_encode(output, out_len, hex_output);

  Serial2.printf("RES2 %u %u %u %u %s\n",
                 (unsigned)cycles, (unsigned)micros_,
                 (unsigned)insns,  (unsigned)branches,
                 hex_output);
  pings_seen++;
}
