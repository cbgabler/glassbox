# GlassBox — Detection Capabilities, File Inventory, and Proposed Layout

A reference doc that answers three concrete questions about the current state of
the repo:

1. **What can the hardware actually detect today?** (after the v2 expansion)
2. **What does each file in the repo do?**
3. **How could the codebase be reorganized so the directories enforce the
   intended dependency graph?**

---

## 1. What the GlassBox hardware can now detect

The capability matrix is split into the original side-channel detectors (which
have always worked) and the v2 additions that landed alongside this doc.

### Side-channel detection (pre-existing, unchanged)

1. **Timing leaks (cycles & micros).** Welch's t-test on per-call cycle counts
   and `esp_timer` microseconds catches any function whose execution time
   depends on the secret — classic strcmp / memcmp / branch-on-secret leaks.
2. **Power leaks (Pico INA169 ADC).** Per-sample TVLA on the 256-sample power
   trace catches leaks that don't move the cycle counter (Hamming-weight
   effects, asymmetric dual-rail logic, secret-indexed table accesses).
3. **Variance / masked-implementation leaks (higher-order TVLA).** Second-order
   TVLA on cycles + power catches code whose mean is secret-independent but
   whose *variance* still depends on the secret — i.e. half-broken masking.
4. **Live ML classifier + automatic quarantine.** A trained sklearn classifier
   scores every trace; after a configurable streak of "leak" verdicts the Pico
   drives the kill lines and persists the quarantine flag in NVS.

### New capabilities (v2 — added in this round of work)

5. **Memory-corruption detection (no AddressSanitizer required).** 32-byte
   sentinel patterns bracket the input and output buffers, plus a stack canary
   uint32, all checked after every call; any byte that changed makes the
   firmware emit `MEMVIOL <kind> overrun=<n>` and the runner classifies it as
   a `memory_corruption` finding (CRITICAL).
6. **Crash / hang / panic capture.** The runner's new `request_trace_safe()`
   converts firmware faults into `TraceFailure` records — `timeout`, `panic`,
   `err_response`, `memory`, `wdt_reset` — so a single bad input no longer
   kills an N-thousand-trace campaign and crashes turn into named, counted
   findings.
7. **Output non-determinism check.** With `--check-determinism`, ~5 % of
   inputs are immediately rerun and outputs compared; any disagreement is the
   signature of an uninitialized read, an interrupt-shared buffer, an
   accidental `esp_random()` call, or residue from the previous call.
8. **Length-oracle detection.** Per-group histograms of `out_len` plus a
   Welch-t when there are exactly two groups surface functions whose
   return-buffer length correlates with the secret. Always emitted (even when
   "pass") so the UI can show a green check.
9. **CPA key recovery on existing power traces.** Vectorized AES first-round
   S-box correlation with the Hamming-weight model upgrades the demo from
   "we detected leakage" to "here is the recovered AES key, byte by byte".
   Verified against synthetic traces (16/16 bytes recovered at rank 1).
10. **Pre-flash constant-time linter.** Six rules over `gb_target.cpp`
    catch branch-on-secret, variable-time comparators, secret-indexed tables,
    variable-time arithmetic, secret-printing debug logs, and yields-during-
    compute — *before* you waste 10 minutes flashing and sweeping. A light
    taint pass picks up `local = secret[i]` aliases. Verified to flag every
    leaky example target and clear every safe one.

Everything above feeds into the same polymorphic `findings[]` array in
`run_detail.json`, with the worst-severity finding driving the headline
verdict (`leak_detected | memory_corruption_detected | crash_detected |
non_determinism_detected | static_warning | key_recovered | safe`).

---

## 2. One-sentence inventory of every file

### Top level

| File | What it is |
|---|---|
| `glassbox/README.md` | Repo entry point: hardware bring-up, wiring, top-level architecture, and pitch. |

### `glassbox/docs/`

