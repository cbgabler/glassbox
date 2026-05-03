package main

import (
	"glassbox/backend/cleanup"
	"log"
	"net/http"
	"time"

	"github.com/AnthonyL103/GOMCP/chat"
	"github.com/AnthonyL103/GOMCP/protocol/parseagentprotocol"
	"github.com/AnthonyL103/GOMCP/transport"
	voicechat "github.com/AnthonyL103/GOMCP/voice"
	"github.com/joho/godotenv"
)

func main() {
	defer func() {
		if err := cleanup.RemoveAllRuns(); err != nil {
			log.Printf("cleanup: %v", err)
		}
	}()

	if err := godotenv.Load(".env"); err != nil {
		log.Println("No .env file found, using system env")
	} else {
		log.Println("✓ Loaded .env successfully")
	}

	ag, err := parseagentprotocol.ParseAgentConfig()
	if err != nil {
		log.Fatalf("parse agent config: %v", err)
	}

	log.Println("Starting MCP servers...")
	processes, err := StartAllServers(ag)
	if err != nil {
		log.Fatalf("start servers: %v", err)
	}
	setupGracefulShutdown(processes)
	time.Sleep(2 * time.Second)
	log.Println("All servers started!")

	provider, err := createProvider(ag)
	if err != nil {
		log.Fatalf("create provider: %v", err)
	}
	log.Printf("Using provider: %s", provider.GetProviderName())

	hub := newHub()

	// Wire callback after hub exists
	if ap, ok := provider.(*transport.AnthropicProvider); ok {
		log.Println("Setting AnthropicProvider OnToolCall callback to broadcast tool calls to WS clients")
		ap.OnToolCall = func(msg chat.Message) {
			hub.Broadcast(msg)
		}
	}

	srv := &Server{
		ag:       ag,
		provider: provider,
		chat:     chat.NewChat("session-1", 50),
		hub:      hub,
	}

	if ag.VoiceChat {
		vcParser := voicechat.NewVoiceChatParser(srv.chat, ag, provider)
		go vcParser.Start()
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/chat", srv.handleChat)
	mux.HandleFunc("/ws", srv.handleWS)
	mux.HandleFunc("/done", srv.handleDone)

	log.Println("Listening on :8080")
	log.Fatal(http.ListenAndServe(":8080", corsMiddleware(mux)))
}
