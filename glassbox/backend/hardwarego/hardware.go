// hardwarego -- MCP server that lets the agent run the GlassBox hardware
// pipeline against a freshly cloned repo.
//
// The agent flow is:
//
//   1. agent calls repocontextserver/clone_repo to drop a target repo into
//      backend/clonedrepos/<run_id>/
//   2. agent calls hardware/list_hardware_targets {repo_root: ...} to find
//      every .cpp/.cc/.c file in that repo that exposes the gb_target_call
//      ABI (i.e. is compatible with our ESP32 harness)
//   3. agent calls hardware/start_hardware_audit {repo_root: ...} which
//      kicks off the long-running flash+verify sweep IN THE BACKGROUND and
//      returns an audit_id immediately.
//   4. agent polls hardware/get_hardware_audit_status {audit_id: ...} on
//      user request ("how's it going?") and gets a structured progress
//      report it can summarize.
//   5. agent reports the final per-target findings once status == "done".
//
// Only one audit can run at a time (the harness is a single piece of
// physical hardware). A second start request returns 409 Conflict.
//
// Endpoints (POST JSON unless noted):
//
//   GET  /health
//   POST /execute/list_hardware_targets   {repo_root, filter?}
//   POST /execute/start_hardware_audit    {repo_root, filter?, bridge_seconds?, esp_port?, pico_port?}
//   POST /execute/get_hardware_audit_status {audit_id?}
//   POST /execute/cancel_hardware_audit   {audit_id?}

package main

import (
	"bufio"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"
)

// -----------------------------------------------------------------------------
// Repo-relative paths (resolved against the auto-detected glassbox root)
// -----------------------------------------------------------------------------

const (
	// scan_target.py is the per-target orchestrator: install -> ct_lint ->
	// flash (compile+upload+verify) -> collect (TVLA campaign) -> analyze
	// (Welch's t-test + CPA) -> emit JSON TargetReport on stdout. We invoke
	// it once per harness-compatible source file.
	relScanTarget  = "glassbox/backend/hardware/runner/scan_target.py"
	relGbTargetCpp = "glassbox/backend/hardware/esp/harness/gb_target.cpp"
	relRunnerVenv  = "glassbox/backend/hardware/runner/.venv/bin/python"

	// scan_target.py logs "[scan] --- stage: <name> ---" on stderr at each
	// pipeline boundary. We use that to drive the per-target step field.
	scanStageMarker = "[scan] --- stage: "

	// Substrings we still grep on stderr to surface common environment
	// problems straight to the agent rather than burying them inside the
	// scanner's traceback.
	bridgeAckMiss  = "Pico did not enter bridge mode"
	esptoolMissing = "No module named esptool"
	noPicoDetected = "no Pico detected via USB"

	defaultBridgeSeconds = 90
	// Per-target hard timeout. With --n 500 we expect ~50s of capture +
	// ~30s of flash + a few seconds of analyze; 360s leaves slack for
	// slow Wi-Fi/USB while still aborting truly stuck runs.
	defaultPerTargetSecs  = 360
	defaultPostSuccessSec = 2
	defaultPostFailExtra  = 5

	// Number of traces per TVLA group. Higher = more sensitive but slower.
	// 500 matches scan_target.DEFAULT_N_PER_GROUP and gives ~|t|>4.5
	// detection power on the LX6 PMU channels.
	defaultNPerGroup = 500

	listenPort = ":8084"
)

// gbTargetCallRegex matches `gb_target_call(` -- the signature every
// harness-compatible C/C++ source must define. We accept return-type and
// whitespace variations.
var gbTargetCallRegex = regexp.MustCompile(`(?m)^[^/\n]*\bgb_target_call\s*\(`)

// harnessABISignature is the exact function the harness expects to find in
// each candidate source file. Kept in sync with
// glassbox/backend/hardware/esp/harness/gb_target.h. Surfaced verbatim to
// the agent in error responses so it can show users exactly what's missing
// without us having to update the prompt every time the ABI shifts.
const harnessABISignature = "int gb_target_call(const uint8_t* secret, size_t secret_len, uint8_t* out, size_t* out_len);"

const harnessABIHeaderPath = "glassbox/backend/hardware/esp/harness/gb_target.h"

// maxSkippedExamples caps how many skipped files we list back to the agent
// in a no_targets response. Enough for the agent to show the user a useful
// sample without flooding the LLM context on huge repos.
const maxSkippedExamples = 10

// syntheticDirName is the subdir under the cloned repo where we drop
// auto-generated harness wrappers (one .cpp per registered target).
// Living inside repo_root means scanRepoForTargets picks them up for free
// AND they get cleaned up when the repo dir is removed.
const syntheticDirName = "__glassbox_synthetic__"

// maxRepoFileSize bounds how much of a candidate source file we'll read
// when parsing its function signatures. 1 MiB is generous for any real
// hand-written .cpp; generated/concatenated files larger than this almost
// certainly aren't single-purpose.
const maxRepoFileSize = 1 * 1024 * 1024

// maxReferenceLen bounds the byte length of a comparator-shape reference
// constant. The harness only accepts inputs <= 64 bytes, so a reference
// longer than that is unaudible.
const maxReferenceLen = 64

// fnSigRegexFmt builds a regex that finds a forward decl OR definition of
// a specific function by name. We deliberately keep this simple: the
// agent already knows the function name, so we just need to locate one
// declaration to extract the parameter list. Captures: (1) return-type
// fragment, (2) parameter list (without parens). Matches things like:
//
//	int byte_compare(const uint8_t* a, const uint8_t* b, size_t n)
//	void  aes_block( const uint8_t key[16], const uint8_t in[16], uint8_t out[16] )
//	extern "C" int gb_target_call(const uint8_t* s, size_t n, uint8_t* o, size_t* ol)
//
// We require a balanced single-line param list -- multi-line decls are
// rare for the small audit-shaped functions we care about, and demanding
// single-line keeps the regex tractable.
const fnSigRegexFmt = `(?m)^\s*(?:extern\s+"C"\s+)?` +
	`((?:const\s+)?(?:unsigned\s+)?(?:int|bool|void|uint8_t|size_t|char|long))` +
	`\s*\*?\s*\b%s\b\s*\(\s*([^)]*?)\s*\)\s*[{;]`

// -----------------------------------------------------------------------------
// Types: request/response payloads
// -----------------------------------------------------------------------------

type listTargetsRequest struct {
	RepoRoot string `json:"repo_root"`
	Filter   string `json:"filter,omitempty"`
}

type targetInfo struct {
	Path     string `json:"path"`     // absolute
	RelPath  string `json:"rel_path"` // relative to repo_root
	Name     string `json:"name"`
	Reason   string `json:"reason"`
	Excluded bool   `json:"excluded,omitempty"`
}

type listTargetsResponse struct {
	RepoRoot string       `json:"repo_root"`
	Targets  []targetInfo `json:"targets"`
	Skipped  []targetInfo `json:"skipped"`
	Count    int          `json:"count"`
	Note     string       `json:"note,omitempty"`
	// HarnessABISignature is included on every response (not just empties)
	// so the agent always knows what makes a file flashable and can
	// describe it to the user without us re-prompting.
	HarnessABISignature string `json:"harness_abi_signature"`
	HarnessABIHeader    string `json:"harness_abi_header"`
}

type startAuditRequest struct {
	RepoRoot      string `json:"repo_root"`
	Filter        string `json:"filter,omitempty"`
	BridgeSeconds int    `json:"bridge_seconds,omitempty"`
	ESPPort       string `json:"esp_port,omitempty"`
	PicoPort      string `json:"pico_port,omitempty"`
}

