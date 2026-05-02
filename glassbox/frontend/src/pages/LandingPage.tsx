import { ShieldAlert, ArrowRight, Cpu, Code2 } from "lucide-react"
import { useNavigate } from "react-router-dom"

export function LandingPage() {
  const navigate = useNavigate()

  return (
    <div className="min-h-screen w-full bg-background text-foreground flex flex-col relative overflow-hidden bg-[linear-gradient(to_right,#80808012_1px,transparent_1px),linear-gradient(to_bottom,#80808012_1px,transparent_1px)] bg-[size:24px_24px]">
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
          onClick={() => navigate('/initiate')}
          className="flex items-center gap-3 bg-emerald-500 text-black hover:bg-emerald-400 px-8 py-4 rounded-full font-bold transition-all shadow-[0_0_40px_rgba(16,185,129,0.3)] hover:shadow-[0_0_60px_rgba(16,185,129,0.5)] active:scale-95 group animate-in fade-in slide-in-from-bottom-10 duration-700 delay-300"
        >
          Start New Audit
          <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
        </button>

        {/* Visual Anchor (Abstract Dashboard Mockup) */}
        <div className="mt-16 w-full max-w-5xl mx-auto h-[300px] sm:h-[400px] rounded-t-xl bg-card/30 backdrop-blur-md border border-white/10 border-b-0 shadow-2xl relative overflow-hidden animate-in fade-in slide-in-from-bottom-12 duration-1000 delay-500 flex">
          <div className="absolute inset-0 bg-gradient-to-t from-background via-transparent to-transparent z-10" />
          {/* Fake Sidebar */}
          <div className="w-48 sm:w-64 border-r border-white/5 p-4 flex flex-col gap-3 opacity-50">
            <div className="w-full h-4 bg-white/10 rounded-md" />
            <div className="w-3/4 h-4 bg-white/10 rounded-md" />
            <div className="w-5/6 h-4 bg-white/10 rounded-md" />
            <div className="w-1/2 h-4 bg-white/5 rounded-md mt-4" />
          </div>
          {/* Fake Main Area */}
          <div className="flex-1 p-6 flex flex-col gap-4 opacity-70">
            <div className="w-1/3 h-6 bg-emerald-500/20 rounded-md mb-2" />
            <div className="w-full h-24 bg-white/5 rounded-lg border border-white/5" />
            <div className="w-full h-24 bg-white/5 rounded-lg border border-white/5" />
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
            <h3 className="font-semibold text-foreground">MCP Agent</h3>
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