| File | What it is |
|---|---|
| `agent-repo-audit-guide.md` | How to drive `repo_audit.py` from an AI agent to scan an arbitrary GitHub repo. |
| `example-DRY-ATTACKER.md` | Transcript of `live_attacker.py --no-fire` (classifier loop, no quarantine actually fires). |
| `example-QUAR-ATTACKER.md` | Transcript of `live_attacker.py` end-to-end including the closed-loop quarantine. |
| `example-runner-test.md` | Transcript of `runner.py` collecting a real parquet from the pod. |
| `example-smoke-test.md` | Transcript of `smoketest.py` round-tripping three RUN commands. |
| `example-tvla-eval.md` | Transcript of `tvla_eval.py` rendering a leak verdict from a parquet. |
| `plain.md` | Plain-English elevator pitch: what GlassBox is and why it matters. |
| `platform-readme.md` | Cloud platform / dashboard architecture (the longer-term product vision). |
| `quarantine-demo-guide.md` | Step-by-step demo script for the closed-loop quarantine showcase. |
| `test-arbitrary-code-guide.md` | How to drop a user `gb_target.cpp` into the harness and test it. |
| `tinyml-guide.md` | TinyML validation guide — how the on-device classifier was trained and ported. |
| `capabilities-and-layout.md` | This file. |

### `glassbox/esp/` (ESP32 victim firmware)

| File | What it is |
|---|---|
| `harness/harness.ino` | The victim firmware: UART RUN/RES2 protocol, four timing channels, GPIO5 trigger, NVS-backed quarantine, and (v2) shadow-sentinel memory guards + panic handler. |
| `harness/gb_target.h` | Public interface for user-supplied functions under test (`gb_target_call` / `gb_target_name`). |
| `harness/gb_target.cpp` | Default no-op stub so the sketch always builds; users overwrite this with their function. |
| `pingpong.ino` | Standalone UART ping-pong sketch used during initial hardware bring-up. |

### `glassbox/raspberry/` (Pico monitor firmware)

| File | What it is |
|---|---|
| `harness/harness.ino` | The Pico monitor: USB CDC parser, GPIO5 trigger sampling at ~250 kHz off the INA169, kill-line driver, and TRACE-line emitter. |
| `pingpong.ino` | Pico-side counterpart of the bring-up ping-pong. |

### `glassbox/runner/` (host-side Python)

| File | What it is |
|---|---|
| `requirements.txt` | Pinned Python dependencies (numpy, pandas, pyserial, sklearn, joblib, pyarrow). |
| `runner.py` | Pod I/O primitives: open the Pico, send `RUN`, parse `RES/RES2/TRACE` (and v2 `PANIC/MEMVIOL`); also a strcmp byte sweep used by the demo. |
| `sweep_target.py` | Run a configurable input campaign against the user-target slot (fn_id=3) and write a parquet; v2 also collects crash / non-determinism / length-oracle data. |
| `smoketest.py` | Three-call hello-world: confirms the pod responds and the strcmp leak shows the expected delta. |
| `quarantine.py` | Helpers to send `STATUS / QUARANTINE / UNQUARANTINE` commands and read the chip's reply. |
| `tvla.py` | Pure-numpy TVLA math: Welch's t, higher-order centering, multi-channel report dataclasses + selftest. |
| `tvla_eval.py` | CLI that runs TVLA against a `runner.py`-shaped parquet (older fn_id/label schema). |
| `eval.py` | The user-facing analyze CLI for `sweep_target.py`-shaped parquets: TVLA + leak localization + ML opinion + (v2) CPA + linter merge + polymorphic `run_detail.json`. |
| `cpa.py` | (v2) Vectorized AES first-round S-box CPA with Hamming-weight model; recovers a 16-byte AES key from existing power traces. |
| `ct_lint.py` | (v2) Pre-flash constant-time linter: 6 regex rules + light taint propagation over `gb_target.cpp`. |
| `findings.py` | (v2) Polymorphic finding dataclass + builders + severity ladder + verdict derivation, shared by every detector. |
| `features.py` | Feature extraction from `(cycles, power)` traces for the ML classifier. |
| `classifier.py` | Sklearn RandomForest training pipeline used to produce `baseline.joblib`. |
| `synth.py` | Synthetic trace generator used to bootstrap classifier training before real traces existed. |
| `train_real.py` | Trains the classifier on real-pod traces using the same featurizer. |
| `live_classifier.py` | Runtime wrapper around the joblib model that produces per-call leak confidences. |
| `anomaly_detector.py` | Streak-based decision policy: turns a stream of per-call verdicts into a "fire quarantine now?" bit. |
| `live_attacker.py` | The end-to-end demo: Pico talks to ESP32, classifier scores every trace, anomaly detector fires `QUARANTINE` when the streak crosses threshold. |
| `orchestrator.py` | Stage-by-stage health runner of the whole pipeline; emits a structured `orchestrator_report.json`. |
| `repo_audit.py` | Scan an external git repo for likely-leaky C/C++ functions (host-side static pre-flight). |

