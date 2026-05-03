# `backend/hardware/` — the GlassBox hardware pipeline

Everything in this folder exists to answer one question:

> **Given a C / C++ / Rust / Zig / asm function the user just dropped in,
> does it leak its secret over a measurable side channel when it runs on
> a real ESP32?**

The answer comes from a 3‑box rig (laptop ↔ Pico ↔ ESP32), a Python
runner that orchestrates the rig, and a small Go API
(`backend/hardwarego/hardware.go`) that exposes "scan this file" as an
HTTP endpoint to the agent.

---

## 1. The physical rig

```
     ┌──────────────┐  USB CDC      ┌──────────────┐    UART0     ┌──────────────┐
     │              │ ────────────▶ │              │ ───────────▶ │              │
     │   Laptop     │               │ Raspberry    │              │   ESP32      │
     │  (runner/)   │ ◀──────────── │ Pi  Pico     │ ◀─────────── │  (victim)    │
     │              │   RES2/TRACE  │  (monitor)   │     RES2     │              │
     └──────────────┘               └──────┬───────┘              └──────┬───────┘
                                           │ GP2 (trigger IN)            │ GPIO5 (trigger OUT)
                                           │ GP3 (kill EN, open-drain)   │ EN
                                           │ GP4 (kill BOOT, open-drain) │ GPIO0
                                           │ GP26/ADC0 ◀── INA169 OUT ── shunt on ESP32 VCC
                                           └─────────────────────────────┘
```

Two boards, three roles:

| Board   | Role | Source                                |
|---------|------|---------------------------------------|
| Laptop  | brains: install code, lint, drive runs, analyze traces, decide verdict | `runner/` |
| Pico    | I/O boundary: only thing on USB; flashes the ESP32, captures power traces, drives kill lines | `raspberry/harness/harness.ino` |
| ESP32   | victim: runs the function under test, emits cycles/micros/insns/branches counters | `esp/harness/harness.ino` (+ `gb_target.cpp`) |

Why a Pico in front of the ESP32?

1. **Single‑USB flashing.** The Pico can pretend to be a CP2102 USB↔UART
   chip by toggling `EN` / `GPIO0` in response to host DTR/RTS, so
   `arduino-cli` / `esptool` can flash the ESP32 through the same wires
   it uses to talk to the harness. Only the Pico's USB cable needs to
   be plugged in.
2. **Power sensing.** The INA169 high‑side current sensor is sampled by
   the Pico's 12‑bit ADC at ~4 µs/sample for 256 samples (~1 ms window),
   triggered by GPIO5 going HIGH on the ESP32 right before the function
   under test runs.
3. **Kill lines.** When the runner says `QUARANTINE`, the Pico drives
   the ESP32's `EN` and `GPIO0` low (open‑drain — software or FET),
   stopping the chip in hardware. The state is also persisted in NVS
   on the ESP32 so it survives a reboot.

⚠️  **Don't plug both USB cables in at once.** GPIO1/3 on the dev‑board
are tapped by the onboard USB‑to‑serial chip; the Pico would fight it
on the TX line. Use Pico USB for normal operation; ESP32 USB only for
direct serial debug with the Pico physically disconnected.

---

## 2. Folder layout

