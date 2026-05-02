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

static const uint8_t PIN_UART_TX     = 0;
static const uint8_t PIN_UART_RX     = 1;
static const uint8_t PIN_TRIGGER_IN  = 2;     // ESP32 drives this HIGH during fn
static const uint8_t PIN_KILL_EN     = 3;     // -> ESP32 EN     (open-drain, idle = INPUT/Hi-Z)
static const uint8_t PIN_KILL_BOOT   = 4;     // -> ESP32 GPIO0  (open-drain, idle = INPUT/Hi-Z)
static const uint8_t PIN_ADC         = A0;    // GP26 = ADC0

static const int TRACE_LEN = 256;
static uint16_t  trace[TRACE_LEN];

// =============================================================================
// Kill-line drivers. Two physical wirings are supported
// =============================================================================

// ! ALWAYS SET TO 0 UNLESS YOU HAVE THE HARDWARE !

// USE_HARDWARE_FET = 0  -- "software open-drain" (default, no extra parts):
//     The Pico GPIO is wired directly to ESP32 EN / GPIO0. To assert kill we
//     flip the pin to OUTPUT LOW (sinks the line to ground); to release we go
//     back to INPUT (high-Z)
//
// USE_HARDWARE_FET = 1  -- "hardware open-drain" (production-grade, requires
//     2x 2N7000 N-FET + 2x 10k gate pull-downs + 2x 1k gate series resistors,

#define USE_HARDWARE_FET 1

#if USE_HARDWARE_FET
static void kill_pin_release(uint8_t pin) {
  pinMode(pin, OUTPUT);
  digitalWrite(pin, LOW);              // gate LOW -> FET off -> released
}
static void kill_pin_assert(uint8_t pin) {
  pinMode(pin, OUTPUT);
  digitalWrite(pin, HIGH);             // gate HIGH -> FET on -> ESP32 pin to GND
}
#else
static void kill_pin_release(uint8_t pin) {
  pinMode(pin, INPUT);                 // high-Z; ESP32 pull-up wins
}
static void kill_pin_assert(uint8_t pin) {
  pinMode(pin, OUTPUT);
  digitalWrite(pin, LOW);              // sink ESP32 line to ground directly
}
#endif

// =============================================================================
// Main + Setup
// =============================================================================

void setup() {
  Serial.begin(115200);                           // USB CDC to laptop runner

  // Pin remap is only needed on Earle Philhower's arduino-pico core
  // (which exposes setTX/setRX). The official Arduino Mbed OS RP2040 core
  // hardwires Serial1 to UART0 = GP0 (TX) / GP1 (RX), which is exactly the
  // wiring we want, so the remap is a no-op there. Either core compiles.
#if !defined(ARDUINO_ARCH_MBED) && !defined(ARDUINO_ARCH_MBED_RP2040)
  Serial1.setTX(PIN_UART_TX);
  Serial1.setRX(PIN_UART_RX);
#endif
  Serial1.begin(115200);                          // UART0 to ESP32 (GP0/GP1)

  pinMode(PIN_TRIGGER_IN, INPUT);
  analogReadResolution(12);

  // Park kill lines in high-Z immediately on boot so the ESP32's pull-ups
  // win and the chip boots normally. Same defensive default as the README.
  kill_pin_release(PIN_KILL_EN);
  kill_pin_release(PIN_KILL_BOOT);

  uint32_t t0 = millis();
  while (!Serial && (millis() - t0) < 3000) { delay(10); }

  // Single, well-defined "I'm alive" line. The runner drains the port until
  // it sees this string before sending any RUN commands -- that way boot
  // banners can never collide with the first request.
  Serial.println();
  Serial.println("READY harness v2 quarantine-capable");
}

// =============================================================================
// Drain the UART line into `out` and discard everything else.
// Used when we expect a single short ACK/STATUS line back from the ESP32.
// =============================================================================
static bool wait_uart_line(String& out, uint32_t timeout_ms = 200) {
  out = "";
  uint32_t deadline = millis() + timeout_ms;
  while (millis() < deadline) {
    while (Serial1.available()) {
      char c = Serial1.read();
      if (c == '\r') continue;
      if (c == '\n') return true;
      out += c;
      if ((int)out.length() > 256) return false;
    }
  }
  return false;
}

// =============================================================================
// Quarantine functions
// =============================================================================

// ? Enforcement step of the hardware. Kill it before it has a chance to make more mistakes.

static void fire_quarantine() {
  Serial1.println("QUARANTINE");
  String ack;
  bool got_ack = wait_uart_line(ack, 250);
  if (got_ack && ack.startsWith("ACK")) {
    Serial.print("[pico] ESP32 acknowledged quarantine: ");
    Serial.println(ack);
  } else {
    Serial.print("[pico] WARN: no ACK from ESP32 (got=");
    Serial.print(ack);
    Serial.println("); continuing with kill-line strike anyway");
  }

  // Hardware kill: pull both lines LOW for ~20 ms, then release.
  kill_pin_assert(PIN_KILL_BOOT);          // GPIO0 LOW first
  delayMicroseconds(500);                  // settle
  kill_pin_assert(PIN_KILL_EN);            // tap EN
  delay(20);                               // hold reset > 10 ms
  kill_pin_release(PIN_KILL_EN);           // release; chip resets, samples GPIO0=LOW
  delay(50);                               // wait for bootloader to come up
  kill_pin_release(PIN_KILL_BOOT);         // safe to release; chip is in DL mode
}

