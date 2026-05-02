import { useState, useRef } from "react"
import { useParams, Link, useNavigate } from "react-router-dom"
import { FileTree } from "@/components/FileTree"
import { SnippetViewer } from "@/components/SnippetViewer"
import { ChatPanel } from "@/components/ChatPanel"
import { CheckCircle2, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, GripVertical } from "lucide-react"
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle, PanelImperativeHandle } from "react-resizable-panels"

export function AuditDashboard() {
  const { runId } = useParams()
  const navigate = useNavigate()
  
  const leftPanelRef = useRef<PanelImperativeHandle>(null)
  const rightPanelRef = useRef<PanelImperativeHandle>(null)
  const [isLeftCollapsed, setIsLeftCollapsed] = useState(false)
  const [isRightCollapsed, setIsRightCollapsed] = useState(false)

  const toggleLeft = () => {
    const panel = leftPanelRef.current
    if (panel) {
      if (panel.isCollapsed()) panel.expand()
      else panel.collapse()
    }
  }

  const toggleRight = () => {
    const panel = rightPanelRef.current
    if (panel) {
      if (panel.isCollapsed()) panel.expand()
      else panel.collapse()
    }
  }

  return (
    <div className="h-screen w-full bg-background text-foreground overflow-hidden font-sans">
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
              <span>Run:</span>
              <span className="font-mono text-emerald-500 truncate" title={runId}>{runId}</span>
            </p>
          </div>
          <div className="flex-1 overflow-y-auto">
            <FileTree />
          </div>
          <div className="p-4 border-t border-border shrink-0 bg-background/50 backdrop-blur-md">
            <button 
              onClick={() => navigate('/')}
              className="w-full py-2.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-md transition-all shadow-[0_0_15px_rgba(16,185,129,0.2)] hover:shadow-[0_0_25px_rgba(16,185,129,0.3)] flex items-center justify-center gap-2 text-sm font-medium active:scale-95"
            >
              <CheckCircle2 className="w-4 h-4" />
              Done
            </button>
          </div>
        </Panel>

        <PanelResizeHandle className="w-1 bg-border/50 hover:bg-emerald-500/50 transition-colors cursor-col-resize flex flex-col justify-center items-center group relative">
          <div className="absolute inset-y-0 -left-1 -right-1 z-10" />
          <GripVertical className="w-3 h-6 text-muted-foreground group-hover:text-emerald-400 opacity-0 group-hover:opacity-100 transition-opacity" />
        </PanelResizeHandle>

        {/* Column 2: Triage & Snippets */}
        <Panel defaultSize={55} minSize={30} className="flex flex-col min-w-0 bg-background relative shadow-inner">
          <div className="flex items-center justify-between p-2 border-b border-border bg-card/30 shrink-0">
            <button onClick={toggleLeft} className="p-1.5 hover:bg-muted rounded-md text-muted-foreground hover:text-foreground transition-colors" title="Toggle File Tree">
              {isLeftCollapsed ? <PanelLeftOpen className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
            </button>
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Triage Workspace</span>
            <button onClick={toggleRight} className="p-1.5 hover:bg-muted rounded-md text-muted-foreground hover:text-foreground transition-colors" title="Toggle Chat">
              {isRightCollapsed ? <PanelRightOpen className="w-4 h-4" /> : <PanelRightClose className="w-4 h-4" />}
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-6 lg:p-8">
            <SnippetViewer />
          </div>
        </Panel>

        <PanelResizeHandle className="w-1 bg-border/50 hover:bg-emerald-500/50 transition-colors cursor-col-resize flex flex-col justify-center items-center group relative">
          <div className="absolute inset-y-0 -left-1 -right-1 z-10" />
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
            <ChatPanel />
          </div>
        </Panel>
      </PanelGroup>
    </div>
  )
}
