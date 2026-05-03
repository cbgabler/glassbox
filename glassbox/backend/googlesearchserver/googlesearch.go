package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"strconv"
)

type SearchResult struct {
	Title   string `json:"title"`
	Link    string `json:"link"`
	Snippet string `json:"snippet"`
}

type ImageResult struct {
	Title       string `json:"title"`
	Link        string `json:"link"`
	DisplayLink string `json:"displayLink"`
	Image       struct {
		ThumbnailUrl string `json:"thumbnailUrl"`
		Width        int    `json:"width"`
		Height       int    `json:"height"`
	} `json:"image"`
}

type GoogleSearchResponse struct {
	Items []struct {
		Title   string `json:"title"`
		Link    string `json:"link"`
		Snippet string `json:"snippet"`
	} `json:"items"`
}

type GoogleImageResponse struct {
	Items []ImageResult `json:"items"`
}

func main() {
	http.HandleFunc("/execute/search", handleSearch)
	http.HandleFunc("/execute/search_images", handleSearchImages)

	port := ":8082"
	log.Printf("Starting Google Search server on port %s\n", port)
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

	results, err := performGoogleSearch(query, maxResults)
	if err != nil {
		http.Error(w, fmt.Sprintf("Search error: %v", err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"query":   query,
		"results": results,
		"count":   len(results),
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

	results, err := performImageSearch(query, maxResults)
	if err != nil {
		http.Error(w, fmt.Sprintf("Image search error: %v", err), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"query":   query,
		"results": results,
		"count":   len(results),
	})
}

func performGoogleSearch(query string, maxResults int) ([]SearchResult, error) {
	apiKey := os.Getenv("GOOGLE_API_KEY")
	searchEngineID := os.Getenv("GOOGLE_SEARCH_ENGINE_ID")

	if apiKey == "" || searchEngineID == "" {
		log.Println("Warning: GOOGLE_API_KEY or GOOGLE_SEARCH_ENGINE_ID not set")
		return getMockSearchResults(query, maxResults), nil
	}

	params := url.Values{}
	params.Add("q", query)
	params.Add("key", apiKey)
	params.Add("cx", searchEngineID)
	params.Add("num", strconv.Itoa(maxResults))

	searchURL := fmt.Sprintf("https://www.googleapis.com/customsearch/v1?%s", params.Encode())

	resp, err := http.Get(searchURL)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("Google API error: %d - %s", resp.StatusCode, string(body))
	}

	var result GoogleSearchResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}

	var results []SearchResult
	for _, item := range result.Items {
		results = append(results, SearchResult{
			Title:   item.Title,
			Link:    item.Link,
			Snippet: item.Snippet,
		})
	}

	return results, nil
}

func performImageSearch(query string, maxResults int) ([]ImageResult, error) {
	apiKey := os.Getenv("GOOGLE_API_KEY")
	log.Println("GOOGLE_API_KEY:", apiKey) // Debug
	searchEngineID := os.Getenv("GOOGLE_SEARCH_ENGINE_ID")
	log.Println("GOOGLE_SEARCH_ENGINE_ID:", searchEngineID) // Debug

	if apiKey == "" || searchEngineID == "" {
		log.Println("Warning: GOOGLE_API_KEY or GOOGLE_SEARCH_ENGINE_ID not set")
		return []ImageResult{}, nil
	}

	params := url.Values{}
	params.Add("q", query)
	params.Add("key", apiKey)
	params.Add("cx", searchEngineID)
	params.Add("searchType", "image")
	params.Add("num", strconv.Itoa(maxResults))

	searchURL := fmt.Sprintf("https://www.googleapis.com/customsearch/v1?%s", params.Encode())

	resp, err := http.Get(searchURL)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("Google API error: %d - %s", resp.StatusCode, string(body))
	}

	var result GoogleImageResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}

	return result.Items, nil
}

// Mock results for development/testing when API keys aren't set
func getMockSearchResults(query string, maxResults int) []SearchResult {
	mockResults := []SearchResult{
		{
			Title:   fmt.Sprintf("Results for '%s' - Example 1", query),
			Link:    "https://example.com/1",
			Snippet: "This is a mock search result. Configure GOOGLE_API_KEY and GOOGLE_SEARCH_ENGINE_ID for real results.",
		},
		{
			Title:   fmt.Sprintf("Results for '%s' - Example 2", query),
			Link:    "https://example.com/2",
			Snippet: "Another mock result to demonstrate the search tool functionality.",
		},
	}

	if maxResults < len(mockResults) {
		return mockResults[:maxResults]
	}
	return mockResults
}
