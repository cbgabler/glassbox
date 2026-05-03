import { useState, useEffect } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Send, Sparkles, Bot, User, Loader2, Wrench, ChevronDown, ChevronRight } from "lucide-react"
import { sendChatPrompt, ParsedResponse, ToolCall, ToolResult } from "@/lib/api"
import { motion, AnimatePresence } from "framer-motion"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism"

function ToolCallBlock({ toolCall }: { toolCall: ToolCall }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-2 rounded-lg border border-emerald-500/20 bg-emerald-500/5 text-xs font-mono overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-emerald-400 hover:bg-emerald-500/10 transition-colors text-left"
      >
        <Wrench className="w-3 h-3 shrink-0" />
        <span className="font-semibold">{toolCall.tool_id}</span>
        <span className="text-emerald-600 ml-auto">{toolCall.server_id}</span>
        {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
      </button>
      {open && (
        <div className="px-3 pb-3 flex flex-col gap-1.5">
          {toolCall.reasoning && (
            <p className="text-muted-foreground italic text-[11px] border-t border-white/5 pt-2">{toolCall.reasoning}</p>
          )}
          <pre className="text-[11px] text-foreground/80 whitespace-pre-wrap break-all">
            {JSON.stringify(toolCall.parameters, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}

function ToolResultBlock({ toolResult }: { toolResult: ToolResult }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={`mt-1 rounded-lg border text-xs font-mono overflow-hidden ${
      toolResult.is_error ? "border-red-500/20 bg-red-500/5" : "border-white/10 bg-white/5"
    }`}>
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-muted-foreground hover:bg-white/5 transition-colors text-left"
      >
        <span className={`w-2 h-2 rounded-full shrink-0 ${toolResult.is_error ? "bg-red-500" : "bg-emerald-500"}`} />
        <span>{toolResult.tool_id} result</span>
        {open ? <ChevronDown className="w-3 h-3 ml-auto" /> : <ChevronRight className="w-3 h-3 ml-auto" />}
      </button>
      {open && (
        <pre className="px-3 pb-3 text-[11px] text-foreground/70 whitespace-pre-wrap break-all border-t border-white/5 pt-2">
          {toolResult.content}
        </pre>
      )}
    </div>
  )
}

function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="prose prose-invert prose-sm max-w-none prose-pre:!p-0 prose-pre:!bg-transparent prose-pre:border-0">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ node, inline, className, children, ...props }: any) {
            const match = /language-(\w+)/.exec(className || "")
            return !inline && match ? (
              <SyntaxHighlighter
                {...props}
                style={vscDarkPlus}
                language={match[1]}
                PreTag="div"
                className="rounded-md border border-white/10 !bg-[#000000] !p-3 !my-3 text-[12px] font-mono shadow-inner"
              >
                {String(children).replace(/\n$/, "")}
              </SyntaxHighlighter>
            ) : (
              <code {...props} className={`${className} bg-white/10 border border-white/5 px-1 py-0.5 rounded text-emerald-300 font-mono text-[0.85em]`}>
                {children}
              </code>
            )
          }
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

interface ChatMessage {
  role:    "user" | "bot";
  content: string;
  parsed?: ParsedResponse;
}

export function ChatPanel({
  isGenerating,
  initialResponse,
  clearTrigger,
  onRepoParsed,
  onCodeParsed,
}: {
  isGenerating:      boolean;
  initialResponse?:  ParsedResponse;
  clearTrigger?:     number;
  onRepoParsed?:     (parsed: ParsedResponse) => void;
  onCodeParsed?:     (parsed: ParsedResponse) => void;
}) {
  const [input, setInput]       = useState("")
  const [isTyping, setIsTyping] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])

  // Clear messages when clearTrigger changes
  useEffect(() => {
    setMessages([])
    setInput("")
  }, [clearTrigger])

  // When the dashboard's initial fetch resolves, seed the first bot message
  useEffect(() => {
    console.log("[ChatPanel] initialResponse changed:", initialResponse)
    if (initialResponse) {
      console.log("[ChatPanel] chat blocks:", initialResponse.chat)
      setMessages([{
        role:    "bot",
        content: initialResponse.chat.join("\n\n"),
        parsed:  initialResponse,
      }])
    }
  }, [initialResponse])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!input.trim() || isTyping) return

    const userMessage = input
    setInput("")
    setMessages(prev => [...prev, { role: "user", content: userMessage }])
    setIsTyping(true)

    try {
      const parsed = await sendChatPrompt(userMessage)

      if (parsed.repo.length > 0) onRepoParsed?.(parsed)
      if (parsed.code.length > 0) onCodeParsed?.(parsed)

      setMessages(prev => [...prev, {
        role:    "bot",
        content: parsed.chat.join("\n\n"),
        parsed,
      }])
    } catch (err) {
      console.error(err)
      setMessages(prev => [...prev, {
        role:    "bot",
        content: "**Error:** Failed to communicate with GlassBox server.",
      }])
    } finally {
      setIsTyping(false)
    }
  }



  return (
    <div className="flex flex-col h-full">
      <div className="p-4 border-b border-border bg-card/50 flex items-center gap-2 sticky top-0 backdrop-blur-md z-10">
        <Sparkles className="w-5 h-5 text-emerald-400" />
        <h2 className="font-medium text-foreground tracking-tight">Chat</h2>
      </div>

      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-6">
        <AnimatePresence initial={false}>
          {messages.map((msg, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 10, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ duration: 0.3 }}
              className={`flex gap-3 ${msg.role === "user" ? "flex-row-reverse" : ""}`}
            >
              <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 border ${
                msg.role === "user"
                  ? "bg-blue-500/10 border-blue-500/20 text-blue-400"
                  : "bg-emerald-500/10 border-emerald-500/20 text-emerald-400"
              }`}>
                {msg.role === "user" ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
              </div>

              <div className={`flex flex-col gap-1 w-full max-w-[85%] ${msg.role === "user" ? "items-end" : ""}`}>
                <span className="text-xs font-medium text-muted-foreground">
                  {msg.role === "user" ? "You" : "GlassBox"}
                </span>
                <div className={`text-sm leading-relaxed p-3 rounded-2xl shadow-sm border ${
                  msg.role === "user"
                    ? "bg-blue-600 border-blue-500 text-white rounded-tr-sm"
                    : "bg-white/5 border-white/10 text-foreground rounded-tl-sm backdrop-blur-sm"
                }`}>
                  {msg.role === "user" ? (
                    msg.content
                  ) : (
                    <>
                      {msg.parsed?.toolCall   && <ToolCallBlock   toolCall={msg.parsed.toolCall} />}
                      {msg.parsed?.toolResult && <ToolResultBlock toolResult={msg.parsed.toolResult} />}
                      {msg.content && <MarkdownContent content={msg.content} />}
                    </>
                  )}
                </div>
              </div>
            </motion.div>
          ))}

          {isTyping && (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.9 }}
              className="flex gap-3"
            >
              <div className="w-8 h-8 rounded-full bg-emerald-500/10 flex items-center justify-center shrink-0 border border-emerald-500/20">
                <Loader2 className="w-4 h-4 text-emerald-400 animate-spin" />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div className="p-4 bg-background shrink-0 border-t border-border mt-auto">
        {isGenerating || isTyping ? (
          <div className="flex items-center justify-center gap-2 rounded-full border border-emerald-500/20 bg-emerald-500/5 px-4 py-3 text-sm text-emerald-400">
            <Loader2 className="w-4 h-4 animate-spin" />
            {isGenerating ? "Analyzing repo..." : "Just thinking..."}
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="relative flex items-center">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask GlassBox about the findings..."
              className="w-full bg-muted border border-border rounded-full py-2.5 pl-4 pr-10 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500/50 focus:border-emerald-500/50 transition-all text-foreground placeholder:text-muted-foreground"
            />
            <motion.button
              type="submit"
              disabled={!input.trim()}
              className="absolute right-1 w-8 h-8 flex items-center justify-center rounded-full hover:bg-background transition-colors text-muted-foreground hover:text-emerald-400 disabled:opacity-50"
              whileHover={input.trim() ? { scale: 1.15 } : {}}
              whileTap={input.trim() ? { scale: 0.9 } : {}}
            >
              <AnimatePresence mode="wait">
                <motion.div
                  key="send"
                  initial={{ opacity: 0, scale: 0.5, rotate: -180 }}
                  animate={{ opacity: 1, scale: 1, rotate: 0 }}
                  exit={{ opacity: 0, scale: 0.5, rotate: 180 }}
                  transition={{ duration: 0.3 }}
                >
                  <Send className="w-4 h-4" />
                </motion.div>
              </AnimatePresence>
            </motion.button>
          </form>
        )}
      </div>
    </div>
  )
}