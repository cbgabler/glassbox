// agent/launcher.go
package main

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"strings"

	agent "github.com/AnthonyL103/GOMCP/Agent"
	"github.com/AnthonyL103/GOMCP/server"
)

func buildcommand(config *server.RuntimeConfig) (string, []string, error) {
	typ := strings.ToLower(strings.TrimSpace(config.Type))
	cmd := strings.TrimSpace(config.Command)
	args := append([]string{}, config.Args...) // copy

	if cmd == "" {
		return "", nil, fmt.Errorf("runtime.command is empty")
	}

	switch typ {
	case "go":
		// Expect args[0] = entrypoint (.go file OR package path)
		// Turn: go <entry> ...  into  go run <entry> ...
		if len(args) == 0 {
			return "", nil, fmt.Errorf("go runtime requires args[0] entrypoint (e.g. path/to/main.go or ./cmd/server)")
		}
		// If user already included "run", don't double it
		if args[0] != "run" {
			args = append([]string{"run"}, args...)
		}
		return cmd, args, nil

	case "python":
		// If command is "python"/"python3"/"py", args can be:
		// - ["path/to/server.py", ...]
		// - ["-m", "module.name", ...]
		// If user gives just ["module.name", ...], we convert to ["-m", "module.name", ...]
		if len(args) == 0 {
			return "", nil, fmt.Errorf("python runtime requires args (script path or -m module)")
		}
		if args[0] != "-m" && !strings.HasSuffix(strings.ToLower(args[0]), ".py") {
			// Treat as module
			args = append([]string{"-m"}, args...)
		}
		return cmd, args, nil

	default:
		// For node/ruby/etc., assume config.command + config.args is already executable form
		return cmd, args, nil
	}
}

// StartServer launches a server process and returns the process handle
func StartServer(srv *server.MCPServer) (*os.Process, error) {
	config := srv.RuntimeConfig
	log.Printf("Starting server '%s' on port %d (%s)", srv.ServerID, config.Port, config.Type)

	exe, args, err := buildcommand(config)
	if err != nil {
		return nil, fmt.Errorf("failed to build command for server %s: %w", srv.ServerID, err)
	}

	cmd := exec.Command(exe, args...)
	cmd.Env = os.Environ() // Inherit all env vars including loaded .env
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("failed to start server %s: %w", srv.ServerID, err)
	}

	log.Printf("Server '%s' started (PID: %d)", srv.ServerID, cmd.Process.Pid)
	return cmd.Process, nil
}

// StartAllServers launches all servers and returns their process handles
func StartAllServers(ag *agent.Agent) ([]*os.Process, error) {
	processes := []*os.Process{}

	for serverID, srv := range ag.Registry.Servers {
		proc, err := StartServer(srv)
		if err != nil {
			// Kill already started processes on error
			for _, p := range processes {
				p.Kill()
			}
			return nil, fmt.Errorf("failed to start server %s: %w", serverID, err)
		}
		processes = append(processes, proc)
	}

	return processes, nil
}
