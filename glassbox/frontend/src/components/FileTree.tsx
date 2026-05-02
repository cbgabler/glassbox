import { FileIcon, FolderIcon, AlertCircle } from "lucide-react"
import { cn } from "@/lib/utils"

// Mock data to visualize the Tree Structure
const mockTree = [
  {
    name: "src",
    type: "folder",
    children: [
      { name: "main.py", type: "file", hasIssue: false },
      { name: "auth.py", type: "file", hasIssue: true, severity: "CRITICAL" },
      { name: "utils.py", type: "file", hasIssue: false },
    ],
  },
  {
    name: "package.json",
    type: "file",
    hasIssue: true,
    severity: "HIGH",
  },
  {
    name: ".env",
    type: "file",
    hasIssue: true,
    severity: "HIGH",
  },
]

export function FileTree() {
  return (
    <div className="p-4 flex flex-col gap-1 text-sm">
      {mockTree.map((node, i) => (
        <TreeNode key={i} node={node} depth={0} />
      ))}
    </div>
  )
}

function TreeNode({ node, depth }: { node: any; depth: number }) {
  const isFolder = node.type === "folder"
  const paddingLeft = `${depth * 1.5}rem`

  return (
    <div className="flex flex-col">
      <div
        className={cn(
          "flex items-center gap-2 py-1.5 px-2 rounded-md hover:bg-muted/50 cursor-pointer transition-colors group",
          node.hasIssue && "bg-destructive/10 hover:bg-destructive/20"
        )}
        style={{ paddingLeft: depth === 0 ? "0.5rem" : paddingLeft }}
      >
        {isFolder ? (
          <FolderIcon className="w-4 h-4 text-blue-400 opacity-80 group-hover:opacity-100" />
        ) : (
          <FileIcon className="w-4 h-4 text-muted-foreground opacity-80 group-hover:opacity-100" />
        )}
        <span className="flex-1 truncate select-none text-muted-foreground group-hover:text-foreground transition-colors">
          {node.name}
        </span>
        {node.hasIssue && (
          <AlertCircle className={cn(
            "w-3.5 h-3.5",
            node.severity === "CRITICAL" ? "text-red-500" : "text-amber-500"
          )} />
        )}
      </div>
      {isFolder && node.children && (
        <div className="flex flex-col">
          {node.children.map((child: any, i: number) => (
            <TreeNode key={i} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}