type startAuditResponse struct {
	AuditID    string   `json:"audit_id"`
	RepoRoot   string   `json:"repo_root"`
	Targets    []string `json:"targets"`
	StartedAt  string   `json:"started_at"`
	StatusHint string   `json:"status_hint"`
}

// targetResult is one source file's audit outcome. The first half is run
// telemetry (state + timing); the second half is the TargetReport
// scan_target.py emitted on stdout, surfaced verbatim so the agent can
// quote real TVLA / CPA / crash / ct_lint findings instead of pattern-
// matching the source itself.
type targetResult struct {
	Name         string  `json:"name"`
	Path         string  `json:"path"`
	State        string  `json:"state"` // queued|install|ct_lint|flash|collect|analyze|pass|fail|skipped|cancelled
	Pass         bool    `json:"pass"`
	Reason       string  `json:"reason,omitempty"`
	StartedAt    string  `json:"started_at,omitempty"`
	FinishedAt   string  `json:"finished_at,omitempty"`
	DurationSecs float64 `json:"duration_secs,omitempty"`
	BridgeLocked bool    `json:"bridge_locked,omitempty"`

	// ---- TargetReport fields (mirrors scan_target.TargetReport.to_dict) ----
	// Verdict is the headline category derived from the worst finding.
	// Examples: "safe", "leak_detected", "crash_detected", "key_recovered",
	// "memory_corruption_detected", "static_warning". Empty when the
	// scanner crashed before emitting a report.
	Verdict string `json:"verdict,omitempty"`
	// WorstSeverity is the highest severity in `findings`, or "pass" if
	// no finding was non-pass. Same enum as Finding.severity.
	WorstSeverity string `json:"worst_severity,omitempty"`
	// Findings is the verbatim list of Finding objects from the report.
	// Each entry has the polymorphic shape documented in
	// pipeline/findings.py: {id, type, severity, title, detail, data,
	// remediation?, source?}. The agent is expected to render these
	// (one <<code>> block per finding) rather than re-deriving findings
	// from the source.
	Findings []map[string]any `json:"findings,omitempty"`
	// FindingsSummary mirrors TargetReport.summary -- counts by severity
	// and by type. Useful for the agent's chat-panel rollup.
	FindingsSummary map[string]any `json:"findings_summary,omitempty"`
	// NTraces is how many traces actually survived the collect stage
	// (post-crash filtering). 0 means the scanner never reached collect.
	NTraces int `json:"n_traces,omitempty"`
	// StageSecs maps stage name (install/ct_lint/flash/collect/analyze)
	// to wall time in seconds, so the dashboard can show a flame chart.
	StageSecs map[string]float64 `json:"stage_secs,omitempty"`
}

type auditStatusResponse struct {
	AuditID       string         `json:"audit_id"`
	RepoRoot      string         `json:"repo_root"`
	State         string         `json:"state"` // running|done|cancelled|failed
	StartedAt     string         `json:"started_at"`
	FinishedAt    string         `json:"finished_at,omitempty"`
	ElapsedSecs   float64        `json:"elapsed_secs"`
	Total         int            `json:"total"`
	Completed     int            `json:"completed"`
	Passed        int            `json:"passed"`
	Failed        int            `json:"failed"`
	CurrentIndex  int            `json:"current_index"` // 1-based; 0 if not yet started
	CurrentTarget string         `json:"current_target,omitempty"`
	CurrentStep   string         `json:"current_step,omitempty"`
	LastUpdate    string         `json:"last_update"`
	Results       []targetResult `json:"results"`
	Summary       string         `json:"summary,omitempty"`
	Error         string         `json:"error,omitempty"`
}

type cancelAuditRequest struct {
	AuditID string `json:"audit_id"`
}

// registerTargetRequest is the smallest viable spec for "wrap function X
// in file Y as a flashable harness target". The agent identifies the
// candidate function and gives us the path -- everything else (signature
// parsing, template selection, source rendering, file write) happens
// server-side so the agent's payload stays tiny.
type registerTargetRequest struct {
	RepoRoot     string `json:"repo_root"`
	SourceFile   string `json:"source_file"`             // relative to repo_root
	FunctionName string `json:"function_name"`           // C identifier
	ReferenceHex string `json:"reference_hex,omitempty"` // required only for 2-ptr comparator shapes
	TargetName   string `json:"target_name,omitempty"`   // optional override for the synthetic file's basename
}

// registerTargetResponse tells the agent what shape was matched and
// where the synthesized wrapper landed on disk. The path can be passed
// straight to start_hardware_audit (it sits inside repo_root, so the
// existing scanner picks it up).
type registerTargetResponse struct {
	RepoRoot          string `json:"repo_root"`
	SourceFile        string `json:"source_file"`
	FunctionName      string `json:"function_name"`
	ShapeUsed         string `json:"shape_used"`       // bytes_len | comparator_len | harness_native
	WrapperPath       string `json:"wrapper_path"`     // absolute
	WrapperRelPath    string `json:"wrapper_rel_path"` // relative to repo_root
	ParsedSignature   string `json:"parsed_signature"`
	HarnessABISigUsed string `json:"harness_abi_signature_used"`
}

// jsonErrorResponse is the shape we return for actionable failures (notably
// "no harness-compatible sources" on start_hardware_audit). The MCP-side
// agent gets a stable schema instead of a single line of plain text:
//
//   - Error      : the same human string the old http.Error returned, so
//     any caller still grepping for it keeps working.
//   - ErrorCode  : machine-readable enum the agent can branch on.
//   - Details    : free-form scan stats / examples / signatures.
//   - Hint       : remediation guidance the agent can paraphrase to the user.
//   - NextAction : tool the agent should call next, if any.
type jsonErrorResponse struct {
	Error      string         `json:"error"`
	ErrorCode  string         `json:"error_code,omitempty"`
	Details    map[string]any `json:"details,omitempty"`
	Hint       string         `json:"hint,omitempty"`
	NextAction string         `json:"next_action,omitempty"`
}

// -----------------------------------------------------------------------------
// Audit job state (in-memory, mutex-guarded)
// -----------------------------------------------------------------------------

type audit struct {
	mu sync.Mutex

	id         string
	repoRoot   string
	scanTarget string // absolute path to scan_target.py
	python     string
	pythonArgs []string
	bridgeSecs int
	espPort    string
	picoPort   string
	nPerGroup  int

	state      string // running|done|cancelled|failed
	startedAt  time.Time
	finishedAt time.Time
	lastUpdate time.Time
	errorMsg   string
	summary    string

	targets    []targetInfo
	results    []*targetResult
	currentIdx int

	cancel context.CancelFunc
}

func (a *audit) snapshot() auditStatusResponse {
	a.mu.Lock()
	defer a.mu.Unlock()
	results := make([]targetResult, 0, len(a.results))
	passed, failed, completed := 0, 0, 0
	current := ""
	currentStep := ""
	for i, r := range a.results {
		results = append(results, *r)
		switch r.State {
		case "pass":
			passed++
			completed++
		case "fail", "skipped", "cancelled":
			failed++
			completed++
		default:
			if i+1 == a.currentIdx {
				current = r.Name
				currentStep = r.State
			}
		}
	}
	out := auditStatusResponse{
		AuditID:       a.id,
		RepoRoot:      a.repoRoot,
		State:         a.state,
		StartedAt:     a.startedAt.UTC().Format(time.RFC3339),
		ElapsedSecs:   time.Since(a.startedAt).Seconds(),
		Total:         len(a.targets),
		Completed:     completed,
		Passed:        passed,
		Failed:        failed,
		CurrentIndex:  a.currentIdx,
		CurrentTarget: current,
		CurrentStep:   currentStep,
		LastUpdate:    a.lastUpdate.UTC().Format(time.RFC3339),
		Results:       results,
		Summary:       a.summary,
		Error:         a.errorMsg,
	}
	if !a.finishedAt.IsZero() {
		out.FinishedAt = a.finishedAt.UTC().Format(time.RFC3339)
		out.ElapsedSecs = a.finishedAt.Sub(a.startedAt).Seconds()
	}
	return out
}

