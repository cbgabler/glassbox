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
	log.Printf("cleanup: inspecting runs directory: %s", runsDir)
	// If the runs directory doesn't exist, nothing to do.
	if _, err := os.Stat(runsDir); os.IsNotExist(err) {
		log.Printf("cleanup: runs directory does not exist, nothing to remove")
		return nil
	}
	// Remove only the children of runsDir but keep runsDir itself
	entries, err := os.ReadDir(runsDir)
	if err != nil {
		return err
	}
	log.Printf("cleanup: found %d entries to remove", len(entries))
	for _, e := range entries {
		p := filepath.Join(runsDir, e.Name())
		log.Printf("cleanup: removing %s", p)
		if err := os.RemoveAll(p); err != nil {
			return err
		}
	}
	log.Printf("cleanup: finished removing cloned repos contents")
	return nil
}
