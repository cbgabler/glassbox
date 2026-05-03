package main

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const defaultMaxEntries = 500
const defaultMaxLines = 200

type CloneRequest struct {
	GitURL string `json:"git_url"`
	Path   string `json:"path"`
	Branch string `json:"branch"`
}

type CloneResponse struct {
	RepoRoot      string `json:"repo_root"`
	SourceType    string `json:"source_type"`
	Source        string `json:"source"`
	Branch        string `json:"branch,omitempty"`
	Commit        string `json:"commit,omitempty"`
	CreatedAt     string `json:"created_at"`
	AlreadyCloned bool   `json:"already_cloned,omitempty"`
}

type RepoContextRequest struct {
	RunID      string `json:"run_id"`
	GitURL     string `json:"git_url"`
	Branch     string `json:"branch"`
	Path       string `json:"path"`
	FilePath   string `json:"file_path"`
	MaxDepth   int    `json:"max_depth"`
	MaxEntries int    `json:"max_entries"`
	StartLine  int    `json:"start_line"`
	EndLine    int    `json:"end_line"`
}

type RepoEntry struct {
	Path string `json:"path"`
	Kind string `json:"kind"`
	Size int64  `json:"size,omitempty"`
}

type RepoTreeResponse struct {
	RepoRoot    string      `json:"repo_root"`
	BasePath    string      `json:"base_path"`
	Entries     []RepoEntry `json:"entries"`
	TotalCount  int         `json:"total_count"`
	Truncated   bool        `json:"truncated"`
	GeneratedAt string      `json:"generated_at"`
}

type FileLine struct {
	Number int    `json:"number"`
	Text   string `json:"text"`
}

type FileResponse struct {
	RepoRoot    string     `json:"repo_root"`
	FilePath    string     `json:"file_path"`
	StartLine   int        `json:"start_line"`
	EndLine     int        `json:"end_line"`
	TotalLines  int        `json:"total_lines"`
	Truncated   bool       `json:"truncated"`
	Lines       []FileLine `json:"lines"`
	GeneratedAt string     `json:"generated_at"`
}

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("/execute/clone_repo", handleCloneRepo)
	mux.HandleFunc("/execute/get_repo_context", handleGetRepoContext)
	mux.HandleFunc("/execute/get_file", handleGetFile)
	mux.HandleFunc("/health", handleHealth)

	port := ":8083"
	log.Printf("Starting Repo Context server on port %s\n", port)
	log.Fatal(http.ListenAndServe(port, mux))
}

func handleHealth(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func handleCloneRepo(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req CloneRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}

	req.GitURL = strings.TrimSpace(req.GitURL)
	req.Path = strings.TrimSpace(req.Path)
	req.Branch = strings.TrimSpace(req.Branch)

	if (req.GitURL == "" && req.Path == "") || (req.GitURL != "" && req.Path != "") {
		http.Error(w, "Provide exactly one of git_url or path", http.StatusBadRequest)
		return
	}

	// Use the fixed cloned repos directory as the single repo workspace.
	runsDir, err := resolveRunsDir()
	if err != nil {
		http.Error(w, fmt.Sprintf("Failed to resolve runs dir: %v", err), http.StatusInternalServerError)
		return
	}
	repoRoot := runsDir

	// If something already exists there, report it and don't reclone.
	if entries, _ := os.ReadDir(repoRoot); len(entries) > 0 {
		// Optionally, we could validate this looks like a repo, but return as-is.
		commit, _, _ := gitSnapshot(repoRoot)
		writeJSON(w, http.StatusOK, CloneResponse{
			RepoRoot:      repoRoot,
			SourceType:    "existing",
			Source:        "",
			Commit:        commit,
			CreatedAt:     time.Now().UTC().Format(time.RFC3339),
			AlreadyCloned: true,
		})
		return
	}

	// Ensure directory exists
	if err := os.MkdirAll(repoRoot, 0o755); err != nil {
		http.Error(w, fmt.Sprintf("Failed to create repo dir: %v", err), http.StatusInternalServerError)
		return
	}

	if req.GitURL != "" {
		if err := cloneGitRepo(repoRoot, req.GitURL, req.Branch); err != nil {
			http.Error(w, fmt.Sprintf("Failed to clone repo: %v", err), http.StatusBadRequest)
			return
		}
	} else {
		if err := copyLocalRepo(repoRoot, req.Path); err != nil {
			http.Error(w, fmt.Sprintf("Failed to copy repo: %v", err), http.StatusBadRequest)
			return
		}
	}

	commit, _, _ := gitSnapshot(repoRoot)
	writeJSON(w, http.StatusOK, CloneResponse{
		RepoRoot:   repoRoot,
		SourceType: "cloned",
		Source: func() string {
			if req.GitURL != "" {
				return req.GitURL
			}
			return req.Path
		}(),
		Commit:        commit,
		CreatedAt:     time.Now().UTC().Format(time.RFC3339),
		AlreadyCloned: false,
	})
}