func (a *audit) setStep(idx int, step string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if idx < 0 || idx >= len(a.results) {
		return
	}
	a.results[idx].State = step
	if a.results[idx].StartedAt == "" {
		a.results[idx].StartedAt = time.Now().UTC().Format(time.RFC3339)
	}
	a.currentIdx = idx + 1
	a.lastUpdate = time.Now()
}

// finishTarget records the outcome of one target. `pass` is the
// scanner-level pass/fail (true = scanner finished cleanly AND verdict
// was "safe"; false = scanner crashed OR verdict was non-safe).
// `report` is the parsed TargetReport from scan_target.py's stdout, or
// nil if the scanner died before emitting one (in which case `reason`
// carries the human explanation).
func (a *audit) finishTarget(idx int, pass bool, reason string,
	bridgeLocked bool, report *targetReportPayload) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if idx < 0 || idx >= len(a.results) {
		return
	}
	r := a.results[idx]
	r.Pass = pass
	r.Reason = reason
	r.BridgeLocked = bridgeLocked
	r.FinishedAt = time.Now().UTC().Format(time.RFC3339)
	if t, err := time.Parse(time.RFC3339, r.StartedAt); err == nil {
		r.DurationSecs = time.Since(t).Seconds()
	}
	if report != nil {
		r.Verdict = report.Verdict
		r.WorstSeverity = report.WorstSeverity
		r.Findings = report.Findings
		r.FindingsSummary = report.Summary
		r.NTraces = report.NTraces
		r.StageSecs = report.StageSecs
	}
	if pass {
		r.State = "pass"
	} else {
		r.State = "fail"
	}
	a.lastUpdate = time.Now()
}

// targetReportPayload mirrors TargetReport.to_dict() in
// glassbox/backend/hardware/runner/pipeline/findings.py. We only json-
// decode the fields hardwarego surfaces back to the agent; extra
// fields (e.g. duration_secs computed by Python) are tolerated by
// json.Unmarshal silently.
type targetReportPayload struct {
	Target        string             `json:"target"`
	Verdict       string             `json:"verdict"`
	WorstSeverity string             `json:"worst_severity"`
	Findings      []map[string]any   `json:"findings"`
	Summary       map[string]any     `json:"summary"`
	NTraces       int                `json:"n_traces"`
	StageSecs     map[string]float64 `json:"stage_secs"`
}

// -----------------------------------------------------------------------------
// Server state
// -----------------------------------------------------------------------------

type server struct {
	mu      sync.Mutex
	current *audit // nil unless an audit is in flight or recently finished
}

func newServer() *server { return &server{} }

func (s *server) latest() *audit {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.current
}

// tryStart claims the shared-hardware slot for `a`. Returns
// (true, "") on success, or (false, existingAuditID) when another
// audit is in flight. The single-slot rule is enforced because all
// audits share one ESP32 + one Pico bridge -- a second concurrent run
// would race the first for /dev/cu.* and produce both a corrupted
// flash and useless traces. The agent occasionally fires duplicate
// start_hardware_audit calls (network retry, model double-step, etc.),
// so returning a structured "you already have one running, here's its
// id" beats silently allowing the duplicate.
func (s *server) tryStart(a *audit) (bool, string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.current != nil && s.current.state == "running" {
		return false, s.current.id
	}
	s.current = a
	return true, ""
}

// -----------------------------------------------------------------------------
// HTTP handlers
// -----------------------------------------------------------------------------

func main() {
	srv := newServer()
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("/execute/list_hardware_targets", srv.handleListTargets)
	mux.HandleFunc("/execute/start_hardware_audit", srv.handleStartAudit)
	mux.HandleFunc("/execute/get_hardware_audit_status", srv.handleStatus)
	mux.HandleFunc("/execute/cancel_hardware_audit", srv.handleCancel)
	mux.HandleFunc("/execute/register_synthetic_target", srv.handleRegisterTarget)

	log.Printf("Starting Hardware MCP server on port %s\n", listenPort)
	log.Fatal(http.ListenAndServe(listenPort, mux))
}

