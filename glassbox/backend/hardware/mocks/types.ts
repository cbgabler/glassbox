/**
 * GlassBox dashboard data shapes.
 *
 * Every JSON / NDJSON file in this folder validates against one of the
 * types below. The `schema` field on each top-level payload is a stable
 * version string -- bump the version (e.g. "v1" -> "v2") if you change a
 * field, so the frontend can branch.
 *
 * All numbers are SI / standard:
 *   - cycles    : ESP32 CCOUNT cycles at 240 MHz
 *   - micros    : esp_timer microseconds
 *   - power[i]  : raw 12-bit ADC reading from the Pico (0..4095) sampled at
 *                 ~250 kHz off the INA169 OUT pin. To convert to amps:
 *                   v_adc = (power[i] / 4095) * 3.3                    // volts
 *                   i_load = v_adc / (10 * R_S)                        // amps
 *                 where R_S is the INA169 sense resistor (default 0.1 ohm
 *                 on the TekBots breakout, so 1 V at OUT = 1 A through Vcc).
 */

// ---------------------------------------------------------------------------
// Common building blocks
// ---------------------------------------------------------------------------

export type Verdict =
  | "leak_detected"
  | "leak_detected_and_quarantined"
  | "safe"
  | "anomalous"
  | "unknown"
  // Non-side-channel verdicts produced by the v2 findings pipeline. A run
  // can have multiple findings; `verdict` always reflects the WORST of them
  // so the leaderboard / GitHub comment surfaces the most actionable signal.
  | "memory_corruption_detected"
  | "crash_detected"
  | "non_determinism_detected"
  | "static_warning"
  | "key_recovered";

/** Severity ladder for TVLA results. Order = strongest first. */
export type Severity =
  | "CRITICAL"
  | "HIGH"
  | "MEDIUM"
  | "LOW"
  | "BELOW_QUANTUM"
  | "pass"
  | "flat";

export type Channel = "cycles" | "micros" | "insns" | "branches" | "power";

export type FunctionId = 0 | 1 | 2; // 0=noop, 1=strcmp_naive, 2=strcmp_safe

// ---------------------------------------------------------------------------
// 1. traces.json -- raw per-call traces for the PowerTrace.tsx component.
// ---------------------------------------------------------------------------

export interface Trace {
  trace_id: string;
  fn_id: FunctionId;
  fn: "strcmp_naive" | "strcmp_safe" | "noop" | string;
  label: "safe" | "timing_leak" | "power_leak" | string;
  /** Lowest-order byte of the input. -1 if not byte-indexed. */
  input_byte: number;
  hex_input: string;
  hex_output: string;
  cycles: number;
  micros: number;
  insns: number;
  branches: number;
  trace_len: number; // always 256 in v1
  sample_period_us: number; // ~4 us per sample
  /** Length = trace_len. Each entry is 0..adc_max. */
  power: number[];
}

export interface TracesPayload {
  schema: "glassbox.traces.v1";
  trace_len: number;
  sample_period_us: number;
  adc_bits: number;
  adc_max: number;
  /** Convenience: which input byte produces the longest run on a leaky fn. */
  secret_first_byte: number;
  secret_first_byte_hex: string;
  traces: Trace[];
}

// ---------------------------------------------------------------------------
// 2. byte_histogram.json -- the timing chart (key dashboard view).
// ---------------------------------------------------------------------------

export interface ByteRow {
  byte: number; // 0..255
  byte_hex: string; // "0x00".."0xff"
  ascii: string | null; // null if non-printable
  n: number; // # of repeats per byte (default 5)
  min: number;
  max: number;
  median: number;
  mean: number;
  std: number;
  p25: number;
  p75: number;
  /** Raw cycle samples that produced the stats above. */
  samples: number[];
}

export interface BytePerFunction {
  fn: string;
  channel: Channel;
  by_byte: ByteRow[];
  global_min: number;
  global_max: number;
  /** If the function is leaky on the chosen channel, this is the recovered byte. */
  argmax_byte: number;
}

export interface ByteHistogramPayload {
  schema: "glassbox.byte_histogram.v1";
  secret_first_byte: number;
  secret_first_byte_hex: string;
  n_repeats: number;
  functions: Record<string, BytePerFunction>;
}

// ---------------------------------------------------------------------------
// 3. tvla_report.json -- ISO/IEC 17825 verdicts for the LeakGauge component.
// ---------------------------------------------------------------------------

export interface TvlaChannelResult {
  verdict: Severity;
  /**
   * Welch's t-statistic absolute value. null when the channel was flat (no
   * signal at all) OR when t was infinite (perfect determinism). In the
   * infinite case `t_abs_infinite: true` will be set; the frontend should
   * render this as "∞" or "perfect".
   */
  t_abs: number | null;
  t_abs_infinite?: boolean;
  mean_a: number;
  mean_b: number;
  note?: string;
}

