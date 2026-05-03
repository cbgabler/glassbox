# GlassBox — Full Pin Diagram

A pin-level companion to the wire table in [`../README.md`](../README.md). The
README tells you *what* connects to *what*; this doc tells you *where each pin
physically sits on the board*, so you can replicate the build on a fresh
breadboard without counting holes.

All three boards are 3.3 V logic; the only 5 V on the bus is the high-current
leg through the INA169 (Pico VBUS → Vin+ → Vin− → ESP32 VIN). Nothing on this
diagram needs a level shifter.

---

## 1. Connection summary (12 wires)

| #   | From (Pico)         | To                        | Type    | Notes                                |
| --- | ------------------- | ------------------------- | ------- | ------------------------------------ |
| 1   | **GP0**  (pin 1)    | ESP32 **RX0 / GPIO3**     | UART TX | Pico → ESP32 (commands + esptool)    |
| 2   | **GP1**  (pin 2)    | ESP32 **TX0 / GPIO1**     | UART RX | ESP32 → Pico (telemetry + boot ack)  |
| 3   | **GP2**  (pin 4)    | ESP32 **GPIO5**           | Trigger | Marks function start / end           |
| 4   | **GP3**  (pin 5)    | ESP32 **EN**              | Kill    | `KILL_EN` — open-drain, low = reset  |
| 5   | **GP4**  (pin 6)    | ESP32 **GPIO0**           | Kill    | `KILL_BOOT` — open-drain, low = boot |
| 6   | **GND**  (pin 38)   | ESP32 **GND**             | Ground  | Common ground (digital)              |
| 7   | **VBUS** (pin 40)   | INA169 **Vin+**           | 5 V src | High-side of the sensed leg          |
| 8   | **3V3 OUT** (pin 36)| INA169 **Vcc**            | 3.3 V   | Powers the INA169 op-amp             |
| 9   | **AGND** (pin 33)   | INA169 **GND**            | Ground  | Analog ground for the ADC return     |
| 10  | **GP26 / ADC0** (pin 31) | INA169 **OUT** (Vout) | Analog  | Power trace into the ML classifier   |
| 11  | *(n/a)*             | INA169 **Vin−** → ESP32 **5V / VIN** | 5 V load | Sensed leg into the ESP32 |
| 12  | *(implicit)*        | ESP32 **GND** ↔ INA169 **GND** | Ground | Star-ground at the INA169 pad |

> Pin numbers above are the Raspberry Pi Pico's silk-screen pin numbers
> (1–40 around the edge). The ESP32 names match what's printed on the
> WROOM-32 dev board's silk screen (USB-C, 30-pin variant).

---

## 2. Raspberry Pi Pico — pinout (annotated)

```
                              ┌─── USB ───┐
                              │           │
                  GP0 / TX  → │  1     40 │ ← VBUS  ─────── INA169 Vin+   (wire 7)
                  GP1 / RX  ← │  2     39 │   VSYS
                  GND       ─ │  3     38 │ ─ GND  ──────── ESP32 GND     (wire 6)
                  GP2 (TRG) → │  4     37 │   3V3 EN
                  GP3 (KEN) → │  5     36 │ ← 3V3 OUT  ──── INA169 Vcc    (wire 8)
                  GP4 (KBT) → │  6     35 │   ADC_VREF
                  GP5         │  7     34 │   GP28 / ADC2
                  GND       ─ │  8     33 │ ─ AGND  ─────── INA169 GND    (wire 9)
                  GP6         │  9     32 │   GP27 / ADC1
                  GP7         │ 10     31 │   GP26 / ADC0  → INA169 OUT   (wire 10)
                  GP8         │ 11     30 │   RUN
                  GP9         │ 12     29 │   GP22
                  GND       ─ │ 13     28 │ ─ GND
                  GP10        │ 14     27 │   GP21
                  GP11        │ 15     26 │   GP20
                  GP12        │ 16     25 │   GP19
                  GP13        │ 17     24 │   GP18
                  GND       ─ │ 18     23 │ ─ GND
                  GP14        │ 19     22 │   GP17
                  GP15        │ 20     21 │   GP16
                              └───────────┘

Legend:  TX = UART0 TX     RX = UART0 RX     TRG = trigger
         KEN = KILL_EN     KBT = KILL_BOOT   ADC0 = analog input
```

**Why pin 33 (AGND) and not any other ground?** AGND is internally tied to
the Pico's quiet analog ground plane. Returning the INA169's analog current
through AGND (instead of a digital GND) keeps switching noise from the Pico's
USB / LED / GP0–GP4 wiggling out of the ADC reading.

