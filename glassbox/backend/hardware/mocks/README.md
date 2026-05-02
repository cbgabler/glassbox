# GlassBox frontend mock data

Drop-in JSON / NDJSON fixtures so the dashboard can be built before the
sensor pod or backend exist. Every shape here matches what the cloud API
will eventually return — when the real backend comes online you swap the
fetch URL and nothing else.

## At a glance

| File | Drives | Shape |
|---|---|---|
| `runs_index.json` | Home page leaderboard + recent-runs table | `RunsIndexPayload` |
| `run_detail.json` | `/runs/[id]` page header, summary, GitHub comment block | `RunDetailPayload` |
| `traces.json` | `PowerTrace.tsx` oscilloscope (8 traces × 256 samples) | `TracesPayload` |
| `byte_histogram.json` | `TimingHistogram.tsx` — the **money chart**, cycles-vs-input-byte for all 256 candidates | `ByteHistogramPayload` |
| `tvla_report.json` | `LeakGauge.tsx` per-channel verdicts + severity legend | `TvlaReportPayload` |
| `live_attack_stream.jsonl` | The live "watch GlassBox catch the attacker" view; replay one line at a time to simulate the WebSocket | `LiveStreamEvent[]` |
| `quarantine_events.json` | `QuarantineLog.tsx` state-machine timeline | `QuarantineEventsPayload` |
| `orchestrator_report.json` | Pipeline-health page (stage-by-stage pass/fail strip) | `OrchestratorReport` |

All TypeScript types live in [`types.ts`](./types.ts). Import them and
your fetches are typed end-to-end:

```ts
import type { RunsIndexPayload, RunDetailPayload } from "../mocks/types";

const data: RunsIndexPayload = await fetch("/mocks/runs_index.json").then(r => r.json());
```

## How to use the mocks during development

The simplest setup: copy this whole folder into `frontend/public/mocks/`
and fetch the files at `/mocks/<name>.json`. Vercel/Next will serve them
as static assets and you can swap to real API URLs later.

```ts
const MOCK = process.env.NEXT_PUBLIC_USE_MOCKS === "1";
const url  = MOCK ? "/mocks/runs_index.json" : "/api/runs";
```

For the live stream:

```ts
async function* mockStream() {
  const lines = (await fetch("/mocks/live_attack_stream.jsonl").then(r => r.text()))
                .trim().split("\n");
  for (const line of lines) {
    const ev = JSON.parse(line) as LiveStreamEvent;
    yield ev;
    await new Promise(r => setTimeout(r, 80)); // pace it so the demo is watchable
  }
}
```

When the real backend is up, swap that for `new WebSocket("wss://api.../runs/<id>/stream")`
and `JSON.parse(msg.data)`. The event shapes are identical.

## Suggested component → file mapping

```
┌─────────────── HOME PAGE ────────────────┐
│ <Leaderboard data={runs_index.leaderboard}/>          (runs_index.json)
│ <RecentRuns   data={runs_index.runs}/>                (runs_index.json)
│ <FleetBadge   data={runs_index.fleet}/>               (runs_index.json)
└──────────────────────────────────────────┘

┌─────────── /runs/[id] PAGE ──────────────┐
│ <RunHeader        data={run_detail}/>                 (run_detail.json)
│ <LeakGauge        data={run_detail.tvla_summary}/>    (run_detail.json)
│ <TimingHistogram  data={byte_histogram}/>             (byte_histogram.json)
│ <PowerTrace       data={traces}/>                     (traces.json)
│ <QuarantineLog    data={quarantine_events}/>          (quarantine_events.json)
│ <PipelineStrip    data={orchestrator_report}/>        (orchestrator_report.json)
│ <GitHubComment    data={run_detail.github}/>          (run_detail.json)
└──────────────────────────────────────────┘

┌─────────────── /live PAGE ───────────────┐
│ <LiveOscilloscope events={liveStream}/>               (live_attack_stream.jsonl)
│ <StreakMeter      events={liveStream}/>               (same)
│ <KillLineIndicator events={liveStream}/>              (same)
└──────────────────────────────────────────┘
```

## Knobs

The Pico hardware ships fixed `TRACE_LEN = 256` and `SAMPLE_PERIOD_US ≈ 4`,
so every power array is 256 entries long covering ~1 ms wall-clock. Your
oscilloscope X-axis can either show sample index (`0..255`) or convert to
microseconds with `i * sample_period_us`.

