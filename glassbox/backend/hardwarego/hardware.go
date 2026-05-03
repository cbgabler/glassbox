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
	"sort"
	"strings"
	"sync"
	"syscall"
	"time"
)

// -----------------------------------------------------------------------------
// Repo-relative paths (resolved against the auto-detected glassbox root)
// -----------------------------------------------------------------------------

const (
	relAutoFlash   = "glassbox/backend/hardware/runner/auto_flash.py"
	relGbTargetCpp = "glassbox/backend/hardware/esp/harness/gb_target.cpp"
	relRunnerVenv  = "glassbox/backend/hardware/runner/.venv/bin/python"

	verifyOKMarker = "post-flash verification OK"
	verifyFailMark = "post-flash verification FAILED"
	verifyTimeMark = "post-flash verification TIMEOUT"
	bridgeAckMiss  = "Pico did not enter bridge mode"
	esptoolMissing = "No module named esptool"

	defaultBridgeSeconds  = 90
	defaultPerTargetSecs  = 240
	defaultPostSuccessSec = 2
	defaultPostFailExtra  = 5

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
//   int byte_compare(const uint8_t* a, const uint8_t* b, size_t n)
//   void  aes_block( const uint8_t key[16], const uint8_t in[16], uint8_t out[16] )
//   extern "C" int gb_target_call(const uint8_t* s, size_t n, uint8_t* o, size_t* ol)
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

type targetResult struct {
	Name         string  `json:"name"`
	Path         string  `json:"path"`
	State        string  `json:"state"`           // queued|copy|compile|esptool|booting|verifying|pass|fail|skipped|cancelled
	Pass         bool    `json:"pass"`
	Reason       string  `json:"reason,omitempty"`
	StartedAt    string  `json:"started_at,omitempty"`
	FinishedAt   string  `json:"finished_at,omitempty"`
	DurationSecs float64 `json:"duration_secs,omitempty"`
	BridgeLocked bool    `json:"bridge_locked,omitempty"`
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
	CurrentIndex  int            `json:"current_index"`  // 1-based; 0 if not yet started
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
	ShapeUsed         string `json:"shape_used"`     // bytes_len | comparator_len | harness_native
	WrapperPath       string `json:"wrapper_path"`   // absolute
	WrapperRelPath    string `json:"wrapper_rel_path"` // relative to repo_root
	ParsedSignature   string `json:"parsed_signature"`
	HarnessABISigUsed string `json:"harness_abi_signature_used"`
}

// jsonErrorResponse is the shape we return for actionable failures (notably
// "no harness-compatible sources" on start_hardware_audit). The MCP-side
// agent gets a stable schema instead of a single line of plain text:
//
//   - Error      : the same human string the old http.Error returned, so
//                  any caller still grepping for it keeps working.
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
	autoFlash  string
	python     string
	gbTarget   string
	gbBackup   string
	bridgeSecs int
	espPort    string
	picoPort   string

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

func (a *audit) finishTarget(idx int, pass bool, reason string, bridgeLocked bool) {
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
	if pass {
		r.State = "pass"
	} else {
		r.State = "fail"
	}
	a.lastUpdate = time.Now()
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

func (s *server) tryStart(a *audit) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.current != nil && s.current.state == "running" {
		return false
	}
	s.current = a
	return true
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
	autoFlash := filepath.Join(glassboxRoot, relAutoFlash)
	gbTarget := filepath.Join(glassboxRoot, relGbTargetCpp)
	if !fileExists(autoFlash) {
		http.Error(w, "auto_flash.py missing at "+autoFlash, http.StatusInternalServerError)
		return
	}
	if !fileExists(gbTarget) {
		http.Error(w, "gb_target.cpp missing at "+gbTarget, http.StatusInternalServerError)
		return
	}
	py, err := resolvePython(glassboxRoot)
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
		autoFlash:  autoFlash,
		python:     py,
		gbTarget:   gbTarget,
		bridgeSecs: bridgeSecs,
		espPort:    req.ESPPort,
		picoPort:   req.PicoPort,
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

	if !s.tryStart(a) {
		http.Error(w, "another audit is already running; cancel it first or wait", http.StatusConflict)
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
		restoreHarnessTarget(a)
	}()

	if err := backupHarnessTarget(a); err != nil {
		a.mu.Lock()
		a.state = "failed"
		a.errorMsg = "backup gb_target.cpp: " + err.Error()
		a.mu.Unlock()
		return
	}

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

// runOneTarget returns false if the audit was cancelled mid-target.
func runOneTarget(a *audit, idx int, t targetInfo) bool {
	a.setStep(idx, "copy")
	if err := copyFile(t.Path, a.gbTarget); err != nil {
		a.finishTarget(idx, false, "copy into harness failed: "+err.Error(), false)
		return true
	}

	args := []string{
		"-u",
		a.autoFlash,
		"--via-pico",
		"--bridge-seconds", fmt.Sprintf("%d", a.bridgeSecs),
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

	cmd := exec.CommandContext(ctx, a.python, args...)
	cmd.Env = append(os.Environ(), "PYTHONUNBUFFERED=1")
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		a.finishTarget(idx, false, "stdout pipe: "+err.Error(), true)
		return true
	}
	cmd.Stderr = cmd.Stdout

	a.setStep(idx, "compile")
	if err := cmd.Start(); err != nil {
		a.finishTarget(idx, false, "start auto_flash: "+err.Error(), false)
		return true
	}

	sawVerifyOK := false
	sawVerifyFail := false
	sawBridgeMiss := false
	sawEsptoolMissing := false
	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 64*1024), 4*1024*1024)
	for scanner.Scan() {
		line := scanner.Text()
		log.Printf("[audit %s][%d/%d %s] %s", a.id, idx+1, len(a.targets), t.Name, line)

		switch {
		case strings.Contains(line, "esptool: write_flash"),
			strings.Contains(line, "esptool: flash OK"):
			a.setStep(idx, "esptool")
		case strings.Contains(line, "waiting") && strings.Contains(line, "bridge auto-exit"):
			a.setStep(idx, "booting")
		case strings.Contains(line, "verifying via Pico"):
			a.setStep(idx, "verifying")
		}
		switch {
		case strings.Contains(line, verifyOKMarker):
			sawVerifyOK = true
		case strings.Contains(line, verifyFailMark),
			strings.Contains(line, verifyTimeMark):
			sawVerifyFail = true
		case strings.Contains(line, bridgeAckMiss):
			sawBridgeMiss = true
		case strings.Contains(line, esptoolMissing):
			sawEsptoolMissing = true
		}
	}
	waitErr := cmd.Wait()

	if ctx.Err() == context.DeadlineExceeded {
		a.finishTarget(idx, false, fmt.Sprintf("hard timeout after %ds", defaultPerTargetSecs), true)
		return true
	}
	// User-cancellation: ctx was cancelled but not by deadline -> by handleCancel.
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
			a.finishTarget(idx, false, "wait: "+waitErr.Error(), true)
			return true
		}
	}

	switch {
	case rc == 0 && sawVerifyOK:
		a.finishTarget(idx, true, "verify OK", false)
	case rc == 0 && !sawVerifyOK:
		a.finishTarget(idx, true, "rc=0 (verify marker missing)", false)
	case sawVerifyFail:
		a.finishTarget(idx, false, fmt.Sprintf("verify failed (rc=%d)", rc), true)
	case sawEsptoolMissing:
		a.finishTarget(idx, false, "esptool missing in this Python (run: pip install esptool)", true)
	case sawBridgeMiss:
		a.finishTarget(idx, false, "Pico did not ACK BRIDGE (likely still locked from previous attempt)", true)
	default:
		a.finishTarget(idx, false, fmt.Sprintf("auto_flash rc=%d", rc), true)
	}
	return true
}