**Why pin 38 GND for the ESP32 return?** It's right next to a row of digital
ground pads on the breadboard, and the ESP32's GND is digital. Any of the
Pico's GND pins (3, 8, 13, 18, 23, 28, 38) works electrically — pin 38 is just
the convenient one.

---

## 3. ESP32 WROOM-32 dev board — pinout (annotated)

Pin labels follow the silk-screen on the typical USB-C, 30-pin WROOM-32
dev board (e.g. TekBots PMOGYW). Only the pins involved in the GlassBox pod
are highlighted.

```
                           ┌──── USB-C ────┐
                           │               │
   ESP32 EN  ←─ GP3 (KEN)  │ EN        GND │ → Pico GND  (wire 6)
                           │ VP / GPIO36   │ GPIO23
                           │ VN / GPIO39   │ GPIO22
                           │ GPIO34    TX0 │ ── GPIO1  → Pico GP1 / RX  (wire 2)
                           │ GPIO35    RX0 │ ── GPIO3  ← Pico GP0 / TX  (wire 1)
                           │ GPIO32    GPIO21
                           │ GPIO33    GPIO19
                           │ GPIO25    GPIO18
                           │ GPIO26    GPIO5  ← Pico GP2 (trigger)      (wire 3)
                           │ GPIO27    GPIO17
                           │ GPIO14    GPIO16
                           │ GPIO12    GPIO4
   Pico GND ←──── GND      │ GND       GPIO0  ← Pico GP4 (KILL_BOOT)    (wire 5)
                           │ GPIO13    GPIO2
                           │ GPIO9     GPIO15
                           │ GPIO10    GPIO8
                           │ GPIO11    GPIO7
   INA169 Vin− ─→ 5V / VIN │ 5V        GPIO6
                           │ 3V3            │
                           └────────────────┘

Legend:  EN  = chip enable (active low → reset)
         RX0 = U0RXD = bootloader RX = ESP-side of the ESP↔Pico UART
         TX0 = U0TXD = bootloader TX
         GPIO0 = strap pin: LOW at reset → ROM bootloader (download mode)
```

**Why GPIO1 / GPIO3 (UART0) and not UART2?** Two reasons:

1. UART0 is the **bootloader UART** — the same wires `esptool` talks to.
   Reusing them lets the Pico flash the ESP32 in `BRIDGE` mode without an
   ESP32 USB cable plugged in (Route A in the README).
2. The harness firmware moved `Serial` from UART2 (GPIO16/17) to UART0
   (GPIO1/3) in v3 specifically to enable Route A. See `HANDOFF.md`.

⚠ **Never plug the ESP32's own USB-C in while the Pico is connected** — the
ESP32's onboard CP2102 also drives GPIO1/3 and will fight the Pico for the
TX line. Detach the Pico first if you ever need a direct serial monitor on
the ESP32 (and set `GB_DEBUG 1` in `esp/harness/harness.ino`).

---

## 4. INA169 breakout — pinout (annotated)

The INA169 is a high-side current-sense amp on a 5-pin breakout. It sits
*inline* on the ESP32's 5 V supply and reports current as a 0–3.3 V analog
voltage on `OUT`. The whole point of this board is that wires 7 and 11
together force *all* of the ESP32's supply current through the sense
resistor.

```
                           ┌─────────────────┐
                           │      INA169     │
                           │                 │
        Pico VBUS  ────→   │ Vin+    GND ←── │  Pico AGND   (wire 9)
        ESP32 5V/VIN ──←   │ Vin-           │
        Pico 3V3   ────→   │ Vcc    OUT  ──→ │  Pico GP26   (wire 10)
                           │                 │
                           └─────────────────┘
```

**Power flow (the sensed leg):**

```
   USB host  ──┐
               │  (5 V, up to ~500 mA)
   Pico VBUS ──┴──→  INA169 Vin+  ──[ Rs = 0.1 Ω ]──→  Vin−  ──→  ESP32 VIN
                                                                   │
                                                                   ▼
                                                           ESP32's onboard
                                                           3.3 V LDO + chip
```

The voltage drop across `Rs` is amplified by ~10× (with the default 10 kΩ
gain resistor on the breakout) and emitted on `OUT` as a single-ended
analog voltage referenced to INA169 GND. The Pico's ADC0 reads it relative
to AGND — that's why wire 9 is the analog ground, not a digital one.