The 12-bit ADC means power values are integers in `[0, 4095]`. To convert
to amperes:

```ts
const V_REF = 3.3;          // Pico ADC reference
const ADC_MAX = 4095;        // 2^12 - 1
const INA169_GAIN = 10;      // R_L=10k, R_S=0.1Ω on the TekBots breakout
const R_S = 0.1;             // sense resistor in ohms

function adcToAmps(adc: number): number {
  const v_out = (adc / ADC_MAX) * V_REF;
  return v_out / (INA169_GAIN * R_S);   // amps
}
```

(For the demo it's totally fine to plot raw ADC counts — the *shape* is
what tells the leak story, not the absolute current.)

## Regenerating the data

Everything was emitted by [`generate.py`](./generate.py) from a fixed
seed (`np.random.default_rng(0)`). To tweak something — e.g. give the
attacker more byte positions, slow the live stream, change the secret —
edit the constants at the top of `generate.py` and re-run:

```bash
cd glassbox/frontend/mocks
python3 generate.py
```

The cycle counts come from a model of the **actual** ESP32 firmware
behaviour: `fn_strcmp_naive` returns at the first mismatch (one ladder
step per matched prefix byte), `fn_strcmp_safe` is constant-time. The
TVLA verdicts and the orchestrator report mirror real outputs that the
runner has produced (see `glassbox/docs/example-tvla-eval.md` and
`glassbox/docs/example-runner-test.md` for the wire-truth versions).

## v2: polymorphic findings

`run_detail.json` now also carries an optional `findings: Finding[]` array
plus a `findings_summary` block. A `Finding` is a discriminated union over
`type`:

| `type`                | What it represents |
|---|---|
| `tvla`                | Existing side-channel leak (cycles / micros / power) |
| `crash`               | Function timed out / panicked / returned ERR during the sweep |
| `non_determinism`     | Same input -> different output across reruns (uninitialized memory, IRQ contamination, accidental TRNG use) |
| `length_oracle`       | `out_len` correlates with secret in unintended ways |
| `memory_corruption`   | Shadow sentinel / stack canary / heap poisoning fired in firmware |
| `static`              | Pre-flash constant-time linter (`runner/ct_lint.py`) flagged a pattern in `gb_target.cpp` |
| `cpa_key_recovery`    | Correlation power analysis recovered (or attempted to recover) an AES key |

Every finding has a stable envelope (`id`, `type`, `severity`, `title`,
`detail`, optional `remediation`, optional `source`) and a `data` blob whose
shape is fixed by `type` -- see [`types.ts`](./types.ts) for the exact union.

`runs_index.json` runs may also carry a `findings_summary` for the recent-
runs table to render badge counts. The legacy `verdict` / `leak_channel` /
`leak_severity` fields stay populated for v1 consumers, but the headline
verdict is now driven by the worst finding (`memory_corruption_detected`,
`crash_detected`, `non_determinism_detected`, `static_warning`,
`key_recovered`, ...). New verdict literals are additive in `Verdict`.

Implementation: every detector in `runner/` (TVLA in `eval.py`, crash /
non-determinism / length oracle in `sweep_target.collect`, memory in
firmware via `MEMVIOL`, static in `ct_lint.py`, CPA in `cpa.py`) emits
`Finding` objects via the builders in `runner/findings.py`. The pipeline
merges them into one list and writes `run_detail.json`.

## What's intentionally not here yet

- **EM-channel traces** (Tier-2 stretch in the README; same shape as
  `power[]` so the existing PowerTrace component will Just Work).
- **Multi-pod fleet view.** `runs_index.fleet` already has the field;
  populate it with multiple `pod_id` strings when the fleet is real.
- **Differential-test pages** (compare two `RunDetailPayload`s side by
  side). When the GitHub bot supports before/after PRs, the same shapes
  are reused; you just render two of them.
- **Live INA169 amps**. Today everything's raw ADC counts. The conversion
  helper above is enough for a "Y-axis units" toggle.

If you need a shape that isn't here, copy the closest existing object,
add it to `types.ts`, then add a `build_X()` to `generate.py` so the
mock stays reproducible.