export interface TvlaFunctionResult {
  n_group_a: number; // input == secret byte
  n_group_b: number; // input != secret byte
  channels: Record<Channel, TvlaChannelResult>;
  leak_detected: boolean;
  strongest_channel: Channel | null;
  strongest_severity: Severity | null;
  /** As above: null when t was infinite or no leak. */
  strongest_t_abs: number | null;
}

export interface TvlaSeverityLegend {
  name: Severity;
  rule: string;
  color: string;
}

export interface TvlaReportPayload {
  schema: "glassbox.tvla.v1";
  tvla_threshold: number; // 4.5
  n_traces_total: number;
  per_function: Record<string, TvlaFunctionResult>;
  n_leak: number;
  n_pass: number;
  leaky_functions: string[];
  expectations_match: boolean;
  severity_ladder: TvlaSeverityLegend[];
}

// ---------------------------------------------------------------------------
// 3b. Polymorphic findings (v2 -- additive to TvlaReportPayload).
//
// The v1 schema reports ONLY side-channel leaks. Real hardware testing
// also surfaces crashes, non-determinism, length oracles, memory corruption,
// static-analysis warnings, and key-recovery successes. Rather than
// inventing one mega-shape per finding, we use a discriminated union keyed
// off `type`. Every finding has the same envelope (id, type, severity,
// title, ...) plus a `data` blob whose shape is fixed by `type`.
//
// New finding types append; existing consumers that only handle "tvla"
// keep working unchanged because the v1 fields on RunDetailPayload still
// populate.
// ---------------------------------------------------------------------------

export type FindingType =
  | "tvla"               // existing side-channel leak (cycle / micros / power)
  | "crash"              // function timed out, panicked, or returned ERR
  | "non_determinism"    // same input -> different output across reruns
  | "length_oracle"      // out_len correlates with secret in unintended ways
  | "memory_corruption"  // shadow sentinel tripped, stack canary tripped, heap poisoning fired
  | "static"             // pre-flash linter rule fired on the source file
  | "cpa_key_recovery";  // CPA on power traces recovered (or attempted) a key

export type FindingSeverity =
  | "CRITICAL"
  | "HIGH"
  | "MEDIUM"
  | "LOW"
  | "INFO"
  | "pass";

export interface BaseFinding {
  /** Stable per-run id. e.g. "f_001". */
  id: string;
  type: FindingType;
  severity: FindingSeverity;
  /** One-line human title for the finding. */
  title: string;
  /** Markdown-friendly explanation suitable for a GitHub PR comment. */
  detail: string;
  /** Optional remediation hint (1-3 sentences). */
  remediation?: string;
  /** Optional source location (file:line). Used for `static` findings. */
  source?: { file: string; line: number; col?: number };
}

export interface TvlaFindingData {
  channel: Channel;
  order: 1 | 2;
  t_abs: number | null;
  t_abs_infinite?: boolean;
  threshold: number;
  argmax_sample?: number;
  /** When the leak peaks inside the trace, how far through the function
   *  it occurred (0..1). Only set when the channel is `power`. */
  fraction_through_function?: number;
}
export type TvlaFinding = BaseFinding & { type: "tvla"; data: TvlaFindingData };

export interface CrashFindingData {
  /** What kind of failure terminated the call. */
  kind: "timeout" | "panic" | "err_response" | "wdt_reset";
  /** ESP32 PC at panic if reported by the firmware panic handler. */
  panic_pc?: string;
  /** Reason string from the panic handler (e.g. "LoadProhibited"). */
  panic_reason?: string;
  /** Hex input that triggered the failure (truncated to 64 chars). */
  hex_input?: string;
  /** How many traces total exhibited this failure. */
  count: number;
  /** Total traces attempted in the campaign (denominator). */
  total: number;
}
export type CrashFinding = BaseFinding & { type: "crash"; data: CrashFindingData };

export interface NonDeterminismFindingData {
  /** Number of (input, output_a, output_b) triples that disagreed. */
  disagreements: number;
  /** Total inputs that were tested for determinism. */
  tested: number;
  /** A few example mismatches for the UI. */
  examples: { hex_input: string; hex_output_a: string; hex_output_b: string }[];
}
export type NonDeterminismFinding = BaseFinding & {
  type: "non_determinism";
  data: NonDeterminismFindingData;
};

export interface LengthOracleFindingData {
  /** Distinct out_len values observed and how often each occurred. */
  out_len_histogram: Record<number, number>;
  /** Welch-t between out_len and group label, when computable. */
  t_abs: number | null;
  /** True if the firmware ever returned a non-constant out_len across the campaign. */
  variable: boolean;
}
export type LengthOracleFinding = BaseFinding & {
  type: "length_oracle";
  data: LengthOracleFindingData;
};

