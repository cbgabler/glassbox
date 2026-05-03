import { useState, useEffect } from "react"
import { useParams } from "react-router-dom"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { fetchSnippets } from "@/lib/api"
import { Loader2 } from "lucide-react"
import { motion } from "framer-motion"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism"

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
    <motion.div 
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: "easeOut" }}
      className="flex flex-col gap-6 max-w-4xl mx-auto w-full"
    >
      <div className="bg-card/40 backdrop-blur-xl border border-white/5 rounded-xl p-8 shadow-2xl overflow-x-auto relative ring-1 ring-white/5">
        {/* Subtle top glare */}
        <div className="absolute top-0 inset-x-0 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
        
        <div className="prose prose-invert max-w-none prose-headings:tracking-tight prose-headings:font-semibold prose-h1:text-3xl prose-h1:text-emerald-400 prose-a:text-indigo-400 prose-p:text-muted-foreground prose-strong:text-foreground">
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
                    className="rounded-lg border border-white/10 !bg-[#000000] !p-4 !my-6 text-[13px] font-mono shadow-inner"
                  />
                ) : (
                  <code {...props} className={`${className} bg-white/5 border border-white/10 px-1.5 py-0.5 rounded-md text-emerald-300 font-mono text-[0.85em]`}>
                    {children}
                  </code>
                )
              }
            }}
          >
            {markdown}
          </ReactMarkdown>
        </div>
      </div>
    </motion.div>
  )
}