```
hardware/
├── esp/                 ← ESP32 (victim) firmware
│   ├── harness/
│   │   ├── harness.ino      Protocol parser, timed call site, mem-safety guards
│   │   ├── gb_target.h      C-ABI the user's function must implement
│   │   └── gb_target.cpp    Currently-installed user target (overwritten per scan)
│   └── test-scripts/
│       └── pingpong.ino     Bring-up sketch: confirms wires + serial work
│
├── raspberry/           ← Pico (monitor) firmware
│   ├── harness/
│   │   └── harness.ino      USB↔UART bridge, ADC trace capture, BRIDGE flashing,
│   │                        kill-line driver, soft quarantine
│   └── test-scripts/
│       └── pingpong.ino     Same bring-up role, on the Pico side
│
├── runner/              ← The Python brain on the laptop
│   ├── scan_target.py        ⭐ Per-target orchestrator (THE entry point Go calls)
│   ├── auto_flash.py            arduino-cli/platformio launcher + Pico BRIDGE driver
│   ├── smoketest_probe.py       Confirms the freshly-flashed sketch is "ours"
│   ├── collect/
│   │   ├── pod.py               Pyserial wrapper: speaks the wire protocol to the Pico
│   │   └── traces.py            Runs a TVLA campaign → DataFrame of N traces
│   ├── analyze/
│   │   ├── ct_lint.py           Pre-flash regex linter for obvious CT mistakes
│   │   ├── tvla.py              Welch's t-test (ISO/IEC 17825 threshold = 4.5) two pop t test with possibly diverse pops
│   │   ├── cpa.py               Correlation Power Analysis: AES-128 key recovery
│   │   ├── anomaly.py           Streaming "N-in-a-row leak verdicts → quarantine"
│   │   ├── tvla_eval.py         Post-hoc TVLA report formatter
│   │   └── eval.py              Standalone analyze-from-parquet CLI
│   ├── ml/
│   │   ├── features.py          Statistical/spectral features per trace
│   │   ├── classifier.py        sklearn RandomForest baseline (offline trainer)
│   │   ├── live_classifier.py   Hot-path wrapper for per-trace verdicts
│   │   ├── synth.py             Synthetic trace generator (model-based)
│   │   └── train.py             Trains the baseline classifier
│   ├── pipeline/
│   │   └── findings.py          Polymorphic Finding records (the report schema)
│   ├── targets/
│   │   ├── pkg/compile_target.py    C/C++/asm/Rust/Zig prep + FFI shim generation
│   │   ├── pkg/glassbox_check.py    Standalone CLI ("glassbox check <path>")
│   │   └── README.md                Catalogue of demo leaky/safe target pairs
│   └── requirements.txt
│
├── mocks/               ← Frontend fixtures matching the real report shapes
│   ├── README.md, types.ts          Schema docs for the dashboard
│   └── *.json / *.jsonl             Replayable run + trace data
│
└── tinyml/              ← Reserved for the on-Pico TFLite Micro classifier
```

The Go side (`backend/hardwarego/hardware.go`) is the only consumer of
this folder outside the laptop runner. It uses exactly two paths:

```go
relScanTarget  = "glassbox/backend/hardware/runner/scan_target.py"
relGbTargetCpp = "glassbox/backend/hardware/esp/harness/gb_target.cpp"
relRunnerVenv  = "glassbox/backend/hardware/runner/.venv/bin/python"
```

— it shells out to `scan_target.py` per source file and parses the JSON
report. Nothing else in the runner is part of its public contract.

---

## 3. The wire protocol

All three boundaries speak line‑oriented ASCII over serial at 115200 baud.

### 3a. Laptop ↔ Pico (USB CDC)

Outbound (laptop → Pico):

```
RUN <fn_id> <hex_input>\n        request a trace
QUARANTINE\n                     soft-lock + assert kill lines
UNQUARANTINE\n                   clear the soft lock
STATUS\n                         liveness probe
BRIDGE [seconds]\n               enter USB↔UART0 passthrough (auto_flash only)
```

Inbound (Pico → laptop):

```
READY harness v1\n               boot banner
RES2 <c> <us> <i> <b> <hex_out>\n cycles, micros, insns, branches, output
TRACE s0,s1,...,s255\n            256 ADC samples captured during trigger HIGH
ACK quarantined / unquarantined / bridge <seconds>\n
STATUS running | quarantined\n
ERR <reason>\n                    bad command or hex
PANIC <pc> <reason>\n             ESP32 panic handler tripped
MEMVIOL <kind> overrun=<n>\n      shadow-sentinel / stack-canary fired in firmware
```

### 3b. Pico ↔ ESP32 (UART0, GPIO1/3)

Same protocol, minus `TRACE` (the Pico is the one capturing power; the
ESP32 only emits cycle counters). Built‑in `fn_id`s in the ESP32 harness:

```
0  noop              calibration baseline
1  strcmp_naive      built-in early-return comparator (timing-leak demo)
2  strcmp_safe       built-in constant-time comparator (safe demo)
3  user_target       dispatches to gb_target_call() — the ONLY id the scanner uses
```

### 3c. The user contract — `gb_target.h`

Every user function under test must export:

```cpp
extern "C" int  gb_target_call(const uint8_t* secret, size_t secret_len,
                               uint8_t* out, size_t* out_len);
extern "C" const char* gb_target_name(void);
```

Constraints, enforced in firmware where possible:

* Pure: no `Serial`, no `delay()`, no `yield()`, no flash writes, no
  WiFi. Anything that yields corrupts the trace.
* Bounded: input ≤ 64 bytes, output ≤ 64 bytes, runtime < ~100 ms.
* Self‑contained: must build into a single sketch with the harness.

