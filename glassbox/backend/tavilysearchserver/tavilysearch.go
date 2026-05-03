package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"

	"github.com/joho/godotenv"
)

type TavilySearchRequest struct {
	APIKey      string `json:"api_key"`
	Query       string `json:"query"`
	SearchDepth string `json:"search_depth,omitempty"`
	IncludeImages bool   `json:"include_images,omitempty"`
	MaxResults  int    `json:"max_results,omitempty"`
}

type TavilySearchResult struct {
	Title   string `json:"title"`
	URL     string `json:"url"`
	Content string `json:"content"`
	Score   float64 `json:"score"`
}

type TavilyImageResult struct {
	URL         string `json:"url"`
	Description string `json:"description"`
}

type TavilyResponse struct {
	Results []TavilySearchResult `json:"results"`
	Images  []TavilyImageResult  `json:"images,omitempty"`
}

func main() {
	// Try to load .env if it exists
	_ = godotenv.Load(".env")

	http.HandleFunc("/execute/search", handleSearch)
	http.HandleFunc("/execute/search_images", handleSearchImages)

	port := ":8085"
	log.Printf("Starting Tavily Search server on port %s\n", port)
	log.Fatal(http.ListenAndServe(port, nil))
}

func handleSearch(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var params map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&params); err != nil {
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}

	query, ok := params["query"].(string)
	if !ok || query == "" {
		http.Error(w, "Missing or invalid query parameter", http.StatusBadRequest)
		return
	}

	maxResults := 5
	if mr, ok := params["max_results"].(float64); ok {
		maxResults = int(mr)
	}

	results, err := performTavilySearch(query, maxResults, false)
	if err != nil {
		http.Error(w, fmt.Sprintf("Search error: %v", err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"query":   query,
		"results": results.Results,
		"count":   len(results.Results),
	})
}

func handleSearchImages(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var params map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&params); err != nil {
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}

	query, ok := params["query"].(string)
	if !ok || query == "" {
		http.Error(w, "Missing or invalid query parameter", http.StatusBadRequest)
		return
	}

	maxResults := 5
	if mr, ok := params["max_results"].(float64); ok {
		maxResults = int(mr)
	}

	results, err := performTavilySearch(query, maxResults, true)
	if err != nil {
		http.Error(w, fmt.Sprintf("Image search error: %v", err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"query":   query,
		"results": results.Images,
		"count":   len(results.Images),
	})
}

func performTavilySearch(query string, maxResults int, includeImages bool) (*TavilyResponse, error) {
	apiKey := os.Getenv("TAVILY_API_KEY")
	if apiKey == "" {
		return nil, fmt.Errorf("TAVILY_API_KEY environment variable not set")
	}

	reqBody := TavilySearchRequest{
		APIKey:      apiKey,
		Query:       query,
		SearchDepth: "basic",
		MaxResults:  maxResults,
		IncludeImages: includeImages,
	}

	jsonData, err := json.Marshal(reqBody)
	if err != nil {
		return nil, err
	}

	resp, err := http.Post("https://api.tavily.com/search", "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("Tavily API error: %d - %s", resp.StatusCode, string(body))
	}

	var result TavilyResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}

	return &result, nil
}
