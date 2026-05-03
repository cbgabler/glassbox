// Pico monitor harness for GlassBox.
//
// Bridges USB CDC (laptop runner <-> Pico) and UART0 (Pico <-> ESP32),
// captures a 256-sample power trace from the INA169 ADC during the
// timed window the ESP32 marks with its trigger pin, and is the active
// enforcement boundary that drives the kill lines on `EN` and `GPIO0`.
//
// USB CDC commands (laptop -> Pico):
//   "RUN <fn_id> <hex_input>\n"   -- forward to ESP32, capture trace
//   "QUARANTINE\n"                -- soft-quarantine ESP32 (NVS flag) +
//                                    fire kill lines (open-drain on EN/GPIO0)
//   "UNQUARANTINE\n"              -- clear the NVS flag (operator escape hatch)
//   "STATUS\n"                    -- ask Pico+ESP32 for combined status
//   "BRIDGE [seconds]\n"          -- v3: enter transparent USB<->UART0
//                                   passthrough so esptool / arduino-cli
//                                   can flash the ESP32 through this Pico.
//                                   Auto-exits after `seconds` (default 90)
//                                   or when DTR drops after the upload.
//
// USB CDC responses (Pico -> laptop):
//   "RES2 <cycles> <us> <insns> <branches> <hex_output>\n"
//   "TRACE <s0,s1,...,s255>\n"
//   "ACK quarantined\n"   /  "ACK unquarantined\n"
//   "ACK bridge <seconds>\n"   (entering bridge mode)
//   "STATUS <state>\n"    -- e.g. "STATUS running" or "STATUS quarantined"
//   "ERR <reason>\n"
//
// Wiring (v3 -- updated for Route A: single-USB flashing through the Pico):
//   Pico GP0 (UART0 TX) -> ESP32 GPIO3  (U0RXD)   ◀── moved from GPIO16
//   Pico GP1 (UART0 RX) <- ESP32 GPIO1  (U0TXD)   ◀── moved from GPIO17
//   Pico GP2            <- ESP32 GPIO5  (trigger INPUT, ESP32 drives)
//   Pico GP3            -> ESP32 EN     (kill / esptool reset, open-drain)
//   Pico GP4            -> ESP32 GPIO0  (kill / esptool boot,  open-drain)
//   Pico GP26 (ADC0)    <- INA169 OUT
//   Pico GND            <-> ESP32 GND   (REQUIRED)
//
// Why UART0 on the ESP32 side? Because that's where the ROM bootloader
// lives. The Pico can pretend to be a CP2102 USB-to-serial chip when the
// host opens its CDC port and starts toggling DTR/RTS, forwarding bytes
// onto UART0 and translating the line-state transitions into the EN /
// GPIO0 reset sequence esptool expects. Net effect: only the Pico USB
// cable needs to be plugged in for both flashing AND running the harness.

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

#define USE_HARDWARE_FET 0

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
// v3 -- BRIDGE mode: pretend to be a CP2102 USB-to-serial chip for esptool.
// =============================================================================
//
// In normal operation the Pico parses RUN/STATUS/QUARANTINE/etc. lines from
// the USB CDC port and replies with structured RES2/TRACE/ACK lines. When
// the runner wants to FLASH the ESP32 over this same wire, it sends a
// "BRIDGE [seconds]\n" command and we switch to a transparent passthrough:
//
//   * Every byte from USB CDC is forwarded verbatim onto UART0 (Serial1).
//   * Every byte from UART0 is forwarded verbatim back to USB CDC.
//   * Every change in the host's DTR/RTS line state is translated into
//     EN / GPIO0 GPIO transitions exactly the way a CP2102 dev-board's
//     auto-reset circuitry would, so esptool's standard reset sequence
//     drops the ESP32 into download mode.
//
// We exit bridge mode in three ways (whichever fires first):
//   (a) the per-session deadline (default 90 s) elapses,
//   (b) the host closes the CDC port (DTR drops and stays inactive
//       for >300 ms after we've seen at least one byte of traffic), or
//   (c) the user power-cycles the Pico.
//
// (a) is the safety net so a half-finished flash can't permanently
// strand us; (b) is the normal happy path.
//
// Polarity: most ESP32 dev boards invert DTR/RTS through transistors so that
// host_DTR=true -> EN=LOW (chip in reset) and host_RTS=true -> GPIO0=LOW
// (boot mode). We reproduce that exactly using kill_pin_assert/release
// (which already encode the FET-vs-direct wiring choice). The two GPIOs
// drive the ESP32's actual EN and GPIO0 lines, so esptool's reset dance
// works without any electrical changes -- same wires, new firmware role.
static bool     g_in_bridge       = false;
static uint32_t g_bridge_deadline = 0;     // millis() value when bridge auto-exits
static bool     g_bridge_saw_data = false; // becomes true after first forwarded byte
static uint32_t g_bridge_dtr_low_since = 0; // millis() when DTR last went inactive
static bool     g_last_dtr        = false;
// Phased close-detection state. A normal flash session has TWO host-side
// port closes:
//   phase 0: the runner that issued BRIDGE is still holding the port open.
//   phase 1: the runner closed the port to hand off to arduino-cli/esptool.
//            We have to be patient here -- arduino-cli's compile step can
//            keep the port closed for many seconds, especially the first
//            time. Use a long timeout (60 s) before giving up.
//   phase 2: esptool has reopened the port and at some point closed it
//            again (the flash finished). Now we want to exit BRIDGE
//            promptly so exit_to_run_mode() boots the freshly-flashed
//            firmware. Use a short timeout (5 s) here.
static uint8_t  g_bridge_dtr_phase = 0;

