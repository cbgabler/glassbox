import { useState, useEffect } from "react"
import { useParams } from "react-router-dom"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { fetchSnippets } from "@/lib/api"
import { Loader2, Cpu } from "lucide-react"

export function SnippetViewer() {
  const { runId } = useParams()
  const [markdown, setMarkdown] = useState<string | null>(null)

  useEffect(() => {
    if (runId) {
      fetchSnippets(runId).then(setMarkdown)
    }
  }, [runId])

  if (!markdown) {
    return (
      <div className="flex justify-center items-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6 max-w-4xl mx-auto w-full animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="bg-card border border-border rounded-xl p-6 shadow-sm overflow-x-auto relative">
        <button className="absolute top-6 right-6 flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded-md text-xs font-medium transition-all shadow-lg shadow-indigo-500/20 active:scale-95 group z-10">
          <Cpu className="w-3.5 h-3.5 group-hover:rotate-12 transition-transform" />
          Confirm on hardware
        </button>
        <div className="prose prose-invert max-w-none prose-pre:bg-[#0d1117] prose-pre:border prose-pre:border-border prose-headings:tracking-tight prose-a:text-indigo-400">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {markdown}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  )
}
