import { ShieldAlert, ArrowRight, Cpu, Code2 } from "lucide-react"
import { useNavigate } from "react-router-dom"
import { useState } from "react"

export function LandingPage() {
  const navigate = useNavigate()
  const [isNavigating, setIsNavigating] = useState(false)

  const handleStart = () => {
    setIsNavigating(true)
    setTimeout(() => {
      navigate('/initiate')
    }, 600)
  }

  return (
    <div className={`min-h-screen w-full bg-background text-foreground flex flex-col relative overflow-hidden bg-[linear-gradient(to_right,#80808012_1px,transparent_1px),linear-gradient(to_bottom,#80808012_1px,transparent_1px)] bg-[size:24px_24px] transition-all duration-700 ${isNavigating ? 'opacity-0 scale-[0.98] filter blur-md' : 'opacity-100 scale-100 filter-none'}`}>
      {/* Decorative gradient blob */}
      <div className="absolute top-[-10%] right-[-5%] w-[600px] h-[600px] bg-emerald-500/10 rounded-full blur-[150px] pointer-events-none" />
      
      {/* Navbar */}
      <header className="flex items-center justify-between px-8 py-6 max-w-7xl mx-auto w-full relative z-10">
        <div className="flex items-center gap-2 font-bold text-xl tracking-tight text-foreground">
          <ShieldAlert className="w-6 h-6 text-emerald-500" />
          GlassBox
        </div>
      </header>

      {/* Hero Section */}
      <main className="flex-1 flex flex-col items-center justify-center px-6 pb-20 max-w-4xl mx-auto text-center relative z-10">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-emerald-500/10 text-emerald-400 text-xs font-semibold uppercase tracking-widest border border-emerald-500/20 mb-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
          <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          Hardware-in-the-loop Security
        </div>
        
        <h1 className="text-5xl sm:text-6xl font-extrabold tracking-tight mb-6 leading-[1.1] animate-in fade-in slide-in-from-bottom-6 duration-700">
          The auditor that proves <br />
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-indigo-400">
            leaks on real silicon.
          </span>
        </h1>
        
        <p className="text-lg text-muted-foreground mb-10 max-w-2xl leading-relaxed animate-in fade-in slide-in-from-bottom-8 duration-700 delay-150">
          GlassBox is a local-first vulnerability auditor. Point it at any repo to scan for side-channel leaks, exposed endpoints, and git history secrets. Then, confirm them deterministically on real hardware.
        </p>

        <button 
          onClick={handleStart}
          className="flex items-center gap-3 bg-emerald-500 text-black hover:bg-emerald-400 px-8 py-4 rounded-full font-bold transition-all shadow-[0_0_40px_rgba(16,185,129,0.3)] hover:shadow-[0_0_60px_rgba(16,185,129,0.5)] active:scale-95 group animate-in fade-in slide-in-from-bottom-10 duration-700 delay-300"
        >
          Start New Audit
          <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
        </button>

        {/* Visual Anchor (High-Fidelity Dashboard Mockup) */}
        <div className="mt-20 w-full max-w-6xl mx-auto rounded-t-2xl bg-[#0a0a0a] border border-white/10 border-b-0 shadow-[0_-20px_80px_rgba(16,185,129,0.15)] relative overflow-hidden animate-in fade-in slide-in-from-bottom-12 duration-1000 delay-500 flex flex-col ring-1 ring-white/5">
          {/* macOS Title Bar */}
          <div className="w-full h-12 border-b border-white/10 flex items-center px-4 bg-[#0f0f11] shrink-0">
            <div className="flex gap-2">
              <div className="w-3 h-3 rounded-full bg-red-500/80" />
              <div className="w-3 h-3 rounded-full bg-amber-500/80" />
              <div className="w-3 h-3 rounded-full bg-green-500/80" />
            </div>
            <div className="mx-auto flex items-center gap-2 text-xs font-mono text-muted-foreground bg-white/5 px-3 py-1 rounded-md border border-white/5">
              <ShieldAlert className="w-3 h-3 text-emerald-500" />
              GlassBox Triage
            </div>
          </div>
          
          <div className="flex h-[350px] sm:h-[450px]">
            {/* Column 1: File Tree Mock */}
            <div className="hidden md:flex w-56 border-r border-white/5 p-4 flex-col gap-1 bg-[#0a0a0a]/50">
              <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Repository</div>
              <div className="flex items-center gap-2 text-sm text-foreground/80 py-1"><span className="text-blue-400">📁</span> src</div>
              <div className="flex items-center gap-2 text-sm text-foreground/80 py-1 pl-4"><span className="text-emerald-400">📄</span> main.py</div>
              <div className="flex items-center gap-2 text-sm text-emerald-400 font-medium py-1 pl-4 bg-emerald-500/10 border border-emerald-500/20 rounded-md shadow-sm"><span className="text-emerald-400">📄</span> auth.py</div>
              <div className="flex items-center gap-2 text-sm text-foreground/80 py-1 pl-4"><span className="text-emerald-400">📄</span> utils.py</div>
            </div>

            {/* Column 2: Snippet Viewer Mock */}
            <div className="flex-1 flex flex-col bg-[#050505] relative border-r border-white/5">
              <div className="absolute top-0 inset-x-0 h-px bg-gradient-to-r from-transparent via-emerald-500/20 to-transparent" />
              
              <div className="flex h-10 border-b border-white/5 items-center justify-between px-4">
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Triage Workspace</span>
              </div>

              <div className="p-6 font-sans text-sm leading-loose text-muted-foreground overflow-hidden relative">
                <h2 className="text-xl font-bold text-emerald-400 tracking-tight mb-2">Timing Leak in Password Comparison</h2>
                <div className="flex gap-2 mb-4">
                  <span className="bg-red-500/10 text-red-400 border border-red-500/20 px-2 py-0.5 rounded text-xs font-mono">CRITICAL</span>
                  <span className="bg-white/5 text-muted-foreground border border-white/10 px-2 py-0.5 rounded text-xs font-mono">src/auth.py:42</span>
                </div>
                <p className="mb-4">The function uses a naive byte-by-byte comparison for a secret token. This creates an early-exit timing leak.</p>
                
                <div className="bg-[#000000] border border-white/10 rounded-lg p-4 font-mono text-[13px]">
                  <div><span className="text-indigo-400">def</span> <span className="text-blue-300">check_password</span>(user_input: <span className="text-emerald-300">str</span>, secret: <span className="text-emerald-300">str</span>) -&gt; <span className="text-emerald-300">bool</span>:</div>
                  <div className="pl-4 text-foreground/40"># WARNING: VULNERABLE</div>
                  <div className="pl-4 border-l-2 border-red-500 bg-red-500/10 -ml-4 pl-7 shadow-[inset_0_0_20px_rgba(239,68,68,0.05)] py-1">
                    <span className="text-indigo-400">if</span> user_input == secret:
                  </div>
                  <div className="pl-8 text-indigo-400">return <span className="text-blue-400">True</span></div>
                  <div className="pl-4 text-indigo-400">return <span className="text-blue-400">False</span></div>
                </div>
                
                <div className="absolute bottom-0 inset-x-0 h-32 bg-gradient-to-t from-[#050505] to-transparent pointer-events-none" />
              </div>
            </div>

            {/* Column 3: Chat Panel Mock */}
            <div className="hidden lg:flex w-72 flex-col bg-[#0a0a0a]/50 relative">
               <div className="flex h-10 border-b border-white/5 items-center px-4 gap-2">
                 <ShieldAlert className="w-4 h-4 text-emerald-400" />
                 <span className="text-xs font-semibold text-foreground tracking-tight">Glassbox Agent</span>
               </div>
               
               <div className="flex-1 p-4 flex flex-col gap-4 overflow-hidden relative">
                 {/* Bot message */}
                 <div className="flex gap-2">
                
                 </div>

                 {/* User message */}
                 <div className="flex gap-2 flex-row-reverse mt-2">
                   <div className="w-6 h-6 rounded-full bg-blue-500/10 border border-blue-500/20 text-blue-400 flex items-center justify-center shrink-0">
                     <Code2 className="w-3 h-3" />
                   </div>
                   <div className="bg-blue-600 border border-blue-500 text-white rounded-2xl rounded-tr-sm p-3 text-xs shadow-sm">
                     How do I fix the timing leak in auth.py?
                   </div>
                 </div>

                 {/* Bot typing */}
                 <div className="flex gap-2 mt-2">
                   <div className="w-6 h-6 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 flex items-center justify-center shrink-0">
                     <div className="w-1.5 h-1.5 bg-emerald-400 rounded-full animate-pulse" />
                   </div>
                 </div>
               </div>
               
               <div className="p-3 border-t border-white/5 bg-background">
                 <div className="w-full h-8 bg-white/5 border border-white/10 rounded-full" />
               </div>
            </div>
          </div>
        </div>

        {/* Features grid */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-6 mt-16 text-left w-full animate-in fade-in duration-1000 delay-700 relative z-20">
          <div className="flex flex-col gap-3 p-6 rounded-xl bg-card/40 border border-white/5 hover:-translate-y-1 hover:border-indigo-500/30 hover:bg-card/60 transition-all duration-300 group cursor-default">
            <Code2 className="w-5 h-5 text-indigo-400 group-hover:scale-110 transition-transform" />
            <h3 className="font-semibold text-foreground">Static Analysis</h3>
            <p className="text-sm text-muted-foreground">Finds timing leaks, plaintext keys, and exposed endpoints in milliseconds.</p>
          </div>
          <div className="flex flex-col gap-3 p-6 rounded-xl bg-card/40 border border-white/5 hover:-translate-y-1 hover:border-emerald-500/30 hover:bg-card/60 transition-all duration-300 group cursor-default">
            <Cpu className="w-5 h-5 text-emerald-400 group-hover:scale-110 transition-transform" />
            <h3 className="font-semibold text-foreground">Hardware Confirmation</h3>
            <p className="text-sm text-muted-foreground">Extracts C/C++ side-channels and runs TVLA on an ESP32 to prove exploitability.</p>
          </div>
          <div className="flex flex-col gap-3 p-6 rounded-xl bg-card/40 border border-white/5 hover:-translate-y-1 hover:border-amber-500/30 hover:bg-card/60 transition-all duration-300 group cursor-default">
            <ShieldAlert className="w-5 h-5 text-amber-400 group-hover:scale-110 transition-transform" />
            <h3 className="font-semibold text-foreground">Glassbox Agent</h3>
            <p className="text-sm text-muted-foreground">Ask questions, request patches, and control the pod via the built-in AI agent.</p>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="w-full border-t border-border/50 py-8 px-8 text-center text-muted-foreground text-sm mt-auto z-10 relative bg-background/80 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto flex flex-col sm:flex-row justify-between items-center gap-4">
          <p>© 2026 GlassBox Security. All rights reserved.</p>
        </div>
      </footer>
    </div>
  )
}