static void release_quarantine_uart() {
  Serial1.println("UNQUARANTINE");
  String ack;
  bool got_ack = wait_uart_line(ack, 250);
  if (got_ack && ack.startsWith("ACK")) {
    Serial.print("[pico] ESP32 acknowledged unquarantine: ");
    Serial.println(ack);
  } else {
    Serial.print("[pico] WARN: no ACK from ESP32 to UNQUARANTINE (got=");
    Serial.print(ack);
    Serial.println(")");
  }
}

// Read one '\n'-terminated line from a Stream into `out`, with timeout.
// Returns true on success, false on timeout / overflow.
static bool read_line(Stream& s, String& out, uint32_t timeout_ms, int max_len = 512) {
  out = "";
  uint32_t deadline = millis() + timeout_ms;
  while (millis() < deadline) {
    while (s.available()) {
      char c = s.read();
      if (c == '\r') continue;
      if (c == '\n') return true;
      out += c;
      if ((int)out.length() > max_len) return false;
    }
  }
  return false;
}

// Block forever until a '\n'-terminated line arrives on the given stream.
// Used for the USB command channel where we genuinely have nothing else to do.
static String read_line_blocking(Stream& s, int max_len = 512) {
  String out;
  while (true) {
    while (s.available()) {
      char c = s.read();
      if (c == '\r') continue;
      if (c == '\n') return out;
      out += c;
      if ((int)out.length() > max_len) return out;  // truncate, caller must validate
    }
  }
}

void loop() {

  // 1. Wait for command from laptop on connection to hardware

  String cmd = read_line_blocking(Serial);
  cmd.trim();
  if (cmd.length() == 0) {
    return;
  }

  // Control plane

  if (cmd == "QUARANTINE") {
    fire_quarantine();
    Serial.println("ACK quarantined");
    return;
  }
  if (cmd == "QUARANTINE") {
    release_quarantine_uart();
    Serial.println("ACK unquarantined");
    return;
  }
  if (cmd == "STATUS") {
    while (Serial1.available()) Serial1.read();   // drop stale RX bytes
    Serial1.println("STATUS");
    Serial1.flush(); // flush
    String reply;
    bool got = wait_uart_line(reply, 1000);
    if (got && reply.startsWith("STATUS")) {
      Serial.println(reply);
    } else {
      Serial.print("STATUS unresponsive (got=");
      Serial.print(got ? "\"" : "<timeout>");
      if (got) {
        Serial.print(reply);
        Serial.print("\"");
      }
      Serial.println(")");
    }
    return;
  }

  if (!cmd.startsWith("RUN ")) {
    // Hex-dump the bad bytes so the runner can see what actually arrived
    // (printf %s stops at the first null and hides non-printable chars).
    Serial.print("ERR bad command (len=");
    Serial.print(cmd.length());
    Serial.print(", hex=");
    for (size_t i = 0; i < cmd.length(); i++) {
      char buf[4]; snprintf(buf, sizeof(buf), "%02x", (uint8_t)cmd[i]);
      Serial.print(buf);
    }
    Serial.println(")");
    return;
  }

  // 2. Forward command to ESP DEV over UART1

  Serial.println(cmd);
  uint32_t fn_dispatch_us = micros();

  // 3. wait for ESP to raise trigger

  uint32_t deadline = millis() + 200;
  String   pre_trigger_err;
  bool     have_pre_err = false;
  while (digitalRead(PIN_TRIGGER_IN) == LOW && millis() < deadline) {
    while (Serial1.available()) {
      char c = Serial1.read();
      if (c == '\r') continue;
      if (c == '\n') { have_pre_err = true; break; }
      pre_trigger_err += c;
      if (pre_trigger_err.length() > 256) { have_pre_err = true; break; }
    }
    if (have_pre_err) break;
  }
  if (have_pre_err && pre_trigger_err.startsWith("ERR")) {
    // ESP32 actively refused; pass through verbatim.
    Serial.println(pre_trigger_err);
    while (Serial1.available()) Serial1.read();
    return;
  }
  if (digitalRead(PIN_TRIGGER_IN) == LOW) {
    Serial.println("ERR trigger never went HIGH");
    while (Serial1.available()) Serial1.read();
    return;
  }
   // ---- 4. Capture TRACE_LEN ADC samples as fast as the core lets us. ----
  // analogRead() on the Earle Philhower core takes ~3-5 us per call,
  // so 256 samples covers ~1 ms -- enough to capture a strcmp window.
  for (int i = 0; i < TRACE_LEN; i++) {
    trace[i] = analogRead(PIN_ADC);
  }

  // ---- 5. Read the RES line back from the ESP32 (also tolerates ERR). ----
  String reply;
  if (!read_line(Serial1, reply, /*timeout_ms=*/200)) {
    Serial.printf("ERR no reply from ESP32 (last=%s)\n", reply.c_str());
    return;
  }

  // ---- 6. Forward RES + TRACE to the laptop. ----
  Serial.println(reply);
  Serial.print("TRACE ");
  for (int i = 0; i < TRACE_LEN; i++) {
    Serial.print(trace[i]);
    if (i < TRACE_LEN - 1) Serial.print(',');
  }
  Serial.println();

  (void)fn_dispatch_us;  // available for latency telemetry later
}