### `glassbox/runner/targets/` (drop-in sample functions)

| File | What it is |
|---|---|
| `README.md` | How to copy any of these into `gb_target.cpp` and reflash. |
| `strcmp_leaky.cpp` / `strcmp_safe.cpp` | Naive vs constant-time string comparator pair. |
| `password_check_leaky.cpp` / `password_check_safe.cpp` | Stored-credential equality, leaky vs constant-time. |
| `lookup_table_leaky.cpp` / `lookup_table_safe.cpp` | Secret-indexed array read vs scan-and-mask. |
| `branch_on_secret_leaky.cpp` / `branch_on_secret_safe.cpp` | Per-bit conditional execution vs a branch-free version. |

### `glassbox/frontend/mocks/`

| File | What it is |
|---|---|
| `README.md` | Maps each mock JSON to the dashboard component it drives, plus the v2 findings guide. |
| `types.ts` | Canonical TypeScript schema: `Trace`, `TvlaReportPayload`, `RunDetailPayload`, `LiveStreamEvent`, plus (v2) the polymorphic `Finding` union. |
| `generate.py` | Deterministic mock-data generator (seeded numpy) — re-run after any schema change to refresh the JSON files. |
| `runs_index.json` | Leaderboard + recent-runs table for the dashboard home page. |
| `run_detail.json` | One whole run packet for `/runs/[id]`; v2 carries `findings[]` + `findings_summary`. |
| `byte_histogram.json` | Per-byte cycles distribution for the timing histogram chart. |
| `traces.json` | Eight raw 256-sample traces for the oscilloscope view. |
| `tvla_report.json` | ISO/IEC 17825 verdicts for the LeakGauge component. |
| `live_attack_stream.jsonl` | One JSON event per line — replayable feed for the live-attack page. |
| `quarantine_events.json` | State-machine timeline of victim transitions for the QuarantineLog. |
| `orchestrator_report.json` | Pipeline-health snapshot for the per-stage strip. |

---

## 3. Proposed cleanup of `glassbox/runner/`