func handleGetRepoContext(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req RepoContextRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}

	req.GitURL = strings.TrimSpace(req.GitURL)
	req.Path = strings.TrimSpace(req.Path)
	req.FilePath = strings.TrimSpace(req.FilePath)

	runsDir, err := resolveRunsDir()
	if err != nil {
		http.Error(w, fmt.Sprintf("Failed to resolve runs dir: %v", err), http.StatusInternalServerError)
		return
	}
	repoRoot := runsDir

	entries, _ := os.ReadDir(repoRoot)
	if len(entries) == 0 && req.FilePath != "" {
		http.Error(w, "No repository available. Clone a repository first before reading files.", http.StatusBadRequest)
		return
	}

	// If nothing cloned yet, allow cloning via git_url or path
	if entries, _ := os.ReadDir(repoRoot); len(entries) == 0 {
		if req.GitURL == "" && req.Path == "" {
			http.Error(w, "No repository present and missing git_url/path", http.StatusBadRequest)
			return
		}
		if err := os.MkdirAll(repoRoot, 0o755); err != nil {
			http.Error(w, fmt.Sprintf("Failed to create repo dir: %v", err), http.StatusInternalServerError)
			return
		}
		if req.GitURL != "" {
			if err := cloneGitRepo(repoRoot, req.GitURL, req.Branch); err != nil {
				http.Error(w, fmt.Sprintf("Failed to clone repo: %v", err), http.StatusBadRequest)
				return
			}
		} else {
			if err := copyLocalRepo(repoRoot, req.Path); err != nil {
				http.Error(w, fmt.Sprintf("Failed to copy repo: %v", err), http.StatusBadRequest)
				return
			}
		}
	}

	if req.FilePath != "" {
		resp, err := readRepoFile(req, repoRoot)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		writeJSON(w, http.StatusOK, resp)
		return
	}

	// reuse repoRoot as runRoot for compatibility with buildRepoTree
	resp, err := buildRepoTree(req, repoRoot, repoRoot)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	writeJSON(w, http.StatusOK, resp)
}

// handleGetFile reads a single file from an existing run and returns FileResponse
func handleGetFile(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var req RepoContextRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}
	req.FilePath = strings.TrimSpace(req.FilePath)
	if req.FilePath == "" {
		http.Error(w, "file_path is required", http.StatusBadRequest)
		return
	}
	repoRoot, err := resolveRunsDir()
	if err != nil {
		http.Error(w, fmt.Sprintf("Failed to resolve runs dir: %v", err), http.StatusInternalServerError)
		return
	}
	// ensure repo exists
	if info, err := os.Stat(repoRoot); err != nil || !info.IsDir() {
		http.Error(w, "no repository available", http.StatusNotFound)
		return
	}
	resp, err := readRepoFile(req, repoRoot)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	writeJSON(w, http.StatusOK, resp)
}

func createRunWorkspace() (string, string, string, error) {
	// not used in simplified flow
	return "", "", "", fmt.Errorf("createRunWorkspace is deprecated in simplified mode")
}

// findExistingRunBySource scans the runs directory for a run whose metadata
// matches the provided source (git URL or local path). If found, returns
// the runID, runRoot, repoRoot and the metadata.
// findExistingRunBySource removed in simplified flow

