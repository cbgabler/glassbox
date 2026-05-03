package cleanup

import (
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// resolveRunsDir returns the directory where cloned repos live. Resolution order
// must stay in sync with repocontextserver.resolveRunsDir so cleanup actually
// targets the same workspace that clone_repo writes to.
//
//  1. CLONEDREPOS_DIR env var (operator override)
//  2. <git_toplevel>/glassbox/backend/clonedrepos (works on any checkout)
//  3. <cwd>/clonedrepos (last resort, e.g. binary launched outside a git tree)
func resolveRunsDir() (string, error) {
	if v := strings.TrimSpace(os.Getenv("CLONEDREPOS_DIR")); v != "" {
		abs, err := filepath.Abs(v)
		if err == nil {
			return filepath.Clean(abs), nil
		}
	}
	if out, err := exec.Command("git", "rev-parse", "--show-toplevel").Output(); err == nil {
		if root := strings.TrimSpace(string(out)); root != "" {
			return filepath.Clean(filepath.Join(root, "glassbox", "backend", "clonedrepos")), nil
		}
	}
	cwd, err := os.Getwd()
	if err != nil {
		return "", err
	}
	return filepath.Clean(filepath.Join(cwd, "clonedrepos")), nil
}

// RemoveAllRuns removes all saved run workspaces. It attempts to delete the
// runs directory entirely and recreates an empty runs directory so future
// operations still have a valid location.
func RemoveAllRuns() error {
	runsDir, err := resolveRunsDir()
	if err != nil {
		return err
	}
	os.RemoveAll(runsDir) // best effort to remove entire directory first, in case there are nested contents that would error on Windows
	if err := os.MkdirAll(runsDir, 0755); err != nil {
		return err
	}
	log.Printf("cleanup: finished removing cloned repos contents")
	return nil
}