// Hands-off bootloader entry: the Pico drives the ESP32's reset sequence
// itself instead of trying to translate the host's DTR/RTS line state.
// This is more robust because we don't depend on the host-to-Pico CDC
// stack reliably propagating line-state changes (some host/OS/driver
// combinations swallow them), and it means the user only has to wire the
// EN and GPIO0 kill lines correctly -- they don't have to debug esptool's
// auto-reset circuit semantics.
static void enter_rom_bootloader() {
  // Timing here is calibrated to match esptool's classic_reset sequence,
  // which is what the ESP32 dev boards we test on are actually validated
  // against. The ESP32 reference design puts a 10 uF cap on EN to debounce
  // the reset line, so a too-short EN-low pulse doesn't actually discharge
  // the cap below the chip's logic-low threshold and the chip never resets.
  // Empirically: 20 ms was unreliable from a "hot" entry (USB CDC just
  // finished flushing); 100 ms matches esptool and resets every time.
  //
  // Hold IO0 low BEFORE we drop EN -- the bootrom samples GPIO0 the
  // instant EN is released, so IO0 must already be low at that moment.
  kill_pin_assert(PIN_KILL_BOOT);     // IO0 -> LOW
  delay(5);                           // let IO0 settle (overcomes any host-side glitch)
  kill_pin_assert(PIN_KILL_EN);       // EN  -> LOW (chip in reset)
  delay(100);                         // hold reset 100ms (esptool default; 20 was marginal)
  kill_pin_release(PIN_KILL_EN);      // EN released; chip starts in bootloader
  delay(80);                          // give the bootrom time to lock IO0 in (esptool: 50)
  kill_pin_release(PIN_KILL_BOOT);    // safe to release IO0; chip is in DL mode
}

static void exit_to_run_mode() {
  // After flashing, we want the chip to boot the freshly-uploaded user
  // firmware. Make sure IO0 is high (run mode) and pulse EN.
  kill_pin_release(PIN_KILL_BOOT);    // IO0 floats high via internal pull-up
  delayMicroseconds(500);
  kill_pin_assert(PIN_KILL_EN);       // EN -> LOW (reset)
  delay(20);
  kill_pin_release(PIN_KILL_EN);      // EN released; chip boots in RUN mode
}

static void bridge_enter(uint32_t seconds) {
  if (seconds < 5)   seconds = 5;
  if (seconds > 600) seconds = 600;          // bound the safety net
  g_in_bridge       = true;
  g_bridge_deadline = millis() + seconds * 1000UL;
  g_bridge_saw_data = false;
  g_bridge_dtr_low_since = 0;
  g_bridge_dtr_phase = 0;
  g_last_dtr = Serial.dtr();
  enter_rom_bootloader();
}

static void bridge_exit(const char* reason) {
  g_in_bridge = false;
  exit_to_run_mode();
  Serial.printf("ACK bridge_exit %s\n", reason);
  Serial.println("READY harness v2 quarantine-capable");
}