The 18 `.py` files in one folder mix four different concerns: hardware I/O,
pure analysis, ML, and high-level pipeline glue. Splitting them by concern
(and keeping each module's *imports* enforcing the layering) makes the
dependency graph obvious and makes future "swap in a different ML / different
analysis / different I/O" changes cheap.

```
glassbox/
│
├── README.md
│
├── firmware/                          ◀── renamed from esp/ + raspberry/
│   ├── esp32/
│   │   ├── harness/
│   │   │   ├── harness.ino
│   │   │   ├── gb_target.h
│   │   │   └── gb_target.cpp
│   │   └── pingpong.ino
│   └── pico/
│       ├── harness/
│       │   └── harness.ino
│       └── pingpong.ino
│
├── runner/                            ◀── still the "host" side, now subdivided
│   │
│   ├── collect/                       (hardware I/O — talks to the pod)
│   │   ├── __init__.py
│   │   ├── pod.py                     ← runner.py (open_pod, request_trace*)
│   │   ├── sweep.py                   ← sweep_target.py
│   │   ├── smoketest.py
│   │   └── quarantine.py
│   │
│   ├── analyze/                       (pure: numpy in, findings out)
│   │   ├── __init__.py
│   │   ├── tvla.py                    ← tvla.py (math only)
│   │   ├── tvla_cli.py                ← tvla_eval.py (CLI)
│   │   ├── eval.py                    ← eval.py (top-level CLI)
│   │   ├── cpa.py
│   │   ├── ct_lint.py
│   │   └── anomaly.py                 ← anomaly_detector.py
│   │
│   ├── ml/                            (sklearn classifier + features)
│   │   ├── __init__.py
│   │   ├── features.py
│   │   ├── classifier.py
│   │   ├── live_classifier.py
│   │   ├── train.py                   ← train_real.py
│   │   └── synth.py
│   │
│   ├── pipeline/                      (orchestration & cross-cutting glue)
│   │   ├── __init__.py
│   │   ├── findings.py
│   │   ├── orchestrator.py
│   │   ├── live_attacker.py
│   │   └── repo_audit.py
│   │
│   ├── targets/                       (unchanged: drop-in gb_target.cpp examples)
│   │   ├── README.md
│   │   ├── strcmp_{leaky,safe}.cpp
│   │   ├── password_check_{leaky,safe}.cpp
│   │   ├── lookup_table_{leaky,safe}.cpp
│   │   └── branch_on_secret_{leaky,safe}.cpp
│   │
│   └── requirements.txt
│
├── frontend/
│   └── mocks/                         (unchanged — already cleanly scoped)
│       ├── README.md, types.ts, generate.py
│       └── *.json, *.jsonl
│
└── docs/
    ├── architecture/                  (the "why" — one-time reads)
    │   ├── platform-readme.md
    │   └── plain.md
    │
    ├── guides/                        (the "how" — task-oriented)
    │   ├── quarantine-demo-guide.md
    │   ├── test-arbitrary-code-guide.md
    │   ├── agent-repo-audit-guide.md
    │   ├── tinyml-guide.md
    │   └── capabilities-and-layout.md (this file)
    │
    └── examples/                      (transcripts of real runs)
        ├── example-smoke-test.md
        ├── example-runner-test.md
        ├── example-tvla-eval.md
        ├── example-DRY-ATTACKER.md
        └── example-QUAR-ATTACKER.md
```

### Layering rules implied by the diagram

```
collect/ ──┐
           ├──► pipeline/ ──► (top-level CLIs)
analyze/ ──┤              ▲
           │              │
ml/ ───────┘              │
                          │
        findings.py ◄─────┘   (everyone emits Finding objects)
```

- `analyze/` and `ml/` MUST NOT import from `collect/` (they're pure /
  pickled-data driven).
- `collect/` MUST NOT import from `analyze/` (it just produces parquets).
- `pipeline/` is allowed to import from anywhere — it's the wiring layer.
- `findings.py` is the only module everyone may import as a shared schema.

### Why this layout pays off

| Pain today | After cleanup |
|---|---|
| Adding a new analysis (e.g. mutual information) means editing alongside hardware I/O code | Drops in `analyze/`; untouched I/O |
| Hard to tell which modules need a serial port to import | `collect/` clearly contains all hardware-coupled code |
| ML experiments get tangled with TVLA experiments | `ml/` is a self-contained subpackage you can rip out and replace |
| Docs are 12 files in one flat folder; hard to find the right one | Three thematic subfolders match how readers think (architecture vs how-to vs example) |
| `esp/` and `raspberry/` aren't obviously firmware | A single `firmware/` parent makes the hardware-vs-host split visible at the top level |

### Migration plan (if/when you decide to do it)

1. `git mv` each Python module into its new home.
2. Add `__init__.py` files to make the subpackages importable.
3. Update `import` statements in roughly ten places — the dependency graph is
   shallow, so this is mostly mechanical.
4. Update `pyproject.toml` / `requirements.txt` discovery if the
   `orchestrator.py` entry point moves.
5. Update the example-transcripts in `docs/examples/` only if their command
   lines need new module paths (most transcripts use `python <script>.py`
   from the runner directory and would still work).
6. Update the top-level `README.md`'s **Repository Layout** section, which
   today is aspirational and out of sync with reality.

The one-time cost is small (probably under an hour); the long-term win is that
new contributors can guess where to put new code from the directory names
alone.