export interface MemoryFindingData {
  /** Which guard tripped. */
  kind: "input_shadow_overflow" | "output_shadow_overflow" |
        "stack_canary" | "heap_poison" | "ubsan";
  /** Hex input that triggered it (if known). */
  hex_input?: string;
  /** Bytes by which the buffer was overrun, if measurable. */
  overrun_bytes?: number;
  /** Firmware-reported description (raw line). */
  raw?: string;
}
export type MemoryFinding = BaseFinding & {
  type: "memory_corruption";
  data: MemoryFindingData;
};

export interface StaticFindingData {
  rule_id: string;          // e.g. "CT001" -- branch on secret
  /** Code excerpt around the offending line. */
  excerpt: string;
}
export type StaticFinding = BaseFinding & { type: "static"; data: StaticFindingData };

export interface CpaFindingData {
  /** Per-byte recovery: best_guess (0..255), correlation, rank-of-true. */
  per_byte: {
    byte_index: number;
    best_guess: number;
    correlation: number;
    /** When the true secret byte is known to the runner, its rank in the
     *  candidate list (1 = recovered exactly). null when unknown. */
    true_rank: number | null;
  }[];
  /** True iff every byte's best_guess matches the known secret. */
  full_key_recovered: boolean;
  /** Number of traces used in the attack. */
  n_traces: number;
}
export type CpaFinding = BaseFinding & { type: "cpa_key_recovery"; data: CpaFindingData };

export type Finding =
  | TvlaFinding
  | CrashFinding
  | NonDeterminismFinding
  | LengthOracleFinding
  | MemoryFinding
  | StaticFinding
  | CpaFinding;

export interface FindingsSummary {
  /** Total findings, including INFO/pass. */
  total: number;
  /** Per-severity counts. Severities not present are 0. */
  by_severity: Record<FindingSeverity, number>;
  /** Per-type counts. Types not present are 0. */
  by_type: Record<FindingType, number>;
  /** Worst severity encountered (drives RunSummary.verdict). */
  worst_severity: FindingSeverity;
}

// ---------------------------------------------------------------------------
// 4. live_attack_stream.jsonl -- one JSON object per line. Each line is one
//    of the `LiveStreamEvent` variants. Replay this file at any pace to
//    drive the live oscilloscope view; in production the same shape comes
//    over WebSocket from the cloud backend.
// ---------------------------------------------------------------------------

interface BaseStreamEvent {
  /** Seconds since `run_started`. */
  t: number;
}

export type LiveStreamEvent =
  | (BaseStreamEvent & {
      type: "run_started";
      run_id: string;
      fn: string;
      fn_id: FunctionId;
      secret_len: number;
      model: string;
      streak_target: number;
      leak_threshold: number;
    })
  | (BaseStreamEvent & {
      type: "status";
      victim: "running" | "quarantined" | "rom_bootloader" | "boot";
      monitor: "armed" | "fired" | "idle";
      via?: string;
    })
  | (BaseStreamEvent & {
      type: "trace";
      fn: string;
      byte: number;
      byte_hex: string;
      rep: number;
      cycles: number;
      micros: number;
      label: string;
      leak_confidence: number; // 0..1
      streak: number;
    })
  | (BaseStreamEvent & {
      type: "verdict_fired";
      label: string;
      leak_confidence: number;
      channel_argmax: Channel;
      streak_at_fire: number;
      calls_before_fire: number;
    })
  | (BaseStreamEvent & {
      type: "quarantine_command";
      method: "kill_lines+nvs" | "kill_lines" | "nvs";
      pico_pin_kill_en: number;
      pico_pin_kill_boot: number;
    })
  | (BaseStreamEvent & {
      type: "uart_ack";
      from: "esp32" | "pico";
      msg: string;
    })
  | (BaseStreamEvent & {
      type: "kill_line_assert" | "kill_line_release";
      lines: ("KILL_EN" | "KILL_BOOT")[];
      duration_ms?: number;
    })
  | (BaseStreamEvent & {
      type: "run_finished";
      run_id: string;
      verdict: Verdict;
      calls_total: number;
      wall_seconds: number;
      bytes_recovered: number;
      secret_len: number;
    });

// ---------------------------------------------------------------------------
// 5. quarantine_events.json -- state-machine timeline for the QuarantineLog.
// ---------------------------------------------------------------------------

export type VictimState =
  | "boot"
  | "running"
  | "quarantined"
  | "rom_bootloader";

export interface QuarantineEvent {
  /** ISO-8601 UTC timestamp. */
  t: string;
  from: VictimState | "boot";
  to: VictimState;
  reason: string;
  by: "esp32" | "pico_monitor" | "runner" | "operator";
  detail?: Record<string, unknown>;
}