func (s *server) handleListTargets(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	var req listTargetsRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad json: "+err.Error(), http.StatusBadRequest)
		return
	}
	repo := strings.TrimSpace(req.RepoRoot)
	if repo == "" {
		http.Error(w, "repo_root is required", http.StatusBadRequest)
		return
	}
	if !dirExists(repo) {
		http.Error(w, "repo_root does not exist: "+repo, http.StatusBadRequest)
		return
	}
	targets, skipped, err := scanRepoForTargets(repo, req.Filter)
	if err != nil {
		http.Error(w, "scan: "+err.Error(), http.StatusInternalServerError)
		return
	}
	resp := listTargetsResponse{
		RepoRoot:            repo,
		Targets:             targets,
		Skipped:             skipped,
		Count:               len(targets),
		HarnessABISignature: harnessABISignature,
		HarnessABIHeader:    harnessABIHeaderPath,
	}
	if len(targets) == 0 {
		resp.Note = fmt.Sprintf(
			"No flashable files in this repo: scanned %d C/C++ source file(s), "+
				"none defined gb_target_call(...). To be auditable a file must "+
				"implement the harness ABI: `%s` (declared in %s). "+
				"Tell the user the repo is not GlassBox-ready -- do NOT call "+
				"start_hardware_audit; it will return error_code=no_targets.",
			len(skipped), harnessABISignature, harnessABIHeaderPath,
		)
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *server) handleStartAudit(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	var req startAuditRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad json: "+err.Error(), http.StatusBadRequest)
		return
	}
	repo := strings.TrimSpace(req.RepoRoot)
	if repo == "" {
		http.Error(w, "repo_root is required", http.StatusBadRequest)
		return
	}
	if !dirExists(repo) {
		http.Error(w, "repo_root does not exist: "+repo, http.StatusBadRequest)
		return
	}

	glassboxRoot, err := resolveGlassboxRoot()
	if err != nil {
		http.Error(w, "cannot locate glassbox repo: "+err.Error(), http.StatusInternalServerError)
		return
	}
	scanTarget := filepath.Join(glassboxRoot, relScanTarget)
	gbTarget := filepath.Join(glassboxRoot, relGbTargetCpp)
	if !fileExists(scanTarget) {
		http.Error(w, "scan_target.py missing at "+scanTarget, http.StatusInternalServerError)
		return
	}
	if !fileExists(gbTarget) {
		http.Error(w, "gb_target.cpp missing at "+gbTarget, http.StatusInternalServerError)
		return
	}
	py, pyArgs, err := resolvePython(glassboxRoot)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	targets, skipped, err := scanRepoForTargets(repo, req.Filter)
	if err != nil {
		http.Error(w, "scan: "+err.Error(), http.StatusInternalServerError)
		return
	}
	if len(targets) == 0 {
		// Build a structured, agent-actionable 400. The agent should NOT
		// retry start_hardware_audit on this repo; it should instead
		// surface the skipped examples + ABI hint to the user (or call
		// list_hardware_targets if it wants the full list).
		examples := skipped
		if len(examples) > maxSkippedExamples {
			examples = examples[:maxSkippedExamples]
		}
		skippedJSON := make([]map[string]string, 0, len(examples))
		for _, s := range examples {
			skippedJSON = append(skippedJSON, map[string]string{
				"rel_path": s.RelPath,
				"reason":   s.Reason,
			})
		}
		writeJSONError(w, http.StatusBadRequest, jsonErrorResponse{
			Error:     "no harness-compatible source files found in repo_root",
			ErrorCode: "no_targets",
			Details: map[string]any{
				"repo_root":             repo,
				"cpp_files_scanned":     len(skipped),
				"harness_compatible":    0,
				"skipped_count":         len(skipped),
				"skipped_examples":      skippedJSON,
				"filter_applied":        req.Filter,
				"harness_abi_signature": harnessABISignature,
				"harness_abi_header":    harnessABIHeaderPath,
			},
			Hint: "This repo contains no source files implementing the GlassBox " +
				"harness ABI. The harness only flashes files that define the " +
				"function shown in details.harness_abi_signature. Show the user " +
				"details.skipped_examples and details.harness_abi_signature so " +
				"they understand what's missing. Do NOT retry start_hardware_audit " +
				"on this repo -- either ask the user for a different repo or for " +
				"permission to add a gb_target_call(...) wrapper around an " +
				"existing function.",
			NextAction: "list_hardware_targets",
		})
		return
	}

	bridgeSecs := req.BridgeSeconds
	if bridgeSecs <= 0 {
		bridgeSecs = defaultBridgeSeconds
	}

	a := &audit{
		id:         fmt.Sprintf("audit-%d", time.Now().UnixNano()),
		repoRoot:   repo,
		scanTarget: scanTarget,
		python:     py,
		pythonArgs: pyArgs,
		bridgeSecs: bridgeSecs,
		espPort:    req.ESPPort,
		picoPort:   req.PicoPort,
		nPerGroup:  defaultNPerGroup,
		state:      "running",
		startedAt:  time.Now(),
		lastUpdate: time.Now(),
		targets:    targets,
		results:    make([]*targetResult, len(targets)),
	}
	for i, t := range targets {
		a.results[i] = &targetResult{
			Name:  t.Name,
			Path:  t.Path,
			State: "queued",
		}
	}

	if ok, existingID := s.tryStart(a); !ok {
		writeJSONError(w, http.StatusConflict, jsonErrorResponse{
			Error:     "another audit is already running on the shared hardware",
			ErrorCode: "audit_in_progress",
			Details: map[string]any{
				"existing_audit_id": existingID,
				"hint":              "poll get_hardware_audit_status (audit_id omitted) for progress, or cancel_hardware_audit before starting a new one",
			},
			Hint:       "Do NOT call start_hardware_audit again. Poll get_hardware_audit_status with the existing_audit_id from details, OR call cancel_hardware_audit first if the user explicitly asked you to abort.",
			NextAction: "get_hardware_audit_status",
		})
		return
	}

	go runAudit(a)

	names := make([]string, len(targets))
	for i, t := range targets {
		names[i] = t.Name
	}
	writeJSON(w, http.StatusOK, startAuditResponse{
		AuditID:    a.id,
		RepoRoot:   repo,
		Targets:    names,
		StartedAt:  a.startedAt.UTC().Format(time.RFC3339),
		StatusHint: "Poll /execute/get_hardware_audit_status with this audit_id (or no audit_id to get the latest).",
	})
}

func (s *server) handleStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	a := s.latest()
	if a == nil {
		http.Error(w, "no audit has been started", http.StatusNotFound)
		return
	}
	writeJSON(w, http.StatusOK, a.snapshot())
}

func (s *server) handleCancel(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	a := s.latest()
	if a == nil {
		http.Error(w, "no audit has been started", http.StatusNotFound)
		return
	}
	a.mu.Lock()
	cancel := a.cancel
	state := a.state
	a.mu.Unlock()
	if state != "running" {
		writeJSON(w, http.StatusOK, map[string]any{
			"audit_id": a.id, "state": state,
			"message": "audit not running; nothing to cancel",
		})
		return
	}
	if cancel != nil {
		cancel()
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"audit_id": a.id, "state": "cancelling",
		"message": "cancellation requested; subprocess will be killed shortly",
	})
}

// -----------------------------------------------------------------------------
// Audit execution (background goroutine)
// -----------------------------------------------------------------------------

func runAudit(a *audit) {
	defer func() {
		a.mu.Lock()
		if a.state == "running" {
			a.state = "done"
		}
		a.finishedAt = time.Now()
		passed, failed := 0, 0
		for _, r := range a.results {
			if r.Pass {
				passed++
			} else if r.State == "fail" || r.State == "skipped" || r.State == "cancelled" {
				failed++
			}
		}
		a.summary = fmt.Sprintf("%d passed, %d failed (of %d targets)", passed, failed, len(a.targets))
		a.mu.Unlock()
	}()

	for i, t := range a.targets {
		// Inter-target recovery: if previous target left the Pico stuck
		// in bridge mode, wait it out. Otherwise just a brief settle.
		if i > 0 {
			prev := a.results[i-1]
			var sleep time.Duration
			switch {
			case prev.BridgeLocked:
				sleep = time.Duration(a.bridgeSecs+defaultPostFailExtra) * time.Second
			case !prev.Pass:
				sleep = time.Duration(defaultPostFailExtra) * time.Second
			default:
				sleep = time.Duration(defaultPostSuccessSec) * time.Second
			}
			a.mu.Lock()
			a.results[i].State = "recovery_wait"
			a.lastUpdate = time.Now()
			a.mu.Unlock()
			if !sleepCancellable(a, sleep) {
				a.markCancelled(i)
				return
			}
		}

		if !runOneTarget(a, i, t) {
			// Cancellation observed; bail out.
			return
		}
	}
}

