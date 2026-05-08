package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
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
	writeMu sync.Mutex
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

// count returns the current connected client count. Takes the read lock so
// callers don't race against add/remove.
func (h *Hub) count() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.clients)
}

// Broadcast sends any value as JSON to all connected WS clients.
func (h *Hub) Broadcast(v any) {
	data, err := json.Marshal(v)
	if err != nil {
		log.Printf("[Hub] Broadcast marshal error: %v", err)
		return
	}
	h.mu.RLock()
	clients := make([]*websocket.Conn, 0, len(h.clients))
	for c := range h.clients {
		clients = append(clients, c)
	}
	clientCount := len(clients)
	h.mu.RUnlock()
	log.Printf("[Hub] Broadcasting to %d clients: %s", clientCount, string(data[:min(len(data), 200)]))

	// Serialize writes because gorilla/websocket connections are not safe for concurrent writers.
	h.writeMu.Lock()
	defer h.writeMu.Unlock()
	for _, c := range clients {
		if err := c.WriteMessage(websocket.TextMessage, data); err != nil {
			log.Printf("[Hub] Write error: %v", err)
		}
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
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

	// chatMu guards chat. handleChat snapshots the pointer for the duration
	// of one request; handleDone replaces it on cleanup. Without this, the
	// pointer write in handleDone races concurrent reads in handleChat
	// (the race detector flags it).
	chatMu sync.RWMutex
	chat   *chat.Chat

	hub *Hub
}

func (s *Server) getChat() *chat.Chat {
	s.chatMu.RLock()
	defer s.chatMu.RUnlock()
	return s.chat
}

func (s *Server) setChat(c *chat.Chat) {
	s.chatMu.Lock()
	s.chat = c
	s.chatMu.Unlock()
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

	chatInstance := s.getChat()
	if err := s.provider.SendRequest(chatInstance, s.ag, body.Message); err != nil {
		log.Printf("Error: %v", err)
		s.hub.Broadcast(map[string]string{"type": "error", "message": err.Error()})
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	messages := chatInstance.GetMessages()
	log.Printf("Chat request complete, total messages: %d", len(messages))

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
		log.Printf("[WS] upgrade error: %v", err)
		return
	}
	s.hub.add(conn)
	log.Printf("[WS] client connected: %s, total clients: %d", conn.RemoteAddr(), s.hub.count())

	go func() {
		defer func() {
			s.hub.remove(conn)
			log.Printf("[WS] client disconnected: %s", conn.RemoteAddr())
		}()
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				if err.Error() != "websocket: close sent" {
					log.Printf("[WS] read error: %v", err)
				}
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

	s.setChat(chat.NewChat("session-1", 50))
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
		w.Header().Set("Access-Control-Allow-Origin", "*")

		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}

		next.ServeHTTP(w, r)
	})
}
