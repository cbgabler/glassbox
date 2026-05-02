import { AlertTriangle, Cpu, TerminalSquare } from "lucide-react"

export function SnippetViewer() {
  return (
    <div className="flex flex-col gap-6 max-w-4xl mx-auto w-full animate-in fade-in slide-in-from-bottom-4 duration-500">
      
      {/* Finding Header */}
      <div className="flex flex-col gap-4 bg-card border border-border rounded-xl p-6 shadow-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-red-500/10 rounded-lg">
              <AlertTriangle className="w-6 h-6 text-red-500" />
            </div>
            <div>
              <h2 className="text-xl font-semibold text-foreground tracking-tight">Timing Leak in Password Comparison</h2>
              <p className="text-sm text-muted-foreground flex items-center gap-2 mt-1">
                <span className="inline-flex items-center rounded-full bg-red-500/10 px-2 py-0.5 text-xs font-medium text-red-500 ring-1 ring-inset ring-red-500/20">CRITICAL</span>
                <span>•</span>
                <span>src/auth.py:42</span>
                <span>•</span>
                <span className="font-mono text-xs">SideChannelScanner</span>
              </p>
            </div>
          </div>
          
          {/* Hardware Confirm Button */}
          <button className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-md font-medium transition-all shadow-lg shadow-indigo-500/20 active:scale-95 group">
            <Cpu className="w-4 h-4 group-hover:rotate-12 transition-transform" />
            Confirm on hardware
          </button>
        </div>
        
        <p className="text-sm text-foreground/80 leading-relaxed mt-2">
          The function uses a naive byte-by-byte comparison for a secret token. This creates an early-exit timing leak where the time taken correlates with the number of correctly guessed prefix bytes.
        </p>
      </div>

      {/* Code Snippet Editor Mock */}
      <div className="rounded-xl overflow-hidden border border-border bg-[#0d1117] shadow-xl">
        <div className="flex items-center px-4 py-2 border-b border-border bg-[#161b22]">
          <TerminalSquare className="w-4 h-4 text-muted-foreground mr-2" />
          <span className="text-xs font-mono text-muted-foreground">src/auth.py</span>
        </div>
        <div className="p-4 overflow-x-auto">
          <pre className="font-mono text-sm leading-relaxed text-slate-300">
            <code>
              <span className="text-slate-500 mr-4 select-none">40 |</span> <span className="text-purple-400">def</span> <span className="text-blue-400">check_password</span>(user_input: <span className="text-teal-400">str</span>, secret: <span className="text-teal-400">str</span>) -{'>'} <span className="text-teal-400">bool</span>:<br/>
              <span className="text-slate-500 mr-4 select-none">41 |</span>     <span className="text-slate-500 italic"># WARNING: VULNERABLE</span><br/>
              <span className="bg-red-500/20 text-red-200 block px-2 -mx-2 rounded-sm border-l-2 border-red-500"><span className="text-slate-500 mr-4 select-none opacity-50">42 |</span>     <span className="text-purple-400">if</span> user_input == secret:</span>
              <span className="text-slate-500 mr-4 select-none">43 |</span>         <span className="text-purple-400">return</span> <span className="text-orange-400">True</span><br/>
              <span className="text-slate-500 mr-4 select-none">44 |</span>     <span className="text-purple-400">return</span> <span className="text-orange-400">False</span>
            </code>
          </pre>
        </div>
      </div>

    </div>
  )
}