export interface QuarantineEventsPayload {
  schema: "glassbox.quarantine_events.v1";
  victim: { chip: string; freq_mhz: number };
  monitor: { chip: string; kill_lines: string[] };
  events: QuarantineEvent[];
}

// ---------------------------------------------------------------------------
// 6. orchestrator_report.json -- end-to-end pipeline result. Schema lives in
//    runner/orchestrator.py; this is the typed mirror.
// ---------------------------------------------------------------------------

export type OrchestratorVerdict =
  | "ALL_GREEN"
  | "PARTIAL"
  | "ALL_FAIL"
  | "NOTHING_RAN";

export interface OrchestratorStage {
  id: number;
  name: string;
  status: "PASS" | "FAIL" | "SKIP";
  duration_s: number;
  details: Record<string, unknown>;
  error?: string;
}

export interface OrchestratorReport {
  schema_version: "1.0";
  tool: "glassbox.orchestrator";
  timestamp: string; // ISO-8601 UTC
  args: {
    port: string | null;
    report: string;
    traces: string;
    quick: boolean;
    no_fire: boolean;
    repeats: number;
    skip: string;
    only: string;
  };
  summary: {
    stages_total: number;
    stages_passed: number;
    stages_failed: number;
    stages_skipped: number;
    elapsed_s: number;
    verdict: OrchestratorVerdict;
  };
  stages: OrchestratorStage[];
}

// ---------------------------------------------------------------------------
// 7. runs_index.json -- the leaderboard / recent-runs table.
// ---------------------------------------------------------------------------

export interface RunSummary {
  id: string;
  repo: string;
  commit_sha: string;
  pr_number: number;
  function: string;
  verdict: Verdict;
  leak_channel: Channel | null;
  leak_severity: Severity | null;
  /** 0..1 model confidence -- low number on a "safe" verdict means
   *  "the model is confident this is safe". */
  leak_confidence: number;
  n_traces: number;
  duration_s: number;
  started_at: string; // ISO-8601 UTC
  finished_at: string;
  quarantine_fired: boolean;
  /** v2: counts of polymorphic findings, omitted on v1 runs. */
  findings_summary?: FindingsSummary;
}

export interface LeaderboardEntry {
  library: string;
  verdict: Verdict;
  /** Higher = safer. 100 = no leaks observed across the suite. */
  score: number;
}

export interface RunsIndexPayload {
  schema: "glassbox.runs_index.v1";
  fleet: { n_pods: number; pod_ids: string[] };
  leaderboard: LeaderboardEntry[];
  runs: RunSummary[];
}

// ---------------------------------------------------------------------------
// 8. run_detail.json -- one whole run packet for /runs/[id] route.
// ---------------------------------------------------------------------------

export interface RunDetailTracePreview {
  trace_id: string;
  fn: string;
  fn_id: FunctionId;
  label: string;
  input_byte: number;
  hex_input: string;
  cycles: number;
  active_samples: number;
  /** First 32 samples only -- fetch traces.json for the full 256. */
  power_preview: number[];
}

export interface RunDetailPayload {
  schema: "glassbox.run_detail.v1";
  id: string;
  repo: string;
  commit_sha: string;
  pr_number: number;
  branch: string;
  function: string;
  victim: { chip: string; freq_mhz: number; harness: string };
  monitor: {
    chip: string;
    trace_len: number;
    sample_period_us: number;
    model: string;
    model_classes: string[];
  };
  started_at: string;
  finished_at: string;
  duration_s: number;
  verdict: Verdict;
  summary: {
    leak_channel: Channel | null;
    leak_severity: Severity | null;
    leak_confidence: number;
    calls_total: number;
    calls_before_fire: number;
    bytes_recovered: number;
    secret_len: number;
    quarantine_fired: boolean;
    quarantine_method: string;
  };
  tvla_summary: {
    channels: Record<Channel, TvlaChannelResult>;
    leak_detected: boolean;
    strongest_channel: Channel | null;
    strongest_severity: Severity | null;
  };
  histogram_thumbnail: {
    fn: string;
    channel: Channel;
    min: number;
    max: number;
    argmax_byte: number;
  };
  sample_traces: RunDetailTracePreview[];
  /** Filenames in this folder that the dashboard can hot-load on demand. */
  quarantine_events_id: string;
  orchestrator_id: string;
  live_attack_stream_id: string;
  byte_histogram_id: string;
  tvla_report_id: string;
  github: {
    comment_state: "pending" | "posted" | "failed";
    comment_url: string;
    comment_md: string;
  };
  /** v2 polymorphic findings list. Optional so v1 mocks remain valid;
   *  when present, includes BOTH the side-channel findings (as
   *  TvlaFinding entries) AND any non-side-channel findings raised by
   *  the runner / firmware / pre-flash linter. */
  findings?: Finding[];
  findings_summary?: FindingsSummary;
}
