import { useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Send, Sparkles, Bot, User, Loader2 } from "lucide-react"
import { sendChatPrompt } from "@/lib/api"
import { motion, AnimatePresence } from "framer-motion"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism"

export function ChatPanel() {
  const [input, setInput] = useState("")
  const [isTyping, setIsTyping] = useState(false)
  const [messages, setMessages] = useState<{role: "user"|"bot", content: string}[]>([
    { role: "bot", content: "I am connected to the MCP server. Ask me anything about the codebase." }
  ])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!input.trim() || isTyping) return

    const userMessage = input
    setInput("")
    setMessages(prev => [...prev, { role: "user", content: userMessage }])
    setIsTyping(true)

    try {
      const response = await sendChatPrompt(userMessage)
      setMessages(prev => [...prev, { role: "bot", content: response }])
    } catch (err) {
      console.error(err)
      setMessages(prev => [...prev, { role: "bot", content: "**Error:** Failed to communicate with MCP server." }])
    } finally {
      setIsTyping(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b border-border bg-card/50 flex items-center gap-2 sticky top-0 backdrop-blur-md z-10">
        <Sparkles className="w-5 h-5 text-emerald-400" />
        <h2 className="font-medium text-foreground tracking-tight">MCP Agent</h2>
      </div>

      {/* Messages */}
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
                  {msg.role === "user" ? "You" : "MCP"}
                </span>
                <div className={`text-sm leading-relaxed p-3 rounded-2xl shadow-sm border ${
                  msg.role === "user" 
                    ? "bg-blue-600 border-blue-500 text-white rounded-tr-sm" 
                    : "bg-white/5 border-white/10 text-foreground rounded-tl-sm backdrop-blur-sm"
                }`}>
                  {msg.role === "user" ? (
                    msg.content
                  ) : (
                    <div className="prose prose-invert prose-sm max-w-none prose-pre:!p-0 prose-pre:!bg-transparent prose-pre:border-0">
                      <ReactMarkdown 
                        remarkPlugins={[remarkGfm]}
                        components={{
                          code({node, inline, className, children, ...props}: any) {
                            const match = /language-(\w+)/.exec(className || '')
                            return !inline && match ? (
                              <SyntaxHighlighter
                                {...props}
                                children={String(children).replace(/\n$/, '')}
                                style={vscDarkPlus}
                                language={match[1]}
                                PreTag="div"
                                className="rounded-md border border-white/10 !bg-[#000000] !p-3 !my-3 text-[12px] font-mono shadow-inner"
                              />
                            ) : (
                              <code {...props} className={`${className} bg-white/10 border border-white/5 px-1 py-0.5 rounded text-emerald-300 font-mono text-[0.85em]`}>
                                {children}
                              </code>
                            )
                          }
                        }}
                      >
                        {msg.content}
                      </ReactMarkdown>
                    </div>
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

      {/* Input */}
      <div className="p-4 bg-background shrink-0 border-t border-border mt-auto">
        <form onSubmit={handleSubmit} className="relative flex items-center">
          <input 
            type="text" 
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={isTyping}
            placeholder="Ask the MCP about the findings..." 
            className="w-full bg-muted border border-border rounded-full py-2.5 pl-4 pr-10 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500/50 focus:border-emerald-500/50 transition-all text-foreground placeholder:text-muted-foreground disabled:opacity-50"
          />
          <button 
            type="submit"
            disabled={isTyping || !input.trim()}
            className="absolute right-1 w-8 h-8 flex items-center justify-center rounded-full hover:bg-background transition-colors text-muted-foreground hover:text-emerald-400 disabled:opacity-50"
          >
            <Send className="w-4 h-4" />
          </button>
        </form>
      </div>
    </div>
  )
}
