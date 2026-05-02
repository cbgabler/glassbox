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