// runOneTarget invokes scan_target.py against ONE source file, parses
// its TargetReport JSON from stdout, and writes the result back into
// the audit. Returns false only when the audit was cancelled mid-run.
//
// scan_target.py owns gb_target.cpp install/restore itself; we don't
// pre-copy. The protocol is:
//
//	stdout: a single line of JSON (the TargetReport.to_dict() blob)
//	        emitted right at the end. With --out -, no file is written.
//	stderr: live progress -- "[scan] --- stage: <name> ---" markers
//	        plus per-stage chatter. We mirror the stage name to the
//	        agent via setStep() so the user can see "currently capturing
//	        traces" etc. Any familiar error substring (no Pico, esptool
//	        missing, bridge ack miss) gets sticky-flagged so we can give
//	        the agent a precise reason on failure.
func runOneTarget(a *audit, idx int, t targetInfo) bool {
	args := []string{
		"-u",
		a.scanTarget,
		t.Path,
		"--n", fmt.Sprintf("%d", a.nPerGroup),
		"--bridge-seconds", fmt.Sprintf("%d", a.bridgeSecs),
		"--out", "-",
	}
	if a.espPort != "" {
		args = append(args, "--esp-port", a.espPort)
	}
	if a.picoPort != "" {
		args = append(args, "--pico-port", a.picoPort)
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(defaultPerTargetSecs)*time.Second)
	a.mu.Lock()
	a.cancel = cancel
	a.mu.Unlock()
	defer func() {
		a.mu.Lock()
		a.cancel = nil
		a.mu.Unlock()
		cancel()
	}()

	fullArgs := append([]string{}, a.pythonArgs...)
	fullArgs = append(fullArgs, args...)
	cmd := exec.CommandContext(ctx, a.python, fullArgs...)
	cmd.Env = append(os.Environ(), "PYTHONUNBUFFERED=1")
	cmd.SysProcAttr = commandSysProcAttr()

	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		a.finishTarget(idx, false, "stdout pipe: "+err.Error(), true, nil)
		return true
	}
	stderrPipe, err := cmd.StderrPipe()
	if err != nil {
		a.finishTarget(idx, false, "stderr pipe: "+err.Error(), true, nil)
		return true
	}

	a.setStep(idx, "starting")
	if err := cmd.Start(); err != nil {
		a.finishTarget(idx, false, "start scan_target: "+err.Error(), false, nil)
		return true
	}

	// Drain stderr concurrently for progress + sticky error flags.
	type stderrSummary struct {
		bridgeMiss     bool
		esptoolMissing bool
		noPico         bool
		lastFatal      string
	}
	errCh := make(chan stderrSummary, 1)
	go func() {
		s := stderrSummary{}
		scanner := bufio.NewScanner(stderrPipe)
		scanner.Buffer(make([]byte, 64*1024), 4*1024*1024)
		for scanner.Scan() {
			line := scanner.Text()
			log.Printf("[audit %s][%d/%d %s][stderr] %s",
				a.id, idx+1, len(a.targets), t.Name, line)

			if strings.HasPrefix(line, scanStageMarker) {
				stage := strings.TrimPrefix(line, scanStageMarker)
				stage = strings.TrimSuffix(stage, " ---")
				stage = strings.TrimSpace(stage)
				if stage != "" {
					a.setStep(idx, stage)
				}
				continue
			}

			switch {
			case strings.Contains(line, bridgeAckMiss):
				s.bridgeMiss = true
			case strings.Contains(line, esptoolMissing):
				s.esptoolMissing = true
			case strings.Contains(line, noPicoDetected):
				s.noPico = true
			}
			if strings.Contains(line, "FATAL:") {
				if i := strings.Index(line, "FATAL:"); i >= 0 {
					s.lastFatal = strings.TrimSpace(line[i:])
				}
			}
		}
		errCh <- s
	}()

	// scan_target.py prints exactly one JSON blob to stdout (the
	// TargetReport). auto_flash.py and arduino-cli pipe their own
	// chatter through stdout too, though, so we log non-JSON lines
	// as they arrive (compile errors, esptool progress, etc. would
	// otherwise be invisible) and only buffer them for the JSON
	// parser at the end. Single-line JSON wire format means
	// stdoutBytes stays bounded.
	var stdoutBuf strings.Builder
	stdoutScanner := bufio.NewScanner(stdoutPipe)
	stdoutScanner.Buffer(make([]byte, 64*1024), 4*1024*1024)
	for stdoutScanner.Scan() {
		line := stdoutScanner.Text()
		stdoutBuf.WriteString(line)
		stdoutBuf.WriteByte('\n')
		// Only the report lands as a single-line JSON object; everything
		// else is build/flash chatter we want visible in the server log.
		if !strings.HasPrefix(strings.TrimSpace(line), "{") {
			log.Printf("[audit %s][%d/%d %s][stdout] %s",
				a.id, idx+1, len(a.targets), t.Name, line)
		}
	}
	stdoutBytes := []byte(stdoutBuf.String())
	waitErr := cmd.Wait()
	stderrSum := <-errCh

	if ctx.Err() == context.DeadlineExceeded {
		a.finishTarget(idx, false,
			fmt.Sprintf("hard timeout after %ds", defaultPerTargetSecs),
			true, nil)
		return true
	}
	if ctx.Err() == context.Canceled {
		a.markCancelled(idx)
		return false
	}

	rc := 0
	if waitErr != nil {
		var exitErr *exec.ExitError
		if errors.As(waitErr, &exitErr) {
			rc = exitErr.ExitCode()
		} else {
			a.finishTarget(idx, false, "wait: "+waitErr.Error(), true, nil)
			return true
		}
	}

	// Parse stdout JSON. scan_target.py exits 0 on a clean run AND on
	// "found bugs" -- the JSON is the source of truth, not the rc.
	report := parseScanReport(stdoutBytes)

	if report == nil {
		// Scanner didn't emit a TargetReport at all -- env-level failure.
		// Pick the most helpful reason we can from stderr.
		switch {
		case stderrSum.noPico:
			a.finishTarget(idx, false,
				"no Pico detected via USB; plug it in or pass --pico-port",
				true, nil)
		case stderrSum.esptoolMissing:
			a.finishTarget(idx, false,
				"esptool missing in this Python (run: pip install esptool)",
				true, nil)
		case stderrSum.bridgeMiss:
			a.finishTarget(idx, false,
				"Pico did not ACK BRIDGE (still locked from previous run?)",
				true, nil)
		case stderrSum.lastFatal != "":
			a.finishTarget(idx, false,
				"scan_target: "+stderrSum.lastFatal, true, nil)
		default:
			a.finishTarget(idx, false,
				fmt.Sprintf("scan_target rc=%d, no JSON report", rc),
				true, nil)
		}
		return true
	}

	// We have a real report. "Pass" at the audit level means the
	// verdict is `safe` (no findings worse than INFO/pass); anything
	// else is a fail.
	pass := report.Verdict == "safe"
	reason := fmt.Sprintf("verdict=%s worst=%s findings=%d",
		report.Verdict, report.WorstSeverity, len(report.Findings))
	a.finishTarget(idx, pass, reason, false, report)
	return true
}

// parseScanReport tolerates non-JSON noise on stdout (warnings, build
// chatter from misbehaving tools, etc.) by scanning for the LAST line
// that parses as a JSON object. scan_target.py's JSON is always one
// line and always last on stdout, so this is robust without us having
// to negotiate framing.
func parseScanReport(stdout []byte) *targetReportPayload {
	if len(stdout) == 0 {
		return nil
	}
	lines := strings.Split(string(stdout), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if line == "" || !strings.HasPrefix(line, "{") {
			continue
		}
		var rep targetReportPayload
		if err := json.Unmarshal([]byte(line), &rep); err != nil {
			continue
		}
		if rep.Verdict == "" && len(rep.Findings) == 0 {
			// Looked like JSON but isn't our schema; keep scanning.
			continue
		}
		return &rep
	}
	return nil
}

func (a *audit) markCancelled(idx int) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.state = "cancelled"
	a.errorMsg = "cancelled by user"
	now := time.Now().UTC().Format(time.RFC3339)
	for i := idx; i < len(a.results); i++ {
		// Skip targets that already reached a terminal state. Anything
		// else -- including the active target mid-stage (install/ct_lint/
		// flash/collect/analyze) -- needs to be marked cancelled so the
		// status snapshot doesn't leave a non-terminal entry hanging.
		st := a.results[i].State
		if st == "pass" || st == "fail" || st == "skipped" || st == "cancelled" {
			continue
		}
		a.results[i].State = "cancelled"
		if a.results[i].StartedAt == "" {
			a.results[i].StartedAt = now
		}
		a.results[i].FinishedAt = now
		if a.results[i].Reason == "" {
			a.results[i].Reason = "cancelled by user"
		}
	}
}

func sleepCancellable(a *audit, d time.Duration) bool {
	end := time.Now().Add(d)
	for time.Now().Before(end) {
		a.mu.Lock()
		state := a.state
		a.mu.Unlock()
		if state != "running" {
			return false
		}
		nap := time.Until(end)
		if nap > 500*time.Millisecond {
			nap = 500 * time.Millisecond
		}
		time.Sleep(nap)
	}
	return true
}

// -----------------------------------------------------------------------------
// Repo scan + helpers
// -----------------------------------------------------------------------------

