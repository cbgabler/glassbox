import { useParams } from "react-router-dom"
import { FileTree } from "@/components/FileTree"
import { SnippetViewer } from "@/components/SnippetViewer"
import { ChatPanel } from "@/components/ChatPanel"
import { PodPanel } from "@/components/PodPanel"

export function AuditDashboard() {
  const { runId } = useParams()
  
  return (
    <div className="h-screen w-full flex bg-background text-foreground overflow-hidden font-sans">
      {/* Column 1: Repository Structure */}
      <aside className="w-64 border-r border-border bg-card/50 flex flex-col">
        <div className="p-4 border-b border-border">
          <h1 className="font-semibold text-lg tracking-tight text-primary">GlassBox</h1>
          <p className="text-xs text-muted-foreground mt-1">Run: <span className="font-mono text-emerald-500">{runId}</span></p>
        </div>
        <div className="flex-1 overflow-y-auto">
          <FileTree />
        </div>
      </aside>

      {/* Column 2: Triage & Snippets */}
      <main className="flex-1 flex flex-col min-w-0 bg-background relative shadow-inner">
        <div className="flex-1 overflow-y-auto p-6 lg:p-8">
          <SnippetViewer />
        </div>
      </main>

      {/* Column 3: Chat & Pod Control */}
      <aside className="w-80 xl:w-96 border-l border-border bg-card/50 flex flex-col shadow-xl z-10">
        <div className="flex-1 overflow-y-auto flex flex-col">
          <ChatPanel />
        </div>
        <div className="p-4 border-t border-border shrink-0 bg-background/50 backdrop-blur-md">
          <PodPanel />
        </div>
      </aside>
    </div>
  )
}