func resolveRunsDir() (string, error) {
	// Where cloned repos live. Resolution order:
	//   1. CLONEDREPOS_DIR env var (operator override)
	//   2. <glassbox_root>/glassbox/backend/clonedrepos (cross-platform default)
	//   3. <cwd>/clonedrepos as a last resort
	if v := strings.TrimSpace(os.Getenv("CLONEDREPOS_DIR")); v != "" {
		abs, err := filepath.Abs(v)
		if err == nil {
			return filepath.Clean(abs), nil
		}
	}
	out, err := exec.Command("git", "rev-parse", "--show-toplevel").Output()
	if err == nil {
		root := strings.TrimSpace(string(out))
		if root != "" {
			return filepath.Clean(filepath.Join(root, "glassbox", "backend", "clonedrepos")), nil
		}
	}
	cwd, err := os.Getwd()
	if err != nil {
		return "", err
	}
	return filepath.Clean(filepath.Join(cwd, "clonedrepos")), nil
}

func cloneGitRepo(repoRoot, gitURL, branch string) error {
	args := []string{"clone"}
	if branch != "" {
		args = append(args, "--branch", branch)
	}
	args = append(args, "--", gitURL, repoRoot)
	cmd := exec.Command("git", args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func copyLocalRepo(destRoot, sourcePath string) error {
	sourcePath = filepath.Clean(sourcePath)
	info, err := os.Stat(sourcePath)
	if err != nil {
		return err
	}
	if !info.IsDir() {
		return fmt.Errorf("path is not a directory: %s", sourcePath)
	}

	return filepath.WalkDir(sourcePath, func(currentPath string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}

		relPath, err := filepath.Rel(sourcePath, currentPath)
		if err != nil {
			return err
		}
		if relPath == "." {
			return nil
		}

		if shouldSkipPath(relPath) {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}

		targetPath := filepath.Join(destRoot, relPath)
		if d.IsDir() {
			return os.MkdirAll(targetPath, 0o755)
		}

		if err := os.MkdirAll(filepath.Dir(targetPath), 0o755); err != nil {
			return err
		}

		return copyFile(currentPath, targetPath)
	})
}

func shouldSkipPath(relPath string) bool {
	parts := strings.Split(filepath.ToSlash(relPath), "/")
	ignored := map[string]struct{}{
		".git":         {},
		"node_modules": {},
		".venv":        {},
		"venv":         {},
		"dist":         {},
		"build":        {},
		"target":       {},
		"out":          {},
		"coverage":     {},
		"__pycache__":  {},
		".next":        {},
	}
	for _, part := range parts {
		if _, ok := ignored[part]; ok {
			return true
		}
	}
	return false
}

func copyFile(sourcePath, targetPath string) error {
	input, err := os.ReadFile(sourcePath)
	if err != nil {
		return err
	}
	info, err := os.Stat(sourcePath)
	if err != nil {
		return err
	}
	return os.WriteFile(targetPath, input, info.Mode())
}

func gitSnapshot(repoRoot string) (string, string, error) {
	commit, err := runGit(repoRoot, "rev-parse", "HEAD")
	if err != nil {
		return "", "", err
	}
	branch, err := runGit(repoRoot, "branch", "--show-current")
	if err != nil {
		branch = ""
	}
	return strings.TrimSpace(commit), strings.TrimSpace(branch), nil
}

func runGit(repoRoot string, args ...string) (string, error) {
	cmdArgs := append([]string{"-C", repoRoot}, args...)
	cmd := exec.Command("git", cmdArgs...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("git %s failed: %s", strings.Join(args, " "), strings.TrimSpace(string(output)))
	}
	return string(output), nil
}

func buildRepoTree(req RepoContextRequest, runRoot, repoRoot string) (RepoTreeResponse, error) {
	basePath, err := resolveRepoPath(repoRoot, req.Path)
	if err != nil {
		return RepoTreeResponse{}, err
	}
	info, err := os.Stat(basePath)
	if err != nil {
		return RepoTreeResponse{}, err
	}
	if !info.IsDir() {
		return RepoTreeResponse{}, fmt.Errorf("path is not a directory: %s", req.Path)
	}

	maxEntries := req.MaxEntries
	if maxEntries <= 0 {
		maxEntries = defaultMaxEntries
	}
	maxDepth := req.MaxDepth
	if maxDepth <= 0 {
		maxDepth = 4 // was 3
	}

	entries := make([]RepoEntry, 0, min(maxEntries, 512)) // was 256

	total := 0
	truncated := false

	err = filepath.WalkDir(basePath, func(currentPath string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}

		if currentPath == basePath {
			return nil
		}

		relPath, err := filepath.Rel(repoRoot, currentPath)
		if err != nil {
			return err
		}
		if shouldSkipPath(relPath) {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}

		depth := strings.Count(filepath.ToSlash(relPath), "/") + 1
		if depth > maxDepth {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}

		total++
		if len(entries) >= maxEntries {
			truncated = true
			return fs.SkipAll
		}

		entry := RepoEntry{Path: filepath.ToSlash(relPath)}
		if d.IsDir() {
			entry.Kind = "dir"
		} else {
			entry.Kind = "file"
			if fileInfo, statErr := d.Info(); statErr == nil {
				entry.Size = fileInfo.Size()
			}
		}
		entries = append(entries, entry)
		return nil
	})
	if err != nil {
		return RepoTreeResponse{}, err
	}

	sort.Slice(entries, func(i, j int) bool {
		return entries[i].Path < entries[j].Path
	})

	baseLabel := strings.TrimPrefix(filepath.ToSlash(strings.TrimPrefix(basePath, repoRoot)), "/")
	if baseLabel == "." {
		baseLabel = ""
	}

	return RepoTreeResponse{
		RepoRoot:    repoRoot,
		BasePath:    baseLabel,
		Entries:     entries,
		TotalCount:  total,
		Truncated:   truncated,
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func readRepoFile(req RepoContextRequest, repoRoot string) (FileResponse, error) {
	filePath, err := resolveRepoPath(repoRoot, req.FilePath)
	if err != nil {
		return FileResponse{}, err
	}
	info, err := os.Stat(filePath)
	if err != nil {
		return FileResponse{}, err
	}
	if info.IsDir() {
		return FileResponse{}, fmt.Errorf("path is a directory: %s", req.FilePath)
	}

	content, err := os.ReadFile(filePath)
	if err != nil {
		return FileResponse{}, err
	}

	startLine := req.StartLine
	if startLine <= 0 {
		startLine = 1
	}
	endLine := req.EndLine
	if endLine <= 0 {
		endLine = startLine + defaultMaxLines - 1
	}
	if endLine < startLine {
		return FileResponse{}, fmt.Errorf("end_line must be >= start_line")
	}

	allLines := strings.Split(strings.ReplaceAll(string(content), "\r\n", "\n"), "\n")
	totalLines := len(allLines)
	if totalLines > 0 && allLines[totalLines-1] == "" && strings.HasSuffix(string(content), "\n") {
		totalLines--
		allLines = allLines[:totalLines]
	}
	if startLine > totalLines {
		return FileResponse{}, fmt.Errorf("file has fewer than %d lines", startLine)
	}

	if endLine > totalLines {
		endLine = totalLines
	}
	lines := make([]FileLine, 0, endLine-startLine+1)
	for lineNumber := startLine; lineNumber <= endLine; lineNumber++ {
		lines = append(lines, FileLine{Number: lineNumber, Text: allLines[lineNumber-1]})
	}

	return FileResponse{
		RepoRoot:    repoRoot,
		FilePath:    filepath.ToSlash(req.FilePath),
		StartLine:   startLine,
		EndLine:     endLine,
		TotalLines:  totalLines,
		Truncated:   req.EndLine > 0 && req.EndLine < totalLines,
		Lines:       lines,
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func resolveRepoPath(repoRoot, relPath string) (string, error) {
	if relPath == "" {
		return filepath.Clean(repoRoot), nil
	}
	cleaned := filepath.Clean(filepath.FromSlash(relPath))
	fullPath := filepath.Clean(filepath.Join(repoRoot, cleaned))
	rel, err := filepath.Rel(repoRoot, fullPath)
	if err != nil {
		return "", err
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("path escapes repo root: %s", relPath)
	}
	return fullPath, nil
}

func writeJSON(w http.ResponseWriter, statusCode int, payload interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	_ = json.NewEncoder(w).Encode(payload)
}
