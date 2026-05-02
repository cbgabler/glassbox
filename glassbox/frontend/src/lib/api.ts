export interface AuditResponse {
  run_id: string;
}

export async function startAudit(pathOrUrl: string): Promise<AuditResponse> {
  try {
    const response = await fetch("http://localhost:8000/audit", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ path: pathOrUrl }),
    });

    if (!response.ok) {
      throw new Error(`API returned status \${response.status}`);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to start audit:", error);
    // If backend is not running, gracefully fallback to a mock run_id for testing
    console.warn("Backend unavailable, using mock run_id 'mock-run-123'");
    return new Promise((resolve) => {
      setTimeout(() => {
        resolve({ run_id: "mock-run-123" });
      }, 1500); // Simulate network latency
    });
  }
}
