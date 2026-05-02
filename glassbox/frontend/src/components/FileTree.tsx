import { useState, useEffect } from "react"
import { useParams } from "react-router-dom"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { fetchRepoStructure } from "@/lib/api"
import { Loader2 } from "lucide-react"

export function FileTree() {
  const { runId } = useParams()
  const [markdown, setMarkdown] = useState<string | null>(null)

  useEffect(() => {
    if (runId) {
      fetchRepoStructure(runId).then(setMarkdown)
    }
  }, [runId])

  if (!markdown) {
    return (
      <div className="flex justify-center items-center h-48">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="p-4 overflow-y-auto prose prose-invert prose-sm max-w-none prose-a:text-emerald-400 hover:prose-a:text-emerald-300">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {markdown}
      </ReactMarkdown>
    </div>
  )
}