func scanRepoForTargets(root, filter string) (matched, skipped []targetInfo, err error) {
	var re *regexp.Regexp
	if filter != "" {
		re, err = regexp.Compile(filter)
		if err != nil {
			return nil, nil, fmt.Errorf("bad filter regex: %w", err)
		}
	}

	err = filepath.Walk(root, func(p string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return nil
		}
		if info.IsDir() {
			name := info.Name()
			if name == ".git" || name == "node_modules" || name == ".venv" ||
				name == "venv" || name == "__pycache__" || name == "build" ||
				name == "bin" || name == "dist" {
				return filepath.SkipDir
			}
			return nil
		}
		ext := strings.ToLower(filepath.Ext(p))
		if ext != ".cpp" && ext != ".cc" && ext != ".cxx" && ext != ".c" {
			return nil
		}

		rel, _ := filepath.Rel(root, p)
		ti := targetInfo{
			Path:    p,
			RelPath: rel,
			Name:    info.Name(),
		}

		if re != nil && !re.MatchString(rel) {
			ti.Reason = "filtered out by --filter"
			ti.Excluded = true
			skipped = append(skipped, ti)
			return nil
		}

		// Read just enough to find the gb_target_call signature; cap the
		// read at 256 KiB so we don't slurp absurdly large generated
		// sources.
		data, readErr := readUpTo(p, 256*1024)
		if readErr != nil {
			ti.Reason = "read error: " + readErr.Error()
			ti.Excluded = true
			skipped = append(skipped, ti)
			return nil
		}
		if !gbTargetCallRegex.Match(data) {
			ti.Reason = "no gb_target_call(...) signature found"
			ti.Excluded = true
			skipped = append(skipped, ti)
			return nil
		}
		ti.Reason = "matches harness ABI"
		matched = append(matched, ti)
		return nil
	})
	if err != nil {
		return nil, nil, err
	}
	sort.Slice(matched, func(i, j int) bool { return matched[i].RelPath < matched[j].RelPath })
	sort.Slice(skipped, func(i, j int) bool { return skipped[i].RelPath < skipped[j].RelPath })
	return matched, skipped, nil
}

func readUpTo(path string, max int64) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return io.ReadAll(io.LimitReader(f, max))
}

// -----------------------------------------------------------------------------
// Path resolution + small helpers
// -----------------------------------------------------------------------------

func resolveGlassboxRoot() (string, error) {
	// Prefer the env var if set (the parent agent process can pin this).
	if v := os.Getenv("GLASSBOX_ROOT"); v != "" {
		if dirExists(filepath.Join(v, relScanTarget[:strings.LastIndex(relScanTarget, "/")])) {
			return v, nil
		}
	}
	// Otherwise, walk up from cwd.
	cwd, _ := os.Getwd()
	dir := cwd
	for i := 0; i < 8; i++ {
		if fileExists(filepath.Join(dir, relScanTarget)) {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	// Last resort: ask git.
	out, err := exec.Command("git", "rev-parse", "--show-toplevel").Output()
	if err == nil {
		return strings.TrimSpace(string(out)), nil
	}
	return "", fmt.Errorf("could not locate glassbox repo root from cwd=%s", cwd)
}

func resolvePython(glassboxRoot string) (string, []string, error) {
	if runtime.GOOS == "windows" {
		venv := filepath.Join(glassboxRoot, "glassbox", "backend", "hardware", "runner", ".venv", "Scripts", "python.exe")
		if fileExists(venv) {
			return venv, nil, nil
		}
		if p, err := exec.LookPath("py"); err == nil {
			return p, []string{"-3"}, nil
		}
		if p, err := exec.LookPath("python3"); err == nil {
			return p, nil, nil
		}
		if p, err := exec.LookPath("python"); err == nil {
			return p, nil, nil
		}
		return "", nil, errors.New("no python found (looked for runner/.venv/Scripts/python.exe, py -3, python3, python)")
	}

	venv := filepath.Join(glassboxRoot, relRunnerVenv)
	if fileExists(venv) {
		return venv, nil, nil
	}
	if p, err := exec.LookPath("python3"); err == nil {
		return p, nil, nil
	}
	if p, err := exec.LookPath("python"); err == nil {
		return p, nil, nil
	}
	return "", nil, errors.New("no python found (looked for runner/.venv/bin/python, python3, python)")
}

// collapseWhitespace replaces every run of whitespace (including newlines)
// with a single space and trims the ends. Used when embedding a parsed
// C/C++ signature inside a `//` comment so that multi-line declarations
// don't leak past the comment leader.
func collapseWhitespace(s string) string {
	return strings.Join(strings.Fields(s), " ")
}

func fileExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

func dirExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}

func writeJSON(w http.ResponseWriter, code int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(payload)
}

// writeJSONError sends a structured 4xx/5xx response. Use this whenever the
// agent might want to do something with the failure (recover, ask the user
// a clarifying question, switch tools) instead of just bubbling a string.
func writeJSONError(w http.ResponseWriter, code int, payload jsonErrorResponse) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(payload)
}

// -----------------------------------------------------------------------------
// Synthetic-target registration: turn (file, function) into a flashable
// gb_target.cpp shim. Called by the agent when list_hardware_targets
// returned zero native matches, so we never leave the user with "no
// findings, your repo isn't ready."
// -----------------------------------------------------------------------------

// fnParam is one parsed parameter from a C function declaration. We only
// distinguish the categories we need to pick a wrapper template; we do
// NOT try to be a full C++ parser.
type fnParam struct {
	Raw      string
	Category string // byte_ptr_const | byte_ptr_mut | len | len_ptr | other
}

type parsedSignature struct {
	ReturnType string
	Params     []fnParam
	Raw        string
}

type shapeUnsupportedError struct {
	Got        string
	GotParams  []string
	Supported  []string
	Suggestion string
}

func (e *shapeUnsupportedError) Error() string {
	return fmt.Sprintf("unsupported signature shape: got %s", e.Got)
}

// stripParamName removes the trailing identifier from a parameter token
// (so `const uint8_t* secret` becomes `const uint8_t*`). Defensive -- if
// there's no obvious split, returns the input unchanged.
func stripParamName(p string) string {
	p = strings.TrimSpace(p)
	if p == "" {
		return p
	}
	for {
		i := strings.LastIndexByte(p, '[')
		j := strings.LastIndexByte(p, ']')
		if i < 0 || j < 0 || j < i {
			break
		}
		p = strings.TrimSpace(p[:i] + p[j+1:])
	}
	cut := -1
	for i := len(p) - 1; i >= 0; i-- {
		c := p[i]
		if c == ' ' || c == '\t' || c == '*' || c == '&' {
			cut = i
			break
		}
	}
	if cut < 0 {
		return p
	}
	return strings.TrimSpace(p[:cut+1])
}

// classifyParam buckets one parameter token. Heuristic-based; we only
// need to recognize the canonical byte-buffer / length forms.
func classifyParam(raw string) fnParam {
	t := strings.TrimSpace(raw)
	t = strings.Join(strings.Fields(t), " ")
	out := fnParam{Raw: t, Category: "other"}

	typeOnly := stripParamName(t)
	low := strings.ToLower(typeOnly)

	switch {
	case strings.Contains(low, "const") &&
		(strings.Contains(low, "uint8_t*") ||
			strings.Contains(low, "uint8_t *") ||
			strings.Contains(low, "unsigned char*") ||
			strings.Contains(low, "unsigned char *") ||
			strings.Contains(low, "char*") ||
			strings.Contains(low, "char *")):
		out.Category = "byte_ptr_const"
	case strings.Contains(low, "uint8_t*") ||
		strings.Contains(low, "uint8_t *") ||
		strings.Contains(low, "unsigned char*") ||
		strings.Contains(low, "unsigned char *"):
		out.Category = "byte_ptr_mut"
	case strings.Contains(low, "size_t*") ||
		strings.Contains(low, "size_t *"):
		out.Category = "len_ptr"
	case low == "size_t" ||
		low == "unsigned int" ||
		low == "unsigned long" ||
		low == "int" ||
		low == "long":
		out.Category = "len"
	}
	return out
}

