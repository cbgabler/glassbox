import { Send, Sparkles, Bot, User } from "lucide-react"

export function ChatPanel() {
  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b border-border bg-card/50 flex items-center gap-2 sticky top-0 backdrop-blur-md z-10">
        <Sparkles className="w-5 h-5 text-emerald-400" />
        <h2 className="font-medium text-foreground tracking-tight">Nemotron Agent</h2>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-6">
        
        <div className="flex gap-3">
          <div className="w-8 h-8 rounded-full bg-emerald-500/10 flex items-center justify-center shrink-0 border border-emerald-500/20">
            <Bot className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted-foreground">Nemotron</span>
            <div className="text-sm text-foreground leading-relaxed bg-muted/30 p-3 rounded-2xl rounded-tl-sm">
              I've finished scanning the repository. There is a critical timing leak in the authentication module, and 3 exposed secrets in your history. What would you like to fix first?
            </div>
          </div>
        </div>

        <div className="flex gap-3 flex-row-reverse">
          <div className="w-8 h-8 rounded-full bg-blue-500/10 flex items-center justify-center shrink-0 border border-blue-500/20">
            <User className="w-4 h-4 text-blue-400" />
          </div>
          <div className="flex flex-col gap-1 items-end">
            <span className="text-xs font-medium text-muted-foreground">You</span>
            <div className="text-sm text-white leading-relaxed bg-blue-600 p-3 rounded-2xl rounded-tr-sm shadow-sm">
              How do I fix the timing leak?
            </div>
          </div>
        </div>

        <div className="flex gap-3">
          <div className="w-8 h-8 rounded-full bg-emerald-500/10 flex items-center justify-center shrink-0 border border-emerald-500/20">
            <Bot className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="flex flex-col gap-1 w-full max-w-[85%]">
            <span className="text-xs font-medium text-muted-foreground">Nemotron</span>
            <div className="text-sm text-foreground leading-relaxed bg-muted/30 p-3 rounded-2xl rounded-tl-sm space-y-3">
              <p>You should use a constant-time comparison function instead of the standard <code className="bg-background px-1 py-0.5 rounded text-xs border border-border">==</code> operator.</p>
              <div className="bg-background p-2 rounded border border-border">
                <p className="text-xs font-mono text-muted-foreground mb-1 block">Solution:</p>
                <code className="text-xs font-mono text-green-400">import hmac<br/>hmac.compare_digest(user_input, secret)</code>
              </div>
              <p>I've cited the finding here: <span className="inline-flex items-center rounded-full bg-accent px-2 py-0.5 text-xs font-medium text-accent-foreground cursor-pointer hover:bg-accent/80 transition-colors">src/auth.py:42</span></p>
            </div>
          </div>
        </div>

      </div>

      {/* Input */}
      <div className="p-4 bg-background shrink-0 border-t border-border mt-auto">
        <div className="relative flex items-center">
          <input 
            type="text" 
            placeholder="Ask Nemotron about the findings..." 
            className="w-full bg-muted border border-border rounded-full py-2.5 pl-4 pr-10 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500/50 focus:border-emerald-500/50 transition-all text-foreground placeholder:text-muted-foreground"
          />
          <button className="absolute right-1 w-8 h-8 flex items-center justify-center rounded-full hover:bg-background transition-colors text-muted-foreground hover:text-emerald-400">
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
