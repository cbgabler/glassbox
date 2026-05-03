import { useState, useRef, useEffect } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism"
import { useSearchParams, Link, useNavigate } from "react-router-dom"
import { FileTree } from "@/components/FileTree"
import { ChatPanel } from "@/components/ChatPanel"
import { sendChatPrompt, connectToolStream, parseAgentResponse, ParsedResponse, RepoTreeResponse, Message } from "@/lib/api"
import { CheckCircle2, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, GripVertical } from "lucide-react"
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle, PanelImperativeHandle } from "react-resizable-panels"

export function AuditDashboard() {
  const [searchParams] = useSearchParams()
  const repoUrl = decodeURIComponent(searchParams.get("repo") || "")
  const navigate = useNavigate()

  const [isGenerating, setIsGenerating]   = useState(false)
  const [clearTrigger, setClearTrigger]   = useState(0)
  const [initialParsed, setInitialParsed] = useState<ParsedResponse | undefined>()
  const [repoTrees, setRepoTrees]         = useState<RepoTreeResponse[]>([])
  const [codeBlocks, setCodeBlocks]       = useState<string[]>([])
  const [streamedMessages, setStreamedMessages] = useState<Message[]>([])
  const codeScrollRef = useRef<HTMLDivElement>(null)
  const wsCleanupRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    codeScrollRef.current?.scrollTo({
      top: codeScrollRef.current.scrollHeight,
      behavior: "smooth",
    })
  }, [codeBlocks, isGenerating])

  const handleRepoParsed = (parsed: ParsedResponse) => {
    setRepoTrees(prev => [...prev, ...parsed.repo])
  }
  const handleCodeParsed = (parsed: ParsedResponse) => {
    setCodeBlocks(prev => [...prev, ...parsed.code])
  }

  // Connect to WebSocket for streaming updates
  useEffect(() => {
    console.log("[Dashboard] Setting up WebSocket connection...")
    const cleanup = connectToolStream((msg: Message) => {
      console.log("[Dashboard] Received WebSocket message:", msg)
      setStreamedMessages(prev => [...prev, msg])
      
      // Parse the message and update repo/code blocks
      const parsed = parseAgentResponse(msg)
      console.log("[Dashboard] Parsed WebSocket message:", parsed)
      if (parsed.repo.length > 0) {
        console.log("[Dashboard] Got", parsed.repo.length, "repo blocks from stream")
        handleRepoParsed(parsed)
      }
      if (parsed.code.length > 0) {
        console.log("[Dashboard] Got", parsed.code.length, "code blocks from stream")
        handleCodeParsed(parsed)
      }
    })
    wsCleanupRef.current = cleanup
    console.log("[Dashboard] WebSocket connected, cleanup function stored")
    
    return () => {
      console.log("[Dashboard] Cleaning up WebSocket...")
      wsCleanupRef.current?.()
    }
  }, [])

  useEffect(() => {
    if (!repoUrl) {
      navigate('/initiate')
      return
    }

    const run = async () => {
      setIsGenerating(true)
      try {
        const parsed = await sendChatPrompt(
          `Perform a comprehensive security audit of this repository: ${repoUrl}`
        )
        console.log("[Dashboard] parsed result:", parsed)
        console.log("[Dashboard] chat blocks:", parsed.chat)
        setInitialParsed(parsed)
        if (parsed.repo.length > 0) setRepoTrees(parsed.repo)
        if (parsed.code.length > 0) setCodeBlocks(parsed.code)
      } catch (err) {
        console.error("Analysis failed:", err)
      } finally {
        setIsGenerating(false)
      }
    }

    run()
  }, [repoUrl, navigate])

  const leftPanelRef  = useRef<PanelImperativeHandle>(null)
  const rightPanelRef = useRef<PanelImperativeHandle>(null)
  const [isLeftCollapsed,  setIsLeftCollapsed]  = useState(false)
  const [isRightCollapsed, setIsRightCollapsed] = useState(false)

  const toggleLeft = () => {
    const panel = leftPanelRef.current
    if (panel) panel.isCollapsed() ? panel.expand() : panel.collapse()
  }
  const toggleRight = () => {
    const panel = rightPanelRef.current
    if (panel) panel.isCollapsed() ? panel.expand() : panel.collapse()
  }

  const handleDone = async () => {
    try {
      const res = await fetch("http://localhost:8080/done", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      })
      if (!res.ok) throw new Error("Cleanup failed")
      console.log("Cleanup completed")
    } catch (err) {
      console.error("Cleanup error:", err)
    } finally {
      setClearTrigger(prev => prev + 1)
      navigate('/')
    }
  }

  return (
    <div className="h-screen w-full bg-background text-foreground overflow-hidden font-sans bg-[linear-gradient(to_right,#80808012_1px,transparent_1px),linear-gradient(to_bottom,#80808012_1px,transparent_1px)] bg-[size:24px_24px]">
      <PanelGroup orientation="horizontal">

        {/* Column 1: Repository Structure */}
        <Panel
          panelRef={leftPanelRef}
          defaultSize={20}
          minSize={15}
          collapsible={true}
          collapsedSize={0}
          onResize={() => {
            if (leftPanelRef.current) setIsLeftCollapsed(leftPanelRef.current.isCollapsed())
          }}
          className="flex flex-col bg-card/50"
        >
          <div className="p-4 border-b border-border">
            <Link to="/" className="font-semibold text-lg tracking-tight text-primary hover:text-emerald-400 transition-colors">GlassBox</Link>
            <p className="text-xs text-muted-foreground mt-1 flex items-center gap-1 truncate">
              <span>Repo:</span>
              <span className="font-mono text-emerald-500 truncate" title={repoUrl}>{repoUrl}</span>
            </p>
          </div>
          <div className="flex-1 overflow-y-auto">
            <FileTree repoTrees={repoTrees} isLoading={isGenerating && repoTrees.length === 0} />
          </div>
          <div className="p-4 border-t border-border shrink-0 bg-background/50 backdrop-blur-md">
            <button
              onClick={handleDone}
              className="w-full py-2.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-md transition-all shadow-[0_0_15px_rgba(16,185,129,0.2)] hover:shadow-[0_0_25px_rgba(16,185,129,0.3)] flex items-center justify-center gap-2 text-sm font-medium active:scale-95"
            >
              <CheckCircle2 className="w-4 h-4" />
              Done
            </button>
          </div>
        </Panel>

        <PanelResizeHandle className="w-1.5 bg-white/5 backdrop-blur-sm border-x border-white/5 hover:bg-emerald-500/20 hover:border-emerald-500/30 transition-all cursor-col-resize flex flex-col justify-center items-center group relative z-50 shadow-[0_0_10px_rgba(0,0,0,0.5)]">
          <div className="absolute inset-y-0 -left-2 -right-2 z-10" />
          <GripVertical className="w-3 h-6 text-muted-foreground group-hover:text-emerald-400 opacity-0 group-hover:opacity-100 transition-opacity" />
        </PanelResizeHandle>

        {/* Column 2: Triage Workspace */}
        <Panel defaultSize={55} minSize={30} className="flex flex-col min-w-0 bg-[#050505]/80 backdrop-blur-md relative shadow-inner border-x border-white/5">
          <div className="flex items-center justify-between p-2 border-b border-white/5 bg-background/50 backdrop-blur-md shrink-0 relative z-20">
            <button onClick={toggleLeft} className="p-1.5 hover:bg-white/10 rounded-md text-muted-foreground hover:text-foreground transition-colors" title="Toggle File Tree">
              {isLeftCollapsed ? <PanelLeftOpen className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
            </button>
            <div className="flex items-center gap-4">
              <span className="text-xs font-semibold text-foreground uppercase tracking-widest">Triage Workspace</span>
              <div className="hidden sm:flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-[10px] font-medium text-emerald-400 uppercase tracking-wider">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                HW Connected
              </div>
            </div>
            <button onClick={toggleRight} className="p-1.5 hover:bg-white/10 rounded-md text-muted-foreground hover:text-foreground transition-colors" title="Toggle Chat">
              {isRightCollapsed ? <PanelRightOpen className="w-4 h-4" /> : <PanelRightClose className="w-4 h-4" />}
            </button>
          </div>
          <div ref={codeScrollRef} className="flex-1 overflow-y-auto p-4 space-y-4">
            {codeBlocks.length === 0 ? (
              <div className="flex h-full min-h-48 items-center justify-center rounded-xl border border-dashed border-white/10 bg-white/5 text-sm text-muted-foreground">
                {isGenerating ? "Analyzing..." : "No findings yet."}
              </div>
            ) : (
              codeBlocks.map((snippet, index) => (
                <div
                  key={index}
                  className="rounded-xl border border-emerald-500/15 bg-black/60 p-5 shadow-inner prose prose-invert prose-sm max-w-none prose-pre:!p-0 prose-pre:!bg-transparent prose-pre:border-0"
                >
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
                            className="rounded-md border border-white/10 !bg-[#0a0a0a] !p-3 !my-2 text-[12px] font-mono shadow-inner"
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
                    {snippet}
                  </ReactMarkdown>
                </div>
              ))
            )}
          </div>
        </Panel>

        <PanelResizeHandle className="w-1.5 bg-white/5 backdrop-blur-sm border-x border-white/5 hover:bg-emerald-500/20 hover:border-emerald-500/30 transition-all cursor-col-resize flex flex-col justify-center items-center group relative z-50 shadow-[0_0_10px_rgba(0,0,0,0.5)]">
          <div className="absolute inset-y-0 -left-2 -right-2 z-10" />
          <GripVertical className="w-3 h-6 text-muted-foreground group-hover:text-emerald-400 opacity-0 group-hover:opacity-100 transition-opacity" />
        </PanelResizeHandle>

        {/* Column 3: Chat */}
        <Panel
          panelRef={rightPanelRef}
          defaultSize={25}
          minSize={20}
          collapsible={true}
          collapsedSize={0}
          onResize={() => {
            if (rightPanelRef.current) setIsRightCollapsed(rightPanelRef.current.isCollapsed())
          }}
          className="flex flex-col bg-card/50 shadow-xl z-10"
        >
          <div className="flex-1 overflow-y-auto flex flex-col h-full">
            <ChatPanel
              isGenerating={isGenerating}
              initialResponse={initialParsed}
              clearTrigger={clearTrigger}
              onRepoParsed={handleRepoParsed}
              onCodeParsed={handleCodeParsed}
              streamedMessages={streamedMessages}
            />
          </div>
        </Panel>

      </PanelGroup>
    </div>
  )
}