// splitTopLevelCommas splits a parameter list on commas, ignoring commas
// inside parens or template angle brackets. Cheap and good enough.
func splitTopLevelCommas(s string) []string {
	out := []string{}
	depth := 0
	last := 0
	for i := 0; i < len(s); i++ {
		switch s[i] {
		case '(', '<', '[':
			depth++
		case ')', '>', ']':
			if depth > 0 {
				depth--
			}
		case ',':
			if depth == 0 {
				out = append(out, strings.TrimSpace(s[last:i]))
				last = i + 1
			}
		}
	}
	tail := strings.TrimSpace(s[last:])
	if tail != "" {
		out = append(out, tail)
	}
	return out
}

// parseSimpleSignature finds and parses one declaration of fnName in src.
// Returns shapeUnsupportedError if found-but-unrecognized; returns a plain
// error if not found at all.
func parseSimpleSignature(src, fnName string) (*parsedSignature, error) {
	if !regexp.MustCompile(`\b` + regexp.QuoteMeta(fnName) + `\b`).MatchString(src) {
		return nil, fmt.Errorf("function %q not found in source file", fnName)
	}
	re, err := regexp.Compile(fmt.Sprintf(fnSigRegexFmt, regexp.QuoteMeta(fnName)))
	if err != nil {
		return nil, fmt.Errorf("internal: bad signature regex: %w", err)
	}
	m := re.FindStringSubmatch(src)
	if m == nil {
		return nil, fmt.Errorf(
			"function %q is present in source but no parseable forward decl/definition was found "+
				"(supported return types: int, bool, void, uint8_t, size_t, char, long; "+
				"single-line declarations only)",
			fnName,
		)
	}
	ret := strings.TrimSpace(m[1])
	paramsRaw := strings.TrimSpace(m[2])
	var params []fnParam
	if paramsRaw != "" && paramsRaw != "void" {
		for _, tok := range splitTopLevelCommas(paramsRaw) {
			params = append(params, classifyParam(tok))
		}
	}
	return &parsedSignature{
		ReturnType: ret,
		Params:     params,
		Raw:        strings.TrimSpace(m[0]),
	}, nil
}

// pickShape decides which wrapper template fits the parsed signature.
// Returns (shape, nil) on a match; (_, *shapeUnsupportedError) otherwise.
func pickShape(sig *parsedSignature) (string, error) {
	cats := make([]string, len(sig.Params))
	for i, p := range sig.Params {
		cats[i] = p.Category
	}
	switch {
	case len(cats) == 4 &&
		cats[0] == "byte_ptr_const" && cats[1] == "len" &&
		cats[2] == "byte_ptr_mut" && cats[3] == "len_ptr":
		return "harness_native", nil
	case len(cats) == 2 &&
		cats[0] == "byte_ptr_const" && cats[1] == "len":
		return "bytes_len", nil
	case len(cats) == 3 &&
		cats[0] == "byte_ptr_const" && cats[1] == "byte_ptr_const" && cats[2] == "len":
		return "comparator_len", nil
	}
	rawCats := make([]string, len(sig.Params))
	for i, p := range sig.Params {
		rawCats[i] = p.Raw
	}
	return "", &shapeUnsupportedError{
		Got:       fmt.Sprintf("%s(%s)", sig.ReturnType, strings.Join(rawCats, ", ")),
		GotParams: rawCats,
		Supported: []string{
			"int|bool|void f(const uint8_t* p, size_t n)",
			"int|bool|void f(const uint8_t* a, const uint8_t* b, size_t n)",
			"int f(const uint8_t* in, size_t in_len, uint8_t* out, size_t* out_len)",
		},
		Suggestion: "Pick a different function in the same repo whose signature matches " +
			"one of the supported shapes, or restructure the function under test to " +
			"take (const uint8_t* secret, size_t len).",
	}
}

// renderWrapper produces the full text of a synthesized gb_target.cpp.
// The user's source is pulled in via #include of an absolute path -- this
// is the simplest correct approach: no copy of function bodies, no risk
// of getting linkage wrong, and the compiler tells us immediately if the
// included file pulls in non-ESP-safe headers (we surface that error to
// the agent via the audit result).
func renderWrapper(shape, fnName, sourceAbs, targetName string,
	sig *parsedSignature, ref []byte) (string, error) {

	var b strings.Builder
	b.WriteString("// AUTO-GENERATED by hardwarego/register_synthetic_target.\n")
	b.WriteString("// Do not edit by hand -- re-register the target instead.\n")
	// sig.Raw can be a multi-line declaration (the original source may
	// have wrapped its parameters across lines). A bare `// Wrapper for:`
	// + raw signature would only `//`-prefix the first line, leaving the
	// rest as live C++ that the compiler tries to parse as a redeclaration
	// of the wrapped function -- and fails before ever reaching the
	// stdint.h include directive below it. Collapse to one line.
	b.WriteString("// Wrapper for: " + collapseWhitespace(sig.Raw) + "\n")
	b.WriteString("// Shape: " + shape + "\n\n")
	b.WriteString("#include <stddef.h>\n")
	b.WriteString("#include <stdint.h>\n")
	b.WriteString(fmt.Sprintf("#include %q\n\n", sourceAbs))
	b.WriteString("#ifdef __cplusplus\nextern \"C\" {\n#endif\n\n")

	switch shape {
	case "harness_native":
		b.WriteString(fmt.Sprintf(
			"int gb_target_call(const uint8_t* secret, size_t secret_len,\n"+
				"                  uint8_t* out, size_t* out_len) {\n"+
				"  return %s(secret, secret_len, out, out_len);\n"+
				"}\n\n", fnName))

	// NOTE on linkage: we DO NOT redeclare the wrapped function inside
	// our `extern "C" { ... }` block. The user's source is pulled in via
	// `#include` ABOVE the extern "C" block, so its declaration keeps
	// whatever linkage the user wrote (default C++ for .cpp files, C
	// when they used `extern "C"`). Redeclaring the same function with
	// C linkage inside our extern "C" block produced
	//   "conflicting declaration of 'X' with 'C' linkage"
	//   "previous declaration with 'C++' linkage"
	// errors at compile time. The wrapper body calls the function by
	// its source-side declaration, which is sufficient for both C and
	// C++ targets -- the calling-site doesn't need a duplicate decl.
	case "bytes_len":
		b.WriteString(fmt.Sprintf(
			"int gb_target_call(const uint8_t* secret, size_t secret_len,\n"+
				"                  uint8_t* out, size_t* out_len) {\n"+
				"  %s rc = %s(secret, secret_len);\n"+
				"  if (out && out_len && *out_len > 0) {\n"+
				"    out[0] = (uint8_t)((int)rc & 0xff);\n"+
				"    *out_len = 1;\n"+
				"  }\n"+
				"  return (int)rc;\n"+
				"}\n\n", sig.ReturnType, fnName))

	case "comparator_len":
		if len(ref) == 0 {
			return "", fmt.Errorf("comparator_len shape requires reference_hex (got empty)")
		}
		// Wrapper-internal constants get a __gb_ prefix so they can't
		// redefine same-named symbols in the wrapped source. The whole
		// source file is `#include`d above (so its translation unit is
		// merged with the wrapper's), and a user defining their own
		// `kReference` to compare against -- a very natural thing for
		// a byte-compare target to do -- would otherwise collide.
		b.WriteString("static const uint8_t __gb_reference[] = {")
		for i, by := range ref {
			if i > 0 {
				b.WriteString(", ")
			}
			b.WriteString(fmt.Sprintf("0x%02x", by))
		}
		b.WriteString("};\n")
		b.WriteString(fmt.Sprintf("static const size_t __gb_reference_len = %d;\n\n", len(ref)))
		b.WriteString(fmt.Sprintf(
			"int gb_target_call(const uint8_t* secret, size_t secret_len,\n"+
				"                  uint8_t* out, size_t* out_len) {\n"+
				"  size_t n = secret_len < __gb_reference_len ? secret_len : __gb_reference_len;\n"+
				"  %s rc = %s(secret, __gb_reference, n);\n"+
				"  if (out && out_len && *out_len > 0) {\n"+
				"    out[0] = (uint8_t)((int)rc & 0xff);\n"+
				"    *out_len = 1;\n"+
				"  }\n"+
				"  return (int)rc;\n"+
				"}\n\n", sig.ReturnType, fnName))

	default:
		return "", fmt.Errorf("internal: unknown shape %q", shape)
	}

	b.WriteString(fmt.Sprintf(
		"const char* gb_target_name(void) { return %q; }\n", targetName))
	b.WriteString("\n#ifdef __cplusplus\n}\n#endif\n")
	return b.String(), nil
}