// Run one slice of the bridge loop. Called from loop() instead of the
// normal command parser whenever g_in_bridge is true. We do NOT handle
// DTR/RTS in here -- bridge_enter() already put the ESP32 into ROM
// bootloader, and esptool's reset toggles are deliberately ignored. Our
// only job here is to (a) forward bytes both directions, (b) track DTR
// for the "host closed the port -> upload finished" exit condition, and
// (c) enforce the deadline.
static void bridge_tick() {
  bool dtr = Serial.dtr();
  if (dtr != g_last_dtr) {
    g_last_dtr = dtr;
    if (!dtr) {
      if (g_bridge_dtr_low_since == 0) g_bridge_dtr_low_since = millis();
      // Going from "any host has the port open" to "no host". Phase 0 -> 1.
      if (g_bridge_dtr_phase == 0) g_bridge_dtr_phase = 1;
    } else {
      g_bridge_dtr_low_since = 0;
      // A second client (arduino-cli's esptool) has now opened the port.
      // From here on, "DTR drops again" is the meaningful close signal.
      if (g_bridge_dtr_phase == 1) g_bridge_dtr_phase = 2;
    }
  }

  // USB CDC -> UART0  (host bytes onto the wire to the ESP32 bootloader)
  while (Serial.available()) {
    int c = Serial.read();
    if (c < 0) break;
    Serial1.write((uint8_t)c);
    g_bridge_saw_data = true;
  }

  // UART0 -> USB CDC  (ESP32 bootloader replies back to the host)
  while (Serial1.available()) {
    int c = Serial1.read();
    if (c < 0) break;
    Serial.write((uint8_t)c);
    g_bridge_saw_data = true;
  }

  // Exit conditions.
  uint32_t now = millis();
  if ((int32_t)(now - g_bridge_deadline) >= 0) {
    bridge_exit("timeout");
    return;
  }
  // Host closed the port AFTER the upload made any actual progress.
  //
  // A single flash session involves at least two host-side closes:
  //   (1) The python runner that issued BRIDGE closes its diagnostic open
  //       after seeing "ACK bridge" so it can hand control to arduino-cli.
  //       DTR drops -- phase 0 -> 1.
  //   (2) arduino-cli compiles (port stays closed in phase 1 the whole
  //       time), then esptool reopens the port -- DTR rises, phase 1 -> 2.
  //   (3) esptool runs its reset/sync/flash sequence and closes the port.
  //       DTR drops -- still in phase 2, this is the "we're done" signal.
  //
  // The gap between (1) and (2) is unbounded: a cached compile+esptool
  // launch is ~3-10 s, but a cold first-time compile can take 30-90 s. So
  // in phase 1 we have to be patient and use a long timeout (60 s, up
  // from 30 s -- 30 s was killing the bridge mid-compile on cold runs).
  // In phase 2 we know esptool has been driving the chip; once esptool
  // closes the port we want to release the bridge as fast as possible so
  // exit_to_run_mode() can boot the freshly-flashed firmware. 5 s is more
  // than esptool's longest momentary DTR drop (its reset dance toggles
  // DTR for ~100 ms at a time) but short enough that the chip boots
  // promptly after upload completes.
  uint32_t close_timeout = (g_bridge_dtr_phase >= 2) ? 5000UL : 60000UL;
  if (g_bridge_saw_data && !dtr && g_bridge_dtr_low_since > 0
      && (now - g_bridge_dtr_low_since) > close_timeout) {
    bridge_exit("host_closed");
    return;
  }
}

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
  // ---- 0. While in flash-passthrough, skip the command parser entirely. ----
  // bridge_tick() does its own non-blocking USB<->UART forwarding and exits
  // back to harness mode on timeout or host-disconnect. We intentionally do
  // NOT take any other action while bridged -- in particular, no quarantine
  // logic and no trace capture; the kill lines belong to esptool right now.
  if (g_in_bridge) {
    bridge_tick();
    return;
  }

  // ---- 1. Wait for any command from the laptop on USB CDC. ----
  String cmd = read_line_blocking(Serial);
  cmd.trim();
  if (cmd.length() == 0) return;

  // ---- Bridge / passthrough request --------------------------------------
  if (cmd == "BRIDGE" || cmd.startsWith("BRIDGE ")) {
    uint32_t seconds = 90;
    int sp = cmd.indexOf(' ');
    if (sp > 0) {
      long parsed = cmd.substring(sp + 1).toInt();
      if (parsed > 0) seconds = (uint32_t)parsed;
    }
    // Reset the ESP32 into download mode FIRST, before we ACK. Otherwise
    // the host (auto_flash.py) will read "ACK bridge", close the port,
    // hand off to arduino-cli, and esptool can be syncing into a dead
    // window while the Pico is still pulling EN low. Doing the reset up
    // front means by the time the host reads the ACK, the chip is already
    // sitting in the ROM bootloader waiting for a sync packet.
    bridge_enter(seconds);
    Serial.printf("ACK bridge %lu\n", (unsigned long)seconds);
    Serial.flush();
    return;
  }

  // ---- Control plane: quarantine + status ---------------------------------
  if (cmd == "QUARANTINE") {
    fire_quarantine();
    Serial.println("ACK quarantined");
    return;
  }
  if (cmd == "UNQUARANTINE") {
    release_quarantine_uart();
    Serial.println("ACK unquarantined");
    return;
  }
  if (cmd == "STATUS") {
    // Ask the ESP32 over UART. We give it a generous window because the
    // ESP32 can be mid-flush from a previous TRACE and 200 ms isn't always
    // enough on USB-CDC-multipl  exed ports. If it still times out, the
    // hardware kill probably worked and the chip is in download mode.
    while (Serial1.available()) Serial1.read();   // drop stale RX bytes
    Serial1.println("STATUS");
    Serial1.flush();                              // make sure it's on the wire
    String reply;
    bool got = wait_uart_line(reply, 1000);
    if (got && reply.startsWith("STATUS")) {
      Serial.println(reply);
    } else {
      Serial.print("STATUS unresponsive (got=");
      Serial.print(got ? "\"" : "<timeout>");
      if (got) { Serial.print(reply); Serial.print("\""); }
      Serial.println(")");
    }
    return;
  }
  // ---- End control plane --------------------------------------------------

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

  // ---- 2. Forward the command to the ESP32 over UART1. ----
  Serial1.println(cmd);
  uint32_t fn_dispatch_us = micros();

  // ---- 3. Wait for the ESP32 to raise the trigger (start of fn). ----
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