Memory safety is checked at runtime: the ESP32 harness brackets the
input/output buffers with 32‑byte sentinel guard regions and a stack
canary, then verifies them after every call. A trip emits `MEMVIOL …`
instead of `RES2 …` and the runner records a `memory_corruption` finding.

---

## 4. The end‑to‑end pipeline

`scan_target.py` is the single entry point. The Go API runs it once per
source file in the cloned repo:

```bash
python -u scan_target.py <abs/path/to.cpp> --pico-port <p> --n 500 --out -
```

Each invocation runs the following nine stages, emits exactly one JSON
`TargetReport` to stdout, and exits 0 (even on `leak_detected` —
non‑zero exit means the *scanner itself* broke).

```
                                       ┌──────────────────────────────────────┐
   src.cpp ──▶ 1. install ─────────────│ esp/harness/gb_target.cpp (overwrite)│
                                       └──────────────────────────────────────┘
                                                    │
                              2. ct_lint  (regex-based; ~10 ms; no hardware)
                              ─ rule hits become StaticFindings
                              ─ also extracts a static reference constant
                                 (campaign hint for stage 4 "auto" mode)
                                                    │
                              3. flash    arduino-cli or platformio,
                                          via auto_flash.py + Pico BRIDGE mode,
                                          then verify the new firmware boots
                                                    │
                              4. collect  open the Pico (collect.pod.Pod),
                                          pick a Campaign:
                                            auto + ref hint → match_vs_random
                                            otherwise        → random_vs_zero
                                          run N traces per group (default 500),
                                          stream RES2 + TRACE into a DataFrame.
                                          Per-trace failures → CrashFindings.
                                                    │
                              5. tvla     Welch's t-test on cycles, micros,
                                          insns, branches, AND every sample of
                                          power[]. |t| > 4.5 ⇒ TVLAFinding.
                                                    │
                              6. cpa      best-effort AES-128 key recovery on
                                          the random group. Per-byte rank +
                                          best guess. → CPAFinding.
                                                    │
                              7. safety   if ct_lint flagged a comparator AND
                                          we ran random_vs_zero AND every TVLA
                                          channel returned 'pass', emit a
                                          MEDIUM CT_AUTO inconclusive finding
                                          so we can't silently false-negative.
                                                    │
                              8. merge    findings → TargetReport rollup
                                          (worst severity, verdict, stage timings)
                                                    │
                              9. emit     one-line JSON to stdout, exit 0
```

`pipeline/findings.py` defines the canonical `Finding` envelope and the
following discriminated union (the same shape the frontend mocks render):

| `type`                | What it represents                                    |
|-----------------------|-------------------------------------------------------|
| `tvla`                | Welch t-test exceeded 4.5 on some channel             |
| `crash`               | RUN call timed out / panicked / errored               |
| `non_determinism`     | Same input → different output across reruns           |
| `length_oracle`       | `out_len` correlates with the secret                  |
| `memory_corruption`   | `MEMVIOL` from the ESP32's runtime guards             |
| `static`              | `ct_lint.py` flagged a constant‑time anti‑pattern     |
| `cpa_key_recovery`    | CPA recovered (or attempted to recover) an AES byte   |

Severity ladder: `CRITICAL > HIGH > MEDIUM > LOW > INFO > pass`.
The headline `verdict` is driven by the worst finding via `TYPE_TO_VERDICT`.

---

## 5. How an audit looks from outside

From the agent's perspective (via `backend/hardwarego/hardware.go`):

```
POST /hardware/audit { repo_root }     ← list_hardware_targets first if you're polite
  ↓
hardwarego walks the repo, finds files that already define gb_target_call
  ↓ (one goroutine per file)
exec: python -u scan_target.py <file> --pico-port <auto> --n 500 --out -
  ↓
parses TargetReport JSON from stdout
  ↓
GET /hardware/audit/<id>               returns merged findings + per-stage timings
```

If a repo has no native `gb_target_call(...)` files, the agent calls
`register_synthetic_target` (see `serverconfigs/hardwareconfig.yaml`) —
the server reads the user's source, picks a wrapper template
(`bytes_len`, `comparator_len`, or `harness_native`), and writes a
generated wrapper into `<repo>/__glassbox_synthetic__/<name>.cpp` so the
scanner picks it up on the next pass. The synthetic dir is wiped at the
end of every audit.

---

## 6. Bringing up a fresh rig