// handleRegisterTarget is the new endpoint. Inputs are minimal (3 required
// fields, 1 optional); the heavy lifting -- parse, classify, render,
// write -- stays here so the agent doesn't have to ship a single line of
// C++.
func (s *server) handleRegisterTarget(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	var req registerTargetRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSONError(w, http.StatusBadRequest, jsonErrorResponse{
			Error: "bad json: " + err.Error(), ErrorCode: "bad_request",
		})
		return
	}
	repo := strings.TrimSpace(req.RepoRoot)
	srcRel := strings.TrimSpace(req.SourceFile)
	fn := strings.TrimSpace(req.FunctionName)
	if repo == "" || srcRel == "" || fn == "" {
		writeJSONError(w, http.StatusBadRequest, jsonErrorResponse{
			Error:     "repo_root, source_file, and function_name are all required",
			ErrorCode: "bad_request",
		})
		return
	}
	if !dirExists(repo) {
		writeJSONError(w, http.StatusBadRequest, jsonErrorResponse{
			Error: "repo_root does not exist: " + repo, ErrorCode: "bad_request",
		})
		return
	}

	srcAbs := filepath.Clean(filepath.Join(repo, srcRel))
	repoClean := filepath.Clean(repo)
	if srcAbs != repoClean &&
		!strings.HasPrefix(srcAbs, repoClean+string(os.PathSeparator)) {
		writeJSONError(w, http.StatusBadRequest, jsonErrorResponse{
			Error: "source_file must be inside repo_root", ErrorCode: "bad_request",
		})
		return
	}
	if !fileExists(srcAbs) {
		writeJSONError(w, http.StatusBadRequest, jsonErrorResponse{
			Error: "source_file does not exist: " + srcAbs, ErrorCode: "source_not_found",
		})
		return
	}

	cIdent := regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)
	if !cIdent.MatchString(fn) {
		writeJSONError(w, http.StatusBadRequest, jsonErrorResponse{
			Error:     "function_name is not a valid C identifier: " + fn,
			ErrorCode: "bad_request",
		})
		return
	}
	target := strings.TrimSpace(req.TargetName)
	if target == "" {
		target = fn
	}
	if !cIdent.MatchString(target) {
		writeJSONError(w, http.StatusBadRequest, jsonErrorResponse{
			Error:     "target_name is not a valid C identifier: " + target,
			ErrorCode: "bad_request",
		})
		return
	}

	src, err := readUpTo(srcAbs, maxRepoFileSize)
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, jsonErrorResponse{
			Error: "read source_file: " + err.Error(), ErrorCode: "io_error",
		})
		return
	}

	sig, err := parseSimpleSignature(string(src), fn)
	if err != nil {
		writeJSONError(w, http.StatusBadRequest, jsonErrorResponse{
			Error:     err.Error(),
			ErrorCode: "function_not_found",
			Hint: "Confirm function_name appears as a single-line declaration or " +
				"definition in source_file, with one of the supported return types " +
				"(int, bool, void, uint8_t, size_t, char, long).",
		})
		return
	}

	shape, err := pickShape(sig)
	if err != nil {
		var unsup *shapeUnsupportedError
		if errors.As(err, &unsup) {
			writeJSONError(w, http.StatusBadRequest, jsonErrorResponse{
				Error:     err.Error(),
				ErrorCode: "unsupported_signature",
				Details: map[string]any{
					"parsed_signature": sig.Raw,
					"got_params":       unsup.GotParams,
					"supported_shapes": unsup.Supported,
				},
				Hint: unsup.Suggestion,
			})
			return
		}
		writeJSONError(w, http.StatusInternalServerError, jsonErrorResponse{
			Error: "shape selection: " + err.Error(), ErrorCode: "internal_error",
		})
		return
	}

	var refBytes []byte
	if shape == "comparator_len" {
		refHex := strings.TrimSpace(req.ReferenceHex)
		if refHex == "" {
			writeJSONError(w, http.StatusBadRequest, jsonErrorResponse{
				Error:     "comparator_len shape requires reference_hex",
				ErrorCode: "missing_reference",
				Hint: "Provide reference_hex as the constant the function compares secret " +
					"against (the value an attacker is trying to recover). Example: " +
					"reference_hex=\"676c617373626f78\".",
			})
			return
		}
		refBytes, err = hex.DecodeString(refHex)
		if err != nil {
			writeJSONError(w, http.StatusBadRequest, jsonErrorResponse{
				Error:     "reference_hex is not valid hex: " + err.Error(),
				ErrorCode: "bad_request",
			})
			return
		}
		if len(refBytes) == 0 || len(refBytes) > maxReferenceLen {
			writeJSONError(w, http.StatusBadRequest, jsonErrorResponse{
				Error: fmt.Sprintf("reference_hex must decode to 1..%d bytes, got %d",
					maxReferenceLen, len(refBytes)),
				ErrorCode: "bad_request",
			})
			return
		}
	}

	wrapper, err := renderWrapper(shape, fn, srcAbs, target, sig, refBytes)
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, jsonErrorResponse{
			Error: "render wrapper: " + err.Error(), ErrorCode: "internal_error",
		})
		return
	}

	syntheticDir := filepath.Join(repo, syntheticDirName)
	if err := os.MkdirAll(syntheticDir, 0o755); err != nil {
		writeJSONError(w, http.StatusInternalServerError, jsonErrorResponse{
			Error: "mkdir synthetic dir: " + err.Error(), ErrorCode: "io_error",
		})
		return
	}
	wrapperPath := filepath.Join(syntheticDir, target+".cpp")
	if err := os.WriteFile(wrapperPath, []byte(wrapper), 0o644); err != nil {
		writeJSONError(w, http.StatusInternalServerError, jsonErrorResponse{
			Error: "write wrapper: " + err.Error(), ErrorCode: "io_error",
		})
		return
	}
	wrapperRel, _ := filepath.Rel(repo, wrapperPath)

	log.Printf("[register] %s::%s -> %s (shape=%s)", srcRel, fn, wrapperPath, shape)

	writeJSON(w, http.StatusOK, registerTargetResponse{
		RepoRoot:          repo,
		SourceFile:        srcRel,
		FunctionName:      fn,
		ShapeUsed:         shape,
		WrapperPath:       wrapperPath,
		WrapperRelPath:    wrapperRel,
		ParsedSignature:   sig.Raw,
		HarnessABISigUsed: harnessABISignature,
	})
}
