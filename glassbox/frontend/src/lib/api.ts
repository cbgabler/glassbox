export interface AuditResponse {
  run_id: string;
}

// Keep the startAudit for the InitiatePage
export async function startAudit(pathOrUrl: string): Promise<AuditResponse> {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve({ run_id: "mcp-run-" + Date.now() });
    }, 1500);
  });
}

// MCP Mock Endpoints returning Markdown

export async function fetchRepoStructure(runId: string): Promise<string> {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve(`
### Repository Structure

- 📁 **src**
  - 📄 \`main.py\`
  - 📄 \`auth.py\` 🔴 *(Critical: Timing Leak)*
  - 📄 \`utils.py\`
- 📄 \`package.json\` 🟠 *(High: Vulnerable Dependency)*
- 📄 \`.env\` 🟠 *(High: Plaintext Secret)*
      `.trim());
    }, 1000);
  });
}

export async function fetchSnippets(runId: string): Promise<string> {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve(`
# Timing Leak in Password Comparison
**Severity**: \`CRITICAL\` | **Scanner**: \`SideChannelScanner\` | **File**: \`src/auth.py:42\`

The function uses a naive byte-by-byte comparison for a secret token. This creates an early-exit timing leak where the time taken correlates with the number of correctly guessed prefix bytes.

### Vulnerable Snippet
\`\`\`python
def check_password(user_input: str, secret: str) -> bool:
    # WARNING: VULNERABLE
    if user_input == secret:
        return True
    return False
\`\`\`
      `.trim());
    }, 1200);
  });
}

export async function sendChatPrompt(prompt: string): Promise<string> {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve(`
You asked: *"${prompt}"*

Based on my analysis of the codebase, I recommend using a constant-time comparison function instead of the standard \`==\` operator to mitigate the timing leak in \`src/auth.py\`.

### Recommended Fix
\`\`\`python
import hmac

def check_password(user_input: str, secret: str) -> bool:
    # SECURE: Constant-time comparison
    return hmac.compare_digest(user_input, secret)
\`\`\`
      `.trim());
    }, 2000);
  });
}
