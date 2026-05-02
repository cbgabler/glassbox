package cleanup

import (
	"os"
	"path/filepath"
)

// resolveRunsDir returns the directory where run workspaces are stored.
// It mirrors the resolution used elsewhere: honor GLASSBOX_RUNS_DIR or
// default to ~/.glassbox/runs.
func resolveRunsDir() (string, error) {
	// Use a simple fixed directory for cloned repos to keep behavior deterministic
	// and avoid using per-user home locations.
	fixed := `C:\Users\antho\glassbox2\glassbox2\glassbox\backend\clonedrepos`
	return filepath.Clean(fixed), nil
}

// RemoveAllRuns removes all saved run workspaces. It attempts to delete the
// runs directory entirely and recreates an empty runs directory so future
// operations still have a valid location.
func RemoveAllRuns() error {
	runsDir, err := resolveRunsDir()
	if err != nil {
		return err
	}
	// If the runs directory doesn't exist, nothing to do.
	if _, err := os.Stat(runsDir); os.IsNotExist(err) {
		return nil
	}
	// Remove only the children of runsDir but keep runsDir itself
	entries, err := os.ReadDir(runsDir)
	if err != nil {
		return err
	}
	for _, e := range entries {
		p := filepath.Join(runsDir, e.Name())
		if err := os.RemoveAll(p); err != nil {
			return err
		}
	}
	return nil
}