---

## 5. End-to-end "as-wired" view

The combined picture, with every wire from Section 1 placed onto its
actual pads:

```
        ┌─────────────────── Raspberry Pi Pico ───────────────────┐
        │                                                          │
        │  GP0  (1) ───────────────── ESP32 RX0 / GPIO3            │
        │  GP1  (2) ───────────────── ESP32 TX0 / GPIO1            │
        │  GP2  (4) ───────────────── ESP32 GPIO5    (trigger)     │
        │  GP3  (5) ───────────────── ESP32 EN       (KILL_EN)     │
        │  GP4  (6) ───────────────── ESP32 GPIO0    (KILL_BOOT)   │
        │  GND  (38) ──────────────── ESP32 GND                    │
        │                                                          │
        │  VBUS (40) ──────────────── INA169 Vin+                  │
        │  3V3 OUT (36) ───────────── INA169 Vcc                   │
        │  AGND (33) ──────────────── INA169 GND                   │
        │  GP26 / ADC0 (31) ───────── INA169 OUT                   │
        │                                                          │
        └──────────────────────────────────────────────────────────┘
                                                  │
                                                  │  INA169 Vin−
                                                  ▼
                                       ESP32  5V / VIN
```

A successful build looks like this on a 400-point breadboard:

- The Pico straddles the centre channel, USB facing one short edge.
- The ESP32 straddles the centre channel on the *other* short edge (no
  pin overlap with the Pico).
- The INA169 lives on its own column between them, with `Vin+` on the
  Pico-facing side and `Vin−` on the ESP32-facing side.
- Wires 1–5 (UART/trigger/kill) and wire 6 (GND) run across the bridge
  between the two MCUs.
- Wires 7 and 11 form the 5 V high-current loop *through* the INA169.
- Wires 8, 9, and 10 are the INA169's own bias and signal lines back to
  the Pico.

---

## 6. Quick-reference table (silk-screen ↔ silk-screen)

The exact list you'll be staring at while plugging in jumpers:

| Pico (silk)        | ESP32 (silk)     | INA169 (silk) |
| ------------------ | ---------------- | ------------- |
| `GP0`              | `RX0` / `GPIO3`  |               |
| `GP1`              | `TX0` / `GPIO1`  |               |
| `GP2`              | `GPIO5`          |               |
| `GP3`              | `EN`             |               |
| `GP4`              | `GPIO0`          |               |
| `GND` (pin 38)     | `GND`            |               |
| `VBUS` (pin 40)    |                  | `Vin+`        |
| `3V3` (pin 36)     |                  | `Vcc`         |
| `AGND` (pin 33)    |                  | `GND`         |
| `GP26` / `ADC0`    |                  | `OUT` / `Vout`|
|                    | `5V` / `VIN`     | `Vin−`        |

---

## 7. First-power sanity sequence

Before plugging the Pico's USB in for the first time, with a multimeter
in continuity mode:

1. **Pico GND ↔ ESP32 GND ↔ INA169 GND** — all three pairs should beep.
   If any pair is silent, your common ground isn't actually common; fix it
   before powering anything.
2. **Pico VBUS ↔ Pico GND** — silent. (A beep means a short somewhere on
   the 5 V leg — probably wire 7 or wire 11 backwards.)
3. **Pico 3V3 ↔ Pico GND** — silent. (A beep means wire 8 is shorted.)
4. **INA169 Vin+ ↔ INA169 Vin−** — silent at idle. Beeps only because of
   the sense resistor's tiny resistance, which is below the meter's
   threshold; if it actively beeps continuously, `Rs` is shorted.
5. **ESP32 5V / VIN ↔ INA169 Vin−** — beep. Same node.
6. **Pico VBUS ↔ INA169 Vin+** — beep. Same node.

Then plug in only the Pico's USB. Expected readings:

- `INA169 Vin+` ↔ `GND` ≈ 5.0 V
- `INA169 Vin−` ↔ `GND` ≈ 4.95 V (the small drop across `Rs` is the
  whole reason this circuit exists)
- ESP32 `3V3` ↔ `GND` ≈ 3.3 V (its onboard LDO is now sourced from `VIN`)
- INA169 `OUT` ↔ AGND: a small positive DC voltage, jitters slightly when
  the ESP32 is doing work, flat when it's idle. This is the signal the ML
  classifier consumes.

If any of those is off by more than ~5 %, **unplug immediately** and
re-check the wire table.
