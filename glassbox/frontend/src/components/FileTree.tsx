import { useState, useEffect } from "react"
import { useParams } from "react-router-dom"
import { fetchRepoStructure, FileNode } from "@/lib/api"
import { Loader2, ChevronRight, ChevronDown, FileText, Folder, AlertCircle, AlertTriangle } from "lucide-react"

function TreeNode({ node, depth = 0 }: { node: FileNode; depth?: number }) {
  const [isOpen, setIsOpen] = useState(true)
  const isFolder = node.type === "folder"

  return (
    <div className="flex flex-col">
      <div 
        className={`flex items-center gap-1.5 py-1.5 px-2 hover:bg-white/5 rounded-md cursor-pointer text-sm transition-colors ${
          node.status === 'vulnerable' ? 'text-red-400 bg-red-500/5' : 
          node.status === 'warning' ? 'text-amber-400 bg-amber-500/5' : 
          'text-foreground/80 hover:text-foreground'
        }`}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
        onClick={() => isFolder && setIsOpen(!isOpen)}
      >
        <div className="w-4 h-4 flex items-center justify-center shrink-0">
          {isFolder ? (
            isOpen ? <ChevronDown className="w-3 h-3 text-muted-foreground" /> : <ChevronRight className="w-3 h-3 text-muted-foreground" />
          ) : (
            <div className="w-3" />
          )}
        </div>
        
        {isFolder ? <Folder className="w-4 h-4 text-blue-400 shrink-0 fill-blue-400/20" /> : <FileText className="w-4 h-4 text-emerald-400 shrink-0" />}
        
        <span className="truncate font-medium">{node.name}</span>
        
        {node.status === 'vulnerable' && <AlertCircle className="w-3.5 h-3.5 text-red-500 shrink-0 ml-auto" />}
        {node.status === 'warning' && <AlertTriangle className="w-3.5 h-3.5 text-amber-500 shrink-0 ml-auto" />}
      </div>
      
      {node.vulnDescription && (
        <div className="text-[10px] text-muted-foreground px-2 py-0.5 ml-8 border-l border-red-500/30 font-mono">
          {node.vulnDescription}
        </div>
      )}

      {isFolder && isOpen && node.children && (
        <div className="flex flex-col mt-0.5">
          {node.children.map((child, i) => (
            <TreeNode key={i} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}

export function FileTree() {
  const { runId } = useParams()
  const [nodes, setNodes] = useState<FileNode[] | null>(null)

  useEffect(() => {
    if (runId) {
      fetchRepoStructure(runId).then(setNodes)
    }
  }, [runId])

  if (!nodes) {
    return (
      <div className="flex justify-center items-center h-48">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="p-3 overflow-y-auto flex flex-col gap-0.5">
      {nodes.map((node, i) => (
        <TreeNode key={i} node={node} />
      ))}
    </div>
  )
}
