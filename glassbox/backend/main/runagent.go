package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"

	"glassbox/backend/cleanup"

	agent "github.com/AnthonyL103/GOMCP/Agent"
	"github.com/AnthonyL103/GOMCP/chat"
	"github.com/AnthonyL103/GOMCP/transport"
	"github.com/gorilla/websocket"
)

// -------------------------------------------------------------------
// WebSocket hub
// -------------------------------------------------------------------

type Hub struct {
	mu      sync.RWMutex
	clients map[*websocket.Conn]struct{}
}

func newHub() *Hub { return &Hub{clients: make(map[*websocket.Conn]struct{})} }

func (h *Hub) add(c *websocket.Conn) {
	h.mu.Lock()
	h.clients[c] = struct{}{}
	h.mu.Unlock()
}

func (h *Hub) remove(c *websocket.Conn) {
	h.mu.Lock()
	delete(h.clients, c)
	h.mu.Unlock()
	c.Close()
}

// Broadcast sends any value as JSON to all connected WS clients.
func (h *Hub) Broadcast(v any) {
	data, err := json.Marshal(v)
	if err != nil {
		return
	}
	h.mu.RLock()
	defer h.mu.RUnlock()
	for c := range h.clients {
		_ = c.WriteMessage(websocket.TextMessage, data)
	}
}

// -------------------------------------------------------------------
// Server
// -------------------------------------------------------------------

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

type Server struct {
	ag       *agent.Agent
	provider transport.Provider
	chat     *chat.Chat
	hub      *Hub
}

// POST /chat — same logic as the CLI loop, just over HTTP
func (s *Server) handleChat(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var body struct {
		Message string `json:"message"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Message == "" {
		http.Error(w, "invalid body", http.StatusBadRequest)
		return
	}

	// Snapshot count before so we know which messages are new after the request
	before := len(s.chat.GetMessages())

	if err := s.provider.SendRequest(s.chat, s.ag, body.Message); err != nil {
		log.Printf("Error: %v", err)
		s.hub.Broadcast(map[string]string{"type": "error", "message": err.Error()})
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	messages := s.chat.GetMessages()

	log.Printf("Chat request complete, total messages: %d", len(messages))

	// Broadcast every tool cycle added during this request over WebSocket.
	// Tool cycles are assistant messages with both tool_call and tool_result set.
	// The final plain-text response goes back via REST only.
	for _, msg := range messages[before:] {
		log.Printf("New message: %s", msg.Content)
		// Broadcast standard tool cycles
		if msg.ToolCall != nil && msg.ToolResult != nil {
			s.hub.Broadcast(msg)
			continue
		}
		// Also broadcast messages that contain tagged blocks (assistant outputs often include tags)
		content := msg.Content
		// look for common tag markers (supporting both <<tag>>...<</tag>> and repeated-closing <<tag>>)
		if strings.Contains(content, "<<repo>>") || strings.Contains(content, "<<code>>") || strings.Contains(content, "<<chat>>") || strings.Contains(content, "<<secrets>>") || strings.Contains(content, "<<vulnerabilities>>") || strings.Contains(content, "<<findings>>") {
			log.Printf("Broadcasting tagged assistant message")
			s.hub.Broadcast(msg)
		}
	}

	if len(messages) == 0 {
		http.Error(w, "no response", http.StatusInternalServerError)
		return
	}

	lastMsg := messages[len(messages)-1]

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(lastMsg)
}

// GET /ws — clients connect here to receive tool update broadcasts
func (s *Server) handleWS(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("ws upgrade: %v", err)
		return
	}
	s.hub.add(conn)
	log.Printf("ws connected: %s", conn.RemoteAddr())

	go func() {
		defer s.hub.remove(conn)
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				break
			}
		}
	}()
}

// POST /done — trigger cleanup of cloned repos and clear chat context
func (s *Server) handleDone(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	log.Println("Cleanup requested via /done endpoint")
	if err := cleanup.RemoveAllRuns(); err != nil {
		log.Printf("cleanup failed: %v", err)
		http.Error(w, "cleanup failed", http.StatusInternalServerError)
		return
	}

	// Clear chat context for next audit
	s.chat = chat.NewChat("session-1", 50)
	log.Println("Chat context cleared")

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{"status": "cleaned"})
}

// -------------------------------------------------------------------
// Unchanged helpers
// -------------------------------------------------------------------

func findModel(model string) string {
	for _, m := range []string{"gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-preview", "o1-mini"} {
		if m == model {
			return "OpenAI"
		}
	}
	for _, m := range []string{"claude-opus-4-5-20251101", "claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"} {
		if m == model {
			return "Anthropic"
		}
	}
	return ""
}

func createProvider(ag *agent.Agent) (transport.Provider, error) {
	switch findModel(ag.LLMConfig.Model) {
	case "Anthropic":
		return transport.NewAnthropicProvider(ag.LLMConfig), nil
	case "OpenAI":
		return transport.NewOpenAIProvider(ag.LLMConfig), nil
	default:
		return nil, fmt.Errorf("unsupported model: %s", ag.LLMConfig.Model)
	}
}

func setupGracefulShutdown(processes []*os.Process) {
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-sigChan
		log.Println("Shutting down...")
		for _, proc := range processes {
			if proc != nil {
				proc.Kill()
			}
		}
		if err := cleanup.RemoveAllRuns(); err != nil {
			log.Printf("cleanup: %v", err)
		}
		os.Exit(0)
	}()
}

// -------------------------------------------------------------------
// Main
// -------------------------------------------------------------------

func corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "http://localhost:5173")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}

		next.ServeHTTP(w, r)
	})
}