func (a *audit) markCancelled(idx int) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.state = "cancelled"
	a.errorMsg = "cancelled by user"
	for i := idx; i < len(a.results); i++ {
		if a.results[i].State == "queued" || a.results[i].State == "recovery_wait" || a.results[i].State == "" {
			a.results[i].State = "cancelled"
			a.results[i].FinishedAt = time.Now().UTC().Format(time.RFC3339)
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
// Harness backup/restore
// -----------------------------------------------------------------------------

func backupHarnessTarget(a *audit) error {
	tmp, err := os.CreateTemp("", "gb_target.cpp.bak.*")
	if err != nil {
		return err
	}
	tmp.Close()
	if err := copyFile(a.gbTarget, tmp.Name()); err != nil {
		os.Remove(tmp.Name())
		return err
	}
	a.mu.Lock()
	a.gbBackup = tmp.Name()
	a.mu.Unlock()
	log.Printf("[audit %s] backed up %s -> %s", a.id, a.gbTarget, tmp.Name())
	return nil
}

func restoreHarnessTarget(a *audit) {
	a.mu.Lock()
	bak := a.gbBackup
	a.gbBackup = ""
	a.mu.Unlock()
	if bak == "" {
		return
	}
	if err := copyFile(bak, a.gbTarget); err != nil {
		log.Printf("[audit %s] WARNING: failed to restore harness: %v", a.id, err)
		return
	}
	os.Remove(bak)
	log.Printf("[audit %s] restored harness gb_target.cpp", a.id)
}

// -----------------------------------------------------------------------------
// Path resolution + small helpers
// -----------------------------------------------------------------------------

func resolveGlassboxRoot() (string, error) {
	// Prefer the env var if set (the parent agent process can pin this).
	if v := os.Getenv("GLASSBOX_ROOT"); v != "" {
		if dirExists(filepath.Join(v, relAutoFlash[:strings.LastIndex(relAutoFlash, "/")])) {
			return v, nil
		}
	}
	// Otherwise, walk up from cwd.
	cwd, _ := os.Getwd()
	dir := cwd
	for i := 0; i < 8; i++ {
		if fileExists(filepath.Join(dir, relAutoFlash)) {
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

func resolvePython(glassboxRoot string) (string, error) {
	venv := filepath.Join(glassboxRoot, relRunnerVenv)
	if fileExists(venv) {
		return venv, nil
	}
	if p, err := exec.LookPath("python3"); err == nil {
		return p, nil
	}
	if p, err := exec.LookPath("python"); err == nil {
		return p, nil
	}
	return "", errors.New("no python found (looked for runner/.venv/bin/python, python3, python)")
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, in)
	return err
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
