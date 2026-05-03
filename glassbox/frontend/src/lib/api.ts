const API_URL = "http://localhost:8080";
const WS_URL  = "ws://localhost:8080/ws";

// -------------------------------------------------------------------
// Go backend types (mirrors chat package)
// -------------------------------------------------------------------

export interface ToolCall {
  server_id:   string;
  tool_id:     string;
  handler:     string;
  parameters:  Record<string, unknown>;
  reasoning:   string;
  tool_use_id: string;
}

export interface ToolResult {
  server_id:   string;
  tool_id:     string;
  content:     string;
  is_error:    boolean;
  tool_use_id: string;
}

export interface Message {
  role:         "user" | "assistant";
  content:      string;
  timestamp:    string;
  tool_call?:   ToolCall;
  tool_result?: ToolResult;
}

// -------------------------------------------------------------------
// Repo context types (mirrors repo context server)
// -------------------------------------------------------------------

export interface RepoEntry {
  path: string;
  kind: "file" | "dir";
  size?: number;
}

export interface RepoTreeResponse {
  repo_root:    string;
  base_path:    string;
  entries:      RepoEntry[];
  total_count:  number;
  truncated:    boolean;
  generated_at: string;
}

// FileNode is the frontend tree shape derived from RepoTreeResponse
export interface FileNode {
  name:            string;
  type:            "file" | "folder";
  children?:       FileNode[];
  status?:         "normal" | "vulnerable" | "warning";
  vulnDescription?: string;
}

// -------------------------------------------------------------------
// Parsed panels — each tag type has its own typed array
// -------------------------------------------------------------------

export interface ParsedResponse {
  // <<repo>> blocks: parse content as RepoTreeResponse JSON
  repo:  RepoTreeResponse[];
  // <<code>> blocks: render as markdown
  code:  string[];
  // <<chat>> blocks + unwrapped text: render as markdown
  chat:  string[];
  // Tool call/result surfaced directly from the Message (no tags needed)
  toolCall?:   ToolCall;
  toolResult?: ToolResult;
}

// -------------------------------------------------------------------
// Parser
// -------------------------------------------------------------------

function extractBlocks(content: string, tag: string): { blocks: string[]; remainder: string } {
  const pattern = new RegExp(`<<${tag}>>[\\s\\S]*?<<\\/?${tag}>>`, "g");
  const capture = new RegExp(`<<${tag}>>((?:[\\s\\S]*?))<<\\/?${tag}>>`, "g");
  const blocks: string[] = [];

  let match;
  while ((match = capture.exec(content)) !== null) {
    let block = match[1].trim();
    block = block.replace(/^```[\w]*\n?/, "").replace(/\n?```$/, "").trim();
    blocks.push(block);
  }

  return { blocks, remainder: content.replace(pattern, "").trim() };
}

export function parseAgentResponse(message: Message): ParsedResponse {
  let remaining = message.content;
  
  console.log("[parser] raw message content:", message.content)

  const { blocks: rawRepo, remainder: afterRepo } = extractBlocks(remaining, "repo");
  remaining = afterRepo;
  console.log("[parser] <<repo>> blocks found:", rawRepo.length, rawRepo)

  const repo: RepoTreeResponse[] = rawRepo.flatMap(block => {
    try { 
      const parsed = JSON.parse(block) as RepoTreeResponse
      console.log("[parser] <<repo>> parsed JSON ok, entries:", parsed.entries?.length)
      return [parsed]
    }
    catch (e) { 
      console.warn("[parser] <<repo>> JSON parse failed:", e, "\nraw block:", block)
      return []
    }
  });

  const { blocks: code, remainder: afterCode } = extractBlocks(remaining, "code");
  remaining = afterCode;
  console.log("[parser] <<code>> blocks found:", code.length)

  const { blocks: chat, remainder: afterChat } = extractBlocks(remaining, "chat");
  remaining = afterChat;
  console.log("[parser] <<chat>> blocks found:", chat.length)

  if (remaining.length > 0) {
    console.log("[parser] unwrapped remainder (added to chat):", remaining)
    chat.push(remaining)
  }

  const result = { repo, code, chat, toolCall: message.tool_call, toolResult: message.tool_result }
  console.log("[parser] final result — repo:", repo.length, "code:", code.length, "chat:", chat.length, "toolCall:", !!message.tool_call)
  return result
}

// -------------------------------------------------------------------
// Build FileNode tree from flat RepoTreeResponse entries
// -------------------------------------------------------------------

export function buildFileTree(repoTree: RepoTreeResponse): FileNode[] {
  const root: FileNode[] = [];
  const map = new Map<string, FileNode>();

  const sorted = [...repoTree.entries].sort((a, b) => a.path.localeCompare(b.path));

  for (const entry of sorted) {
    // Strip markdown link format [text](url) -> text
    const cleanPath = entry.path.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    const parts = cleanPath.split("/");
    const name = parts[parts.length - 1];
    const node: FileNode = {
      name,
      type: entry.kind === "dir" ? "folder" : "file",
      children: entry.kind === "dir" ? [] : undefined,
    };
    map.set(cleanPath, node);
    if (parts.length === 1) {
      root.push(node);
    } else {
      const parentPath = parts.slice(0, -1).join("/");
      const parent = map.get(parentPath);
      if (parent?.children) parent.children.push(node);
    }
  }

  return root;
}

// -------------------------------------------------------------------
// REST
// -------------------------------------------------------------------

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  console.log(res)
  return res.json();

}

export async function sendChatPrompt(prompt: string): Promise<ParsedResponse> {
  const message = await post<Message>("/chat", { message: prompt });
  return parseAgentResponse(message);
}

export const analyzeRepo = (repoUrl: string, prompt: string) =>
  sendChatPrompt(`${prompt}\n\nRepository: ${repoUrl}`)

// -------------------------------------------------------------------
// WebSocket — tool updates broadcast from Go hub
// -------------------------------------------------------------------

export function connectToolStream(onMessage: (msg: Message) => void): () => void {
  const ws = new WebSocket(WS_URL);

  ws.onmessage = (event) => {
    try {
      const msg: Message = JSON.parse(event.data);
      onMessage(msg);
    } catch {
      console.error("ws parse error", event.data);
    }
  };

  ws.onerror = (e) => console.error("ws error", e);
  return () => ws.close();
}