1. **Wire it.** Follow the diagram in section 1 (and the comment header
   of `esp/harness/harness.ino` for v3 pin assignments — they were
   moved from GPIO16/17 to GPIO1/3 to enable single‑USB flashing).
2. **Flash both harnesses once, manually.** Plug the ESP32 USB cable in,
   flash `esp/harness/harness.ino`, unplug. Plug the Pico USB cable in,
   flash `raspberry/harness/harness.ino`. From here on the Pico USB is
   the only cable you need.
3. **Set up the runner venv:**
   ```bash
   cd glassbox/backend/hardware/runner
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
4. **Smoketest:**
   ```bash
   .venv/bin/python smoketest_probe.py --port <pico-cdc-port>
   ```
   Expects `PASS` on every line.
5. **Run the demo:**
   ```bash
   cp ../esp/harness/gb_target.cpp /tmp/safe_demo.cpp     # already constant-time
   .venv/bin/python scan_target.py /tmp/safe_demo.cpp \
       --pico-port <pico-cdc-port> --n 500 --out report.json
   cat report.json | python -m json.tool
   ```
   Verdict should be `safe`. Then drop in `targets/strcmp_leaky.cpp` and
   re‑run; verdict should be `leak_detected` with a TVLA finding on the
   `cycles` channel (and usually `power` too).

---

## 7. Where things live (cheat sheet)

| You want to…                                                          | Edit                                                                       |
|-----------------------------------------------------------------------|----------------------------------------------------------------------------|
| Add a new constant‑time linter rule                                   | `runner/analyze/ct_lint.py` (`_RULES` table)                               |
| Add a new finding type                                                | `runner/pipeline/findings.py` + `mocks/types.ts` (keep schemas in sync)    |
| Add a new TVLA campaign (input distribution)                          | `runner/collect/traces.py` (subclass `Campaign`)                           |
| Support a new source language                                         | `runner/targets/pkg/compile_target.py`                                     |
| Change the wire protocol                                              | `esp/harness/harness.ino` AND `raspberry/harness/harness.ino` AND `runner/collect/pod.py` (all three must agree) |
| Move a pin                                                            | Both `.ino` files' header comments **and** the `static const` pin defs    |
| Tune leak sensitivity                                                 | `TVLA_THRESHOLD` in `runner/analyze/tvla.py`; `DEFAULT_STREAK` / `DEFAULT_LEAK_THRESHOLD` in `runner/analyze/anomaly.py` |
| Change the Go ↔ Python contract                                       | `backend/hardwarego/hardware.go` (`relScanTarget`, JSON parse) **and** the JSON shape in `runner/pipeline/findings.py` |

---

## 8. Roadmap — where the stack could go

Grouped by layer, ordered loosely from "obvious next step" to "research
project." Most of these are gestured at by `TODO` comments, no‑op
stubs, or empty folders already in the tree; a few are net‑new.

### 8a. Firmware (ESP32 + Pico)

* **Working PMU.** The LX6 `read_pm0()` / `read_pm1()` in
  `esp/harness/harness.ino` are no‑ops (return `0`), so two of the four
  TVLA channels (`insns`, `branches`) are flat‑lined. The fix is to
  port the harness to **ESP32‑S3 (Xtensa LX7)**, which has a working
  PMU. The runner already treats flat channels gracefully — it'll just
  start emitting real verdicts on those channels once the firmware
  fills them in.
* **More victim chips.** Adding STM32, nRF52, or RISC‑V (ESP32‑C3,
  CH32V) victims means swapping `setup_hpc()` / `read_pmN()` /
  `get_ccount()` and the panic handler. The wire protocol stays the
  same, so the Pico and laptop don't change.
* **EM probe channel.** `mocks/README.md` already calls this out: a
  near‑field H‑field probe into a second Pico ADC pin (or an external
  ADC over SPI) gives a power‑independent leak signal. The schema field
  for `power[]` becomes `channels: { power: [...], em: [...] }` and the
  TVLA loop iterates over channels.
* **Source‑level mem‑safety hardening.** Move the harness build to
  PlatformIO (Arduino IDE can't enable these flags cleanly):
  `-fstack-protector-strong`, `CONFIG_HEAP_POISONING_COMPREHENSIVE=1`,
  `CONFIG_HEAP_USE_HOOKS=1`. Today the `MEMVIOL` path catches the most
  common failures via shadow sentinels + a single uint32 stack canary,
  but compiler‑driven coverage is strictly better.
* **Real panic capture.** `gb_panic_emit()` only fires on clean
  shutdowns; a true panic jumps into IDF's `esp_panic_handler()` first.
  Override it to dump real `pc` + `reason` over UART before reset.
* **Tamper detection on the Pico.** A second ADC channel watching for
  EM probe coils, voltage glitch ringing, or unusual current spikes
  could feed a passive "someone is attacking us right now" event into
  the live stream — distinct from "the function under test leaks."

### 8b. Pico + on‑device ML (`tinyml/`)

* **Live classifier on the Pico.** The empty `tinyml/` folder and the
  comment in `runner/ml/live_classifier.py` ("when the on‑Pico TFLite
  Micro classifier ships, this file becomes the shim that just polls
  the Pico for verdicts") are the same plan. Compile the trained
  RandomForest into a TFLite Micro model (or a hand‑rolled decision
  tree), flash it to the Pico, and have it emit `VERDICT <label>
  <conf>` lines per `RUN`. The laptop is then optional for live
  monitoring — important for "deploy this in a customer DC" mode.
* **Fault‑injection mode.** The Pico already drives `EN` and `GPIO0` —
  with one more GPIO into a transistor on the ESP32's VCC rail it can
  do **voltage glitching**, and with a tuned coil on a PWM pin it can
  do **EM glitching**. Active fault injection turns GlassBox from "find
  passive leaks" into "find places the chip skips a `compare` under
  glitch." Schema‑wise this is a new finding type (`fault_skip`) and a
  new campaign (`glitch_sweep`).
* **Standalone enclosure firmware.** Today the Pico boots into "wait
  for `RUN`" mode. A second mode could continuously monitor a
  customer‑deployed ESP32 and only phone home (over WiFi via a third
  ESP32, or over the Pico's PIO‑bit‑banged Ethernet) when the
  classifier fires `> N` consecutive leaks.

### 8c. Runner — collection (`runner/collect/`)

* **Adaptive sampling.** Today `--n 500` is fixed per group. A smarter
  campaign would start at N=100, check whether `|t|` is near the 4.5
  threshold or already clearly in/out, and either stop early or push
  N higher. Per‑repo wall time drops from O(files × 500) to O(files ×
  ~150) on average.
* **Multi‑pod parallelism.** `runs_index.fleet` already has a slot for
  `pod_id[]`. A pod pool would let the Go API shard files across N
  Picos and merge reports. The bottleneck today is one repo = one Pico
  = one file at a time.
* **Higher‑bandwidth trace transport.** TRACE lines as comma‑separated
  ASCII over USB CDC are simple but ~5× slower than they need to be.
  Switching to length‑prefixed binary frames (or USB bulk endpoints)
  would let `TRACE_LEN` grow from 256 to a few thousand without
  blowing the per‑call budget.

### 8d. Runner — analysis (`runner/analyze/`)

* **CPA upgrades.** `cpa.py` is hard‑coded to AES‑128 first‑round
  S‑box and the Hamming‑weight model. Wishlist (the file's own TODOs):
  last‑round attack (`mode="decrypt"`), other key sizes, Hamming‑
  distance model for chips with strong dual‑rail logic, and template
  attacks (collect a profiling set on a known‑key device, then attack
  with far fewer traces).
* **Mutual‑information analysis.** TVLA is a univariate two‑sample
  test; MI catches multivariate leaks (e.g. "the joint distribution of
  cycles and `power[42]` depends on the secret" even when neither
  alone does). Plug into `analyze_traces` as another channel.
* **DPA + higher‑order TVLA.** `tvla.py` already does second‑order;
  pre‑computed difference‑of‑means and third‑order (masking attack)
  variants are the natural extensions.
* **Smarter `ct_lint`.** The regex linter is intentionally
  conservative ("false positives are MUCH cheaper than false
  negatives"), but a libclang AST pass would catch the cases where a
  comparator hides behind a `template` or a macro. Land it as
  `analyze/ct_lint_clang.py` running alongside the regex pass; merge
  the findings via `findings.merge`.
* **Symbolic execution sidecar.** For functions ct_lint flags as
  comparator‑shaped, a tiny SMT‑backed pass (e.g. driven by KLEE or
  Binsec) can answer "is there an execution path whose length depends
  on the secret?" definitively, not statistically. Slow, so opt‑in per
  finding.

### 8e. Runner — orchestration & outputs

* **Differential mode.** Two `RunDetailPayload`s side by side: "this
  PR introduced a leak in `password_check` that wasn't in `main`."
  `mocks/README.md` already plans for it; only the diff renderer is
  missing.
* **Standards‑aligned reports.** Today the report is GlassBox's own
  schema. Generators for FIPS 140‑3 ISO/IEC 17825 evidence, Common
  Criteria SOG‑IS templates, and a CycloneDX‑style "side‑channel
  bill of materials" would unlock pre‑sales conversations.
* **GitHub App.** The runner already produces a PR‑comment block.
  Wrap it in a real GitHub App so installing GlassBox on a repo
  auto‑attaches reports to PRs that touch crypto code (detected via
  `ct_lint` matching on the diff).

### 8f. Languages + scaffolding (`runner/targets/`)

* **End‑to‑end Rust / Zig.** `compile_target.py` generates the project
  skeleton + C++ FFI shim today but stops there ("we don't try to
  invoke the Rust/Zig toolchain ourselves"). Toolchain detection per
  host OS plus a `--build` flag would close the loop.
* **More wrapper shapes for `register_synthetic_target`.** Today the
  server understands `bytes_len`, `comparator_len`, and
  `harness_native`. Useful next ones: stream‑oriented HMAC/MAC
  (`update`/`finalize` pair), AEAD (`encrypt`/`decrypt` with nonce +
  AD + tag), key‑derivation (`derive(secret, salt) -> key`).
* **Auto‑pick candidate functions from a repo.** The agent currently
  reads `.cpp/.c` files itself and asks `register_synthetic_target`.
  A small "candidate scorer" (cyclomatic complexity + ct_lint hits +
  symbol‑name heuristics like `*compare*`, `*verify*`, `*decrypt*`)
  could surface the top‑K functions automatically and let the agent
  just register them.

### 8g. Backend (`hardwarego` / cloud)

* **Pod farm as a service.** Most users won't have a Pico + ESP32 on
  their desk. Ship the rig in a 1U enclosure, run the Go API in front
  of a queue, and let people scan repos against a shared pool. The
  schema and protocol don't change; only `_resolve_pico_port` becomes
  "lease one from the pool."
* **Multi‑tenancy + auth.** Today the Go API trusts whatever asks. To
  ship the cloud version we need accounts, per‑user rate limits, and
  tenant isolation on the synthetic‑wrapper directory.
* **Persistent run history.** `mocks/runs_index.json` shows the shape
  of a leaderboard / recent‑runs list, but the live API doesn't
  persist them. A small Postgres schema (`runs`, `targets`,
  `findings`) plus an S3 bucket for parquet trace blobs unlocks the
  whole frontend.
* **Stream the audit live.** The frontend mocks already define a
  `live_attack_stream.jsonl` shape. A WebSocket from `hardwarego`
  forwarding `RES2`/`TRACE`/`VERDICT` events as they happen lets the
  dashboard render an oscilloscope view in real time.

### 8h. Frontend / agent UX

* **Live oscilloscope view.** Pair with the WebSocket above:
  `<LiveOscilloscope>` already exists in the mocks; wiring it to the
  real stream is mostly plumbing.
* **"Why is this leaking?" overlays.** Highlight the source line(s)
  CPA / TVLA fingerprints land on, side‑by‑side with the leaky vs
  safe diff (we have both pairs in `runner/targets/`). Closes the
  loop from "you have a leak" to "fix it like this."
* **Agent‑authored fix PRs.** The agent already classifies findings
  via `ct_lint` rule IDs; mapping each rule to a constant‑time
  rewrite template (XOR‑accumulate for `CT002`, branchless select
  for `CT001`, etc.) would let the agent open a follow‑up PR with
  the proposed fix and a re‑audit attached.

### 8i. Cross‑cutting

* **Reproducibility.** Pin the `arduino-cli` core + library versions
  in a lockfile so two runs of the same source produce
  bit‑identical firmware. Side‑channel analysis is sensitive to
  compiler output; today an upstream toolchain bump can silently
  shift baselines.
* **Calibration pass.** `pico_sample_period_us ≈ 4` and
  `ESP32_CPU_HZ = 240e6` are hard‑coded. A boot‑time calibration
  step ("send 1000 noops, measure") would auto‑fill both and write
  them into the report so cross‑pod comparisons are honest.
* **Secret rotation in the harness.** The demo `SECRET[] = "hunter2!"`
  is baked into firmware. For real audits we want the secret
  randomized per boot (recorded in the report) so leak findings
  generalize past the demo key.
