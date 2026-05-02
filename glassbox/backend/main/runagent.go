package main

import (
	"bufio"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	agent "github.com/AnthonyL103/GOMCP/Agent"
	"github.com/AnthonyL103/GOMCP/chat"
	"github.com/AnthonyL103/GOMCP/protocol/parseagentprotocol"
	"github.com/AnthonyL103/GOMCP/transport"
	voicechat "github.com/AnthonyL103/GOMCP/voice"
)

// findModel determines which provider to use based on model name
func findModel(model string) string {
	openAIModels := []string{
		"gpt-4o", "gpt-4o-mini", "gpt-4-turbo",
		"o1-preview", "o1-mini",
	}

	anthropicModels := []string{
		"claude-opus-4-5-20251101",
		"claude-sonnet-4-5-20250929",
		"claude-haiku-4-5-20251001",
	}

	// Check if it's an OpenAI model
	for _, m := range openAIModels {
		if m == model {
			return "OpenAI"
		}
	}

	// Check if it's an Anthropic model
	for _, m := range anthropicModels {
		if m == model {
			return "Anthropic"
		}
	}

	// Unknown model
	return ""
}

// createProvider creates the appropriate provider based on the model
func createProvider(ag *agent.Agent) (transport.Provider, error) {
	llmConfig := ag.LLMConfig

	providerType := findModel(llmConfig.Model)

	switch providerType {
	case "Anthropic":
		return transport.NewAnthropicProvider(llmConfig), nil
	case "OpenAI":
		return transport.NewOpenAIProvider(llmConfig), nil
	default:
		return nil, fmt.Errorf("unsupported model: %s", llmConfig.Model)
	}
}

func runagent() {
	// Parse agent config
	ag, err := parseagentprotocol.ParseAgentConfig()
	if err != nil {
		log.Fatal("Failed to parse agent config:", err)
	}

	// Start all servers and track their processes
	log.Println("Starting MCP servers...")
	processes, err := StartAllServers(ag)
	if err != nil {
		log.Fatal("Failed to start servers:", err)
	}

	// Setup graceful shutdown
	setupGracefulShutdown(processes)

	// Give servers time to start up
	time.Sleep(2 * time.Second)
	log.Println("All servers started!")

	// Create chat session
	chat := chat.NewChat("session-1", 50)

	// Create provider based on model
	provider, err := createProvider(ag)
	if err != nil {
		log.Fatal("Failed to create provider:", err)
	}

	log.Printf("Using provider: %s", provider.GetProviderName())

	if ag.VoiceChat {
		log.Println("Voice chat enabled - initializing voice chat parser")
		vcParser := voicechat.NewVoiceChatParser(chat, ag, provider)
		go vcParser.Start()
	}
	// Interactive loop
	log.Println("Agent ready! Type your messages (press Enter twice to send, Ctrl+C to exit):")

	scanner := bufio.NewScanner(os.Stdin)

	for {
		fmt.Print("\nYou: ")

		// Read multi-line input until empty line
		var lines []string
		for scanner.Scan() {
			line := scanner.Text()

			// Empty line signals end of input
			if line == "" {
				break
			}

			lines = append(lines, line)
		}

		if scanner.Err() != nil {
			break // EOF or error
		}

		userMessage := strings.TrimSpace(strings.Join(lines, "\n"))

		// Skip empty messages
		if userMessage == "" {
			continue
		}

		// Exit commands
		if userMessage == "exit" || userMessage == "quit" {
			log.Println("Shutting down...")
			break
		}

		// Send message to agent
		err := provider.SendRequest(chat, ag, userMessage)
		if err != nil {
			log.Printf("Error: %v", err)
			continue
		}

		// Print last assistant message
		messages := chat.GetMessages()
		if len(messages) > 0 {
			lastMsg := messages[len(messages)-1]
			if lastMsg.Role == "assistant" {
				fmt.Printf("\nAssistant: %s\n", lastMsg.Content)
			}
		}
	}

	log.Println("Goodbye!")
}

// setupGracefulShutdown handles Ctrl+C and kills server processes
func setupGracefulShutdown(processes []*os.Process) {
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	go func() {
		<-sigChan
		log.Println("\nReceived shutdown signal, cleaning up...")

		// Kill all server processes
		for _, proc := range processes {
			if proc != nil {
				log.Printf("Killing process PID: %d", proc.Pid)
				proc.Kill()
			}
		}

		log.Println("Cleanup complete. Exiting.")
		os.Exit(0)
	}()
}
