import { ShieldAlert, ArrowRight, Cpu, Code2 } from "lucide-react"
import { useNavigate } from "react-router-dom"

export function LandingPage() {
  const navigate = useNavigate()

  return (
    <div className="min-h-screen w-full bg-background text-foreground flex flex-col relative overflow-hidden">
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
      <main className="flex-1 flex flex-col items-center justify-center px-6 max-w-4xl mx-auto text-center relative z-10">
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
          className="flex items-center gap-3 bg-foreground text-background hover:bg-foreground/90 px-8 py-4 rounded-full font-semibold transition-all shadow-[0_0_40px_rgba(255,255,255,0.1)] hover:shadow-[0_0_60px_rgba(255,255,255,0.15)] active:scale-95 group animate-in fade-in slide-in-from-bottom-10 duration-700 delay-300"
        >
          Start New Audit
          <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
        </button>

        {/* Features grid */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-6 mt-24 text-left w-full animate-in fade-in duration-1000 delay-500 border-t border-border/50 pt-12">
          <div className="flex flex-col gap-3">
            <Code2 className="w-5 h-5 text-indigo-400" />
            <h3 className="font-semibold text-foreground">Static Analysis</h3>
            <p className="text-sm text-muted-foreground">Finds timing leaks, plaintext keys, and exposed endpoints in milliseconds.</p>
          </div>
          <div className="flex flex-col gap-3">
            <Cpu className="w-5 h-5 text-emerald-400" />
            <h3 className="font-semibold text-foreground">Hardware Confirmation</h3>
            <p className="text-sm text-muted-foreground">Extracts C/C++ side-channels and runs TVLA on an ESP32 to prove exploitability.</p>
          </div>
          <div className="flex flex-col gap-3">
            <ShieldAlert className="w-5 h-5 text-amber-400" />
            <h3 className="font-semibold text-foreground">Nemotron Agent</h3>
            <p className="text-sm text-muted-foreground">Ask questions, request patches, and control the pod via the built-in AI agent.</p>
          </div>
        </div>
      </main>
    </div>
  )
}
