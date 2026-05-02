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